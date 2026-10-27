"""Replay one verified general-company run from the isolated cohort archive.

No OpenDART or model request is made. The selected run is read-only.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.finance.peer_dataset import build_snapshot
from agent.finance.verified_run_peer import build_run
from agent.research_api import router
from agent.research_store import ResearchStore

DATA=ROOT/'data/research/broad_303_20261027'
CANDIDATES=ROOT/'doc/finance/BROAD_303_CANDIDATES_20261027.json'


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--corp-code',required=True)
    args=parser.parse_args()
    progress=json.loads((DATA/'progress.json').read_text())['results']
    row=progress[args.corp_code]
    if row.get('cohort_status')!='exploratory_verified_candidate':
        raise ValueError('selected_run_not_verified')
    candidate=next(item for item in json.loads(CANDIDATES.read_text())['records']
                   if item['corp_code']==args.corp_code)
    store=ResearchStore(DATA/'session.db',DATA/'sources')
    run_id=row['run_id']
    run=store.get_run(run_id)
    if run['usage']['model_calls']!=0 or run['status']!='finished':
        raise ValueError('unexpected_run_usage_or_status')
    app=FastAPI();app.state.research_store=store;app.include_router(router)
    with TestClient(app) as client:
        response=client.get('/runs/'+run_id+'/verified-analysis')
    if response.status_code!=200:
        raise ValueError('verified_analysis_api_failed:'+str(response.status_code))
    analysis=response.json()
    if (analysis['row_counts']['verified']!=row['verified_rows'] or
            analysis['row_counts']['withheld']!=row['withheld_rows'] or
            analysis['xbrl_gate']['status']!='pass' or
            analysis['disclosure_history']['status']!='pass'):
        raise ValueError('verified_run_result_changed')
    source_ids={item['source_id'] for item in analysis['evidence'] if item.get('source_id')}
    hashes=[]
    for source_id in sorted(source_ids):
        source=store.get_source(source_id)
        actual=sha256(store.source_content(source_id)).hexdigest()
        if actual!=source['content_hash']:
            raise ValueError('archived_source_hash_mismatch')
        hashes.append(actual)
    peer=build_run(store,run_id,candidate)
    if len(peer['records'])!=row['eligible_observations']:
        raise ValueError('eligible_observation_count_changed')
    for scope in ('OFS','CFS'):
        actual=build_snapshot([item for item in peer['records'] if item['scope']==scope])['rows']
        recorded=json.loads((ROOT/f'doc/finance/BROAD_303_EXPANDED_{scope}_20261027.json').read_text())
        expected=[item for item in recorded['rows'] if item['corp_code']==args.corp_code]
        if actual!=expected:
            raise ValueError('immutable_peer_rows_changed:'+scope)
    report={'schema_version':'cached-general-company-replay-1.0','corp_code':args.corp_code,
            'corp_name':row['corp_name'],'run_id':run_id,
            'validation_batch_id':analysis['validation_batch_id'],
            'mapping_batch_id':analysis['mapping_batch_id'],
            'verified_rows':analysis['row_counts']['verified'],
            'withheld_rows':analysis['row_counts']['withheld'],
            'source_count':len(hashes),
            'source_hash_manifest_sha256':sha256('\n'.join(hashes).encode()).hexdigest(),
            'eligible_observations':len(peer['records']),
            'independent_xbrl_gate':'pass','disclosure_history':'pass',
            'model_calls':0,'external_requests_during_replay':0,
            'snapshot_rows_match':True,
            'limitation':'기존 보관 실행의 읽기 재현이다. 새 공시 수집·새 기준일 검증은 포함하지 않는다.'}
    print(json.dumps(report,ensure_ascii=False,sort_keys=True,separators=(',',':')))


if __name__=='__main__':main()
