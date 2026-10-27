"""Create successor immutable exploratory snapshots after the fixed 12-company batch."""
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

OLD_AUDIT=ROOT/'doc/finance/BROAD_303_EXPANDED_AUDIT_20261027.json'
PLAN=ROOT/'doc/finance/BROAD_303_INCREMENT_PLAN_20261027.json'
CANDIDATES=ROOT/'doc/finance/BROAD_303_CANDIDATES_20261027.json'
DATA=ROOT/'data/research/broad_303_increment_20261027'
PROGRESS=DATA/'progress.json'


def immutable(path,payload):
    content=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n'
    if path.exists() and path.read_text()!=content:raise ValueError('immutable_increment_conflict:'+path.name)
    path.write_text(content)


def main():
    old=json.loads(OLD_AUDIT.read_text());plan=json.loads(PLAN.read_text())
    progress=json.loads(PROGRESS.read_text())
    if set(progress['results'])!={row['corp_code'] for row in plan['targets']}:
        raise ValueError('fixed_increment_plan_not_complete')
    candidates={item['corp_code']:item for item in json.loads(CANDIDATES.read_text())['records']}
    stores={'main':ResearchStore(ROOT/'session.db',ROOT/'data/research'),
            'isolated':ResearchStore(ROOT/'data/research/broad_303_20261027/session.db',
                                     ROOT/'data/research/broad_303_20261027/sources'),
            'increment':ResearchStore(DATA/'session.db',DATA/'sources')}
    records=[];reviews=[]
    for review in old['company_reviews']:
        if not review['eligible_observations']:
            reviews.append(review)
            continue
        result=build_run(stores[review['source_store']],review['run_id'],
                         candidates[review['corp_code']])
        records.extend(result['records']);reviews.append(review)
    for target in plan['targets']:
        code=target['corp_code'];item=progress['results'][code]
        if item.get('cohort_status')!='exploratory_verified_candidate':
            reviews.append({'corp_code':code,'source_store':'increment','run_id':item.get('run_id'),
                            'eligible_observations':0,'exclusions':[{'reason':item.get('reason') or
                                item.get('collection_next_phase') or 'verification_not_completed'}]})
            continue
        result=build_run(stores['increment'],item['run_id'],candidates[code])
        records.extend(result['records'])
        reviews.append({'corp_code':code,'source_store':'increment','run_id':item['run_id'],
                        'eligible_observations':len(result['records']),'exclusions':result['excluded']})
    scopes={}
    for scope in ('OFS','CFS'):
        snapshot=build_snapshot([item for item in records if item['scope']==scope])
        if snapshot['excluded_records']:raise ValueError('unexpected_snapshot_exclusion')
        readiness=model_readiness(snapshot)
        observations=AnalyticsDatasetService._observations(snapshot)
        statistics={code:statistical_analyze(code,observations,analysis_as_of=plan['public_cutoff'])
                    for code in sorted({row['corp_code'] for row in snapshot['rows']})}
        measured=sum(item['status']=='measured' for result in statistics.values()
                     for item in result['peer_comparisons'])
        path=ROOT/f'doc/finance/BROAD_303_EXPANDED_V2_{scope}_20261027.json'
        immutable(path,snapshot)
        scopes[scope]={'snapshot_file':path.name,'snapshot_id':snapshot['snapshot_id'],
                       'row_count':snapshot['row_count'],
                       'distinct_companies':len({row['corp_code'] for row in snapshot['rows']}),
                       'annual_row_count':readiness['annual_row_count'],
                       'training_status':readiness['training_status'],
                       'industry_revision_gate':readiness['checks']['industry_revision'],
                       'measured_peer_comparisons':measured,
                       'statistics_status_counts':{status:sum(item['status']==status
                           for result in statistics.values() for item in result['peer_comparisons'])
                           for status in ('measured','insufficient_peer_data')}}
    incremental_usage=[stores['increment'].get_research(row['id'])['cumulative_usage']
                       for row in stores['increment'].list_researches()]
    summary={'schema_version':'broad-303-expanded-v2-audit-1.0','public_cutoff':plan['public_cutoff'],
             'prior_snapshot_ids':{scope:old['scopes'][scope]['snapshot_id'] for scope in ('OFS','CFS')},
             'prior_audit':OLD_AUDIT.name,'increment_plan':PLAN.name,
             'classification_revision':'unconfirmed','not_ksic30392_snapshot':True,
             'company_reviews':reviews,'scopes':scopes,
             'new_eligible_companies':sum(item.get('cohort_status')=='exploratory_verified_candidate'
                                          for item in progress['results'].values()),
             'increment_external_requests':sum(row['external_requests'] for row in incremental_usage),
             'increment_model_calls':sum(row['model_calls'] for row in incremental_usage),
             'limitations':['새 스냅샷은 기존 불변 스냅샷의 후속 버전입니다.',
                            'XBRL 업종코드의 KSIC 개정 차수는 미확인입니다.',
                            '탐색 비교는 신용위험·회계오류·대출 판단이 아닙니다.']}
    immutable(ROOT/'doc/finance/BROAD_303_EXPANDED_V2_AUDIT_20261027.json',summary)
    print(json.dumps({'scope_summary':scopes,'new_eligible_companies':summary['new_eligible_companies'],
                      'increment_external_requests':summary['increment_external_requests']},
                     ensure_ascii=False,sort_keys=True))


if __name__=='__main__':main()
