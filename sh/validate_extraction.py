"""3단계 저장된 실자료 추출 검증. 네트워크·모델 요청 없이 별도 DB에 후보를 저장한다."""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['LANGCHAIN_TRACING_V2']='false'
os.environ['LANGSMITH_TRACING']='false'
from agent.finance.runtime import create_finance_client
from agent.finance.extraction_service import ExtractionService, ACCOUNT_RULES
from agent.research_store import ResearchStore, safe_payload


async def main():
    source_result=sorted((ROOT/'data/research/validation_stage02').glob('*/result_after_catalogue_fix.json'))[-1]
    old=json.loads(source_result.read_text())
    old_db=source_result.parent/'session.db';source_root=source_result.parent/'sources'
    source_db_hash=hashlib.sha256(old_db.read_bytes()).hexdigest()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    target=ROOT/'data/research/validation_stage03'/stamp
    target.mkdir(parents=True)
    with closing(sqlite3.connect(old_db.resolve().as_uri()+'?mode=ro',uri=True)) as original, \
            closing(sqlite3.connect(target/'session.db')) as copy:
        original.backup(copy)
    store=ResearchStore(target/'session.db',source_root)
    service=ExtractionService(store)
    raw_source=next(x for x in old['results'] if x['tool']=='dart_document')['members'][0]['source_id']
    api_sources=[x['source_id'] for x in old['results'] if x['tool']=='dart_financials']
    before_requests=store.get_run(old['run_id'])['usage']['external_requests']
    client=create_finance_client(store.db_path,store.source_root)
    await client.start()
    started=time.monotonic()
    try:
        results=[]
        for source_id in [raw_source,*api_sources]:
            result=await client.call_finance('extract_source',{'source_id':source_id})
            if result.get('status')!='ok':
                raise AssertionError('extraction did not finish')
            results.append(result)
            print(json.dumps({key:result[key] for key in ('status','manifest_id','table_count','record_count',
                'candidate_count','coverage','unmapped_raw_table_count') if key in result},ensure_ascii=False),flush=True)
        searches=[]
        for query in ('매출액','27. 현금흐름','차입금'):
            result=await client.call_finance('search_source',{'source_id':raw_source,'query':query,'limit':5})
            searches.append({'query':query,'status':result.get('status'),'match_count':result.get('match_count'),
                             'items':result.get('items',[])})
        manifest=service.fragment(results[0]['manifest_id'])
        facts=[store.get_evidence(fid) for fid in manifest['data']['context']['candidate_ids']]
        current={scope:set() for scope in ('OFS','CFS')}
        verified=0
        for fact in facts:
            data=fact['data'];period=data['period']
            is_current=(period.get('as_of')=='2026-06-30' or (
                period.get('start')=='2026-01-01' and period.get('end')=='2026-06-30'))
            if is_current:current[data['scope']].add(data['account'])
            fragment=fact['fragment'];location=fragment['data']['location']
            original=service.original(fragment['id'])
            assert hashlib.sha256(original).hexdigest()==location['raw_span_sha256']
            assert fact['status']=='candidate'
            verified+=1
        revenue=next(f for f in facts if f['data']['account']=='revenue' and f['data']['scope']=='OFS'
                     and f['data']['period'].get('start')=='2026-01-01')
        quarter=next(f for f in facts if f['data']['account']=='revenue' and f['data']['scope']=='OFS'
                     and f['data']['period'].get('start')=='2026-04-01')
        assert revenue['fragment']['data']['location']['column']!=quarter['fragment']['data']['location']['column']
        repeated=await client.call_finance('extract_source',{'source_id':raw_source})
        assert repeated.get('reused') and repeated['manifest_id']==results[0]['manifest_id']
    finally:
        await client.stop()
    assert hashlib.sha256(old_db.read_bytes()).hexdigest()==source_db_hash
    after_requests=store.get_run(old['run_id'])['usage']['external_requests']
    assert before_requests==after_requests
    completed=all(current[scope]==set(ACCOUNT_RULES) for scope in current)
    output={'results':results,'searches':searches,'candidate_count':len(facts),
            'raw_cell_hashes_verified':verified,'current_period_accounts':{k:sorted(v) for k,v in current.items()},
            'both_scopes_have_13_accounts':completed,'same_manifest_reused':True,
            'source_db_unchanged':True,'external_requests':0,'model_calls':0,
            'elapsed_seconds':round(time.monotonic()-started,3),
            'source_root':str(source_root.relative_to(ROOT)),'run_id':old['run_id'],
            'manifest_id':results[0]['manifest_id'],'financial_verification':'not_performed',
            'note':'원문 위치·추출 후보 검사이며 API 대조·계산·전체 문서 검증이 아님'}
    (target/'result.json').write_text(safe_payload(output))
    print(json.dumps({'result_file':str((target/'result.json').relative_to(ROOT)),
        'candidate_count':len(facts),'raw_cell_hashes_verified':verified,
        'current_period_accounts':output['current_period_accounts'],
        'both_scopes_have_13_accounts':completed,'external_requests':0,'model_calls':0},ensure_ascii=False))
    return 0 if completed and all(s['match_count'] for s in searches) else 1


if __name__=='__main__':
    try:
        sys.exit(asyncio.run(main()))
    except Exception as error:
        print(json.dumps({'error_type':type(error).__name__,'message':'추출 시험이 중단됐습니다. 예외 전문은 출력하지 않습니다.'},ensure_ascii=False))
        sys.exit(1)
