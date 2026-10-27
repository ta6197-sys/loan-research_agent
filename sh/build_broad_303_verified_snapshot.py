"""Read stored verified runs and write immutable exploratory OFS/CFS snapshots.

No OpenDART or model calls, publication, or changes to existing batches.
"""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.research_store import ResearchStore
from agent.finance.model_readiness import evaluate as model_readiness
from agent.finance.peer_dataset import build_snapshot
from agent.finance.verified_run_peer import build_run

RUNS=(('00157539','6bc0f8acb8d044e0a646862d4da77575'),
      ('00305668','5f3cd67dcc6e45b8874d04bba0ce804d'))
CANDIDATES=ROOT/'doc/finance/BROAD_303_CANDIDATES_20261027.json'
TARGET=ROOT/'doc/finance'


def immutable_write(path,payload):
    content=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n'
    if path.exists() and path.read_text()!=content:
        raise ValueError('immutable_snapshot_conflict:'+path.name)
    path.write_text(content)


def main():
    candidates={item['corp_code']:item for item in json.loads(CANDIDATES.read_text())['records']}
    store=ResearchStore(ROOT/'session.db',ROOT/'data/research')
    records=[];run_audits=[]
    for code,run_id in RUNS:
        result=build_run(store,run_id,candidates[code])
        records.extend(result['records'])
        run_audits.append({'corp_code':code,'run_id':run_id,'candidate_code':result['candidate_code'],
                           'accepted_record_count':len(result['records']),'excluded':result['excluded']})
    outputs={}
    for scope in ('OFS','CFS'):
        snapshot=build_snapshot([item for item in records if item['scope']==scope])
        readiness=model_readiness(snapshot)
        path=TARGET/f'BROAD_303_VERIFIED_{scope}_20261027.json'
        immutable_write(path,snapshot)
        outputs[scope]={'file':path.name,'snapshot_id':snapshot['snapshot_id'],
                        'row_count':snapshot['row_count'],
                        'distinct_companies':len({r['corp_code'] for r in snapshot['rows']}),
                        'annual_row_count':readiness['annual_row_count'],
                        'training_status':readiness['training_status'],
                        'industry_revision_gate':readiness['checks']['industry_revision'],
                        'feature_missingness':snapshot['feature_missingness'],
                        'excluded_records':snapshot['excluded_records']}
    summary={'schema_version':'broad-303-verified-audit-1.0','public_cutoff':'2026-10-27',
             'candidate_manifest':'BROAD_303_CANDIDATES_20261027.json',
             'group_id':'exploratory_observed_303_family',
             'not_ksic30392_snapshot':True,'run_audits':run_audits,'scopes':outputs,
             'limitations':['303 계열 XBRL 자체 코드의 KSIC 개정 차수는 미확인입니다.',
                '보관된 미게시 검증 실행 2곳만 포함하며 산업 비교 최소 표본에 못 미칩니다.',
                '이 산출물은 분석 전용이며 보고서 게시·모델 학습을 하지 않았습니다.']}
    immutable_write(TARGET/'BROAD_303_VERIFIED_AUDIT_20261027.json',summary)
    print(json.dumps({'scope_rows':{scope:item['row_count'] for scope,item in outputs.items()},
                      'distinct_companies':2,'ml_status':outputs['OFS']['training_status']},
                     ensure_ascii=False,sort_keys=True))


if __name__=='__main__': main()
