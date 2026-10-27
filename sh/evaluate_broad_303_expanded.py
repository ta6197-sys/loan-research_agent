"""Combine two already verified main runs with isolated cohort runs, by scope.

Run only after the fixed batch has recorded all targets. No network/model calls.
"""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.finance.analytics_dataset_service import AnalyticsDatasetService
from agent.finance.model_readiness import evaluate as model_readiness
from agent.finance.peer_dataset import build_snapshot
from agent.finance.statistical_analysis import analyze as statistical_analyze
from agent.finance.verified_run_peer import build_run
from agent.research_store import ResearchStore

PLAN=ROOT/'doc/finance/BROAD_303_COLLECTION_PLAN_20261027.json'
PROGRESS=ROOT/'data/research/broad_303_20261027/progress.json'
CANDIDATES=ROOT/'doc/finance/BROAD_303_CANDIDATES_20261027.json'
INITIAL=(('00157539','6bc0f8acb8d044e0a646862d4da77575'),
         ('00305668','5f3cd67dcc6e45b8874d04bba0ce804d'))


def immutable_write(path,payload):
    content=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n'
    if path.exists() and path.read_text()!=content:
        raise ValueError('immutable_expanded_snapshot_conflict:'+path.name)
    path.write_text(content)


def main():
    plan=json.loads(PLAN.read_text());progress=json.loads(PROGRESS.read_text())
    expected={item['corp_code'] for item in plan['targets']}
    if set(progress['results'])!=expected:
        raise ValueError('fixed_collection_plan_not_complete')
    candidates={row['corp_code']:row for row in json.loads(CANDIDATES.read_text())['records']}
    main_store=ResearchStore(ROOT/'session.db',ROOT/'data/research')
    isolated=ROOT/'data/research/broad_303_20261027'
    cohort_store=ResearchStore(isolated/'session.db',isolated/'sources')
    records=[];review=[]
    for code,run_id in INITIAL:
        result=build_run(main_store,run_id,candidates[code]);records.extend(result['records'])
        review.append({'corp_code':code,'run_id':run_id,'source_store':'main',
                       'eligible_observations':len(result['records']),'exclusions':result['excluded']})
    for target in plan['targets']:
        code=target['corp_code'];item=progress['results'][code]
        if item.get('cohort_status')!='exploratory_verified_candidate':
            review.append({'corp_code':code,'run_id':item.get('run_id'),'source_store':'isolated',
                           'eligible_observations':0,'exclusions':[{'reason':item.get('reason') or item.get('collection_next_phase')}],
                           'external_requests':item.get('external_requests')})
            continue
        result=build_run(cohort_store,item['run_id'],candidates[code]);records.extend(result['records'])
        review.append({'corp_code':code,'run_id':item['run_id'],'source_store':'isolated',
                       'eligible_observations':len(result['records']),'exclusions':result['excluded'],
                       'external_requests':item.get('external_requests')})
    scopes={}
    for scope in ('OFS','CFS'):
        snapshot=build_snapshot([item for item in records if item['scope']==scope])
        if snapshot['excluded_records']:
            raise ValueError('unexpected_peer_snapshot_exclusions')
        readiness=model_readiness(snapshot)
        observations=AnalyticsDatasetService._observations(snapshot)
        statistics={code:statistical_analyze(code,observations,analysis_as_of=plan['public_cutoff'])
                    for code in sorted({r['corp_code'] for r in snapshot['rows']})}
        measured=sum(p['status']=='measured' for result in statistics.values()
                     for p in result['peer_comparisons'])
        snapshot_path=ROOT/f'doc/finance/BROAD_303_EXPANDED_{scope}_20261027.json'
        immutable_write(snapshot_path,snapshot)
        scopes[scope]={'snapshot_file':snapshot_path.name,'snapshot_id':snapshot['snapshot_id'],
                       'row_count':snapshot['row_count'],
                       'distinct_companies':len({r['corp_code'] for r in snapshot['rows']}),
                       'observed_subcodes':{code:len({r['corp_code'] for r in snapshot['rows']
                           if r.get('industry_observed_code')==code}) for code in sorted({
                               r.get('industry_observed_code') for r in snapshot['rows']})},
                       'annual_row_count':readiness['annual_row_count'],
                       'training_status':readiness['training_status'],
                       'industry_revision_gate':readiness['checks']['industry_revision'],
                       'measured_peer_comparisons':measured,
                       'statistics_status_counts':{status:sum(p['status']==status for result in statistics.values()
                           for p in result['peer_comparisons']) for status in ('measured','insufficient_peer_data')}}
    # Count failed and retried runs as well as successful ones. Progress rows
    # may not contain usage when validation raises after collection.
    all_usage=[cohort_store.get_research(row['id'])['cumulative_usage']
               for row in cohort_store.list_researches()]
    summary={'schema_version':'broad-303-expanded-evaluation-1.0',
             'public_cutoff':plan['public_cutoff'],'plan_file':PLAN.name,
             'candidate_file':CANDIDATES.name,'classification_revision':'unconfirmed',
             'not_ksic30392_snapshot':True,'company_reviews':review,'scopes':scopes,
             'external_requests_total':sum(item['external_requests'] for item in all_usage),
             'model_calls_total':sum(item['model_calls'] for item in all_usage),
             'limitations':['이 표본은 24개 후보의 고정 개발 표본이며 303 산업 전체의 대표 표본이 아닙니다.',
                'XBRL 자체 업종코드의 KSIC 개정 차수가 미확인되어 ML 학습은 차단합니다.',
                '통계적 특이성은 회계 오류·신용위험 또는 대출 판단이 아닙니다.']}
    audit_path=ROOT/'doc/finance/BROAD_303_EXPANDED_AUDIT_20261027.json'
    if audit_path.exists():
        historical=json.loads(audit_path.read_text())
        if historical['scopes']!=scopes:
            raise ValueError('immutable_expanded_snapshot_conflict:new_revision_required')
        print(json.dumps({'status':'historical_snapshot_preserved','scope_summary':scopes,
                          'snapshot_external_requests':historical['external_requests_total'],
                          'current_isolated_external_requests':summary['external_requests_total']},
                         ensure_ascii=False,sort_keys=True))
    else:
        immutable_write(audit_path,summary)
        print(json.dumps({'status':'new_snapshot_audit','scope_summary':scopes,
                          'external_requests_total':summary['external_requests_total']},
                         ensure_ascii=False,sort_keys=True))


if __name__=='__main__':main()
