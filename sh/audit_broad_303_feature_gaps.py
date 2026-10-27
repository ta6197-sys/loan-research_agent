"""Explain feature gaps in the immutable exploratory 303 snapshots, read-only."""
from collections import Counter, defaultdict
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.finance.peer_dataset import FEATURES
from agent.research_api import _run_verified_analysis_payload
from agent.research_store import ResearchStore

DATA=ROOT/'data/research/broad_303_20261027'
MAIN=ResearchStore(ROOT/'session.db',ROOT/'data/research')
ISOLATED=ResearchStore(DATA/'session.db',DATA/'sources')
INCREMENT=ResearchStore(ROOT/'data/research/broad_303_increment_20261027/session.db',ROOT/'data/research/broad_303_increment_20261027/sources')


def reason_for(metric,analysis,scope,end):
    if metric=='borrowing_to_assets':return 'feature_policy_candidate_not_implemented'
    matching=[item for item in analysis['financial_metrics']
              if item['name']==metric and item['scope']==scope and
              (item.get('period') or {}).get('end',(item.get('period') or {}).get('as_of'))==end]
    if matching:
        supported=[item for item in matching if item['status']=='supported']
        if supported:return 'supported_metric_not_in_snapshot'
        return matching[0].get('reason_code') or matching[0]['status']
    if metric in {'inventory_to_assets','receivables_to_assets'}:
        account='inventory' if metric=='inventory_to_assets' else 'receivables'
        rows=[item for item in analysis['financial_rows'] if item['scope']==scope and item['end']==end]
        current={item['account']:item for item in rows}
        if account not in current or 'assets' not in current:return 'required_account_absent'
        if any(current[name]['status']!='verified' for name in (account,'assets')):
            return 'required_account_not_verified'
        return 'ratio_denominator_or_trace_withheld'
    return 'comparison_period_not_emitted'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--snapshot-version',choices=('v1','v2'),default='v1')
    args=parser.parse_args()
    suffix='_V2' if args.snapshot_version=='v2' else ''
    audit=json.loads((ROOT/f'doc/finance/BROAD_303_EXPANDED{suffix}_AUDIT_20261027.json').read_text())
    snapshots={scope:json.loads((ROOT/f'doc/finance/BROAD_303_EXPANDED{suffix}_{scope}_20261027.json').read_text())
               for scope in ('OFS','CFS')}
    analyses={}
    for review in audit['company_reviews']:
        if not review['eligible_observations']:continue
        store={'main':MAIN,'isolated':ISOLATED,'increment':INCREMENT}[review['source_store']]
        analyses[review['corp_code']]=_run_verified_analysis_payload(store,review['run_id'])
    scopes={}
    for scope,snapshot in snapshots.items():
        reasons=defaultdict(Counter);by_period=defaultdict(lambda:defaultdict(set))
        for row in snapshot['rows']:
            company=row['corp_code'];end=row['period_end']
            for feature in FEATURES:
                if feature in row['features']:
                    by_period[end][feature].add(company)
                else:
                    reasons[feature][reason_for(feature,analyses[company],scope,end)]+=1
        scopes[scope]={'snapshot_id':snapshot['snapshot_id'],'row_count':snapshot['row_count'],
                       'features':{feature:{'available':snapshot['feature_missingness'][feature]['available_count'],
                                            'missing':snapshot['feature_missingness'][feature]['missing_count'],
                                            'missing_reasons':dict(sorted(reasons[feature].items()))}
                                   for feature in sorted(FEATURES)},
                       'distinct_companies_by_period':{end:{feature:len(companies)
                           for feature,companies in sorted(features.items())}
                           for end,features in sorted(by_period.items())}}
    output={'schema_version':'broad-303-feature-gap-audit-1.0',
            'public_cutoff':audit['public_cutoff'],'cohort_id':'exploratory_observed_303_family',
            'snapshot_version':args.snapshot_version,
            'scopes':scopes,'model_calls':0,'external_requests':0,
            'limitations':['한 기업기간에 같은 계산의 여러 보류 사유가 있어도 대표 사유 하나만 집계합니다.',
                           '원문과 V11 계정·기간 근거가 없는 보류 지표를 0이나 추정값으로 바꾸지 않습니다.',
                           '이 감사는 고정 스냅샷의 결측 구조이며 신규 공시의 존재를 평가하지 않습니다.']}
    path=ROOT/f'doc/finance/BROAD_303_FEATURE_GAP_AUDIT{suffix}_20261027.json'
    encoded=json.dumps(output,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n'
    if path.exists() and path.read_text()!=encoded:raise ValueError('immutable_feature_gap_audit_conflict')
    path.write_text(encoded)
    print(json.dumps({'path':str(path.relative_to(ROOT)),
                      'scope_rows':{key:value['row_count'] for key,value in scopes.items()}},
                     ensure_ascii=False,sort_keys=True))


if __name__=='__main__':main()
