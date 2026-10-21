"""보관한 상신브레이크 자료의 로컬 대조. 원본/기존 DB를 수정하거나 외부 조회하지 않는다."""
from collections import Counter, defaultdict
from contextlib import closing
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import socket
import subprocess
import time
from urllib.request import ProxyHandler, build_opener
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
os.environ['LANGCHAIN_TRACING_V2']='false'
os.environ['LANGSMITH_TRACING']='false'
from agent.finance.numeric_validation import NumericValidationService, decimal, period_key
from agent.finance.validation_acceptance import assess_stage04
from agent.finance.runtime import create_finance_client
from agent.research_models import ResearchInput, SourceInput
from agent.research_store import ResearchStore, safe_payload


class ValidationRunFailure(ValueError):
    pass


def require(condition,reason):
    if not condition:
        raise ValidationRunFailure(reason)


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


async def main(output_root=None):
    case=ROOT/'doc/finance/cases/sangsin_brake'
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    target=Path(output_root or ROOT/'data/research/validation_stage04')/stamp;target.mkdir(parents=True)
    protected={str(p.relative_to(ROOT)):sha(p) for p in case.rglob('*') if p.is_file()}
    store=ResearchStore(target/'session.db',target/'sources')
    research=store.create_research(ResearchInput(company={'name':'상신브레이크','corp_code':'00129642','stock_code':'041650'},
        purpose='보관 공시 수치·산술·비교 검증',public_cutoff='2026-10-20',request_key='stage04-local-cache',
        comparison_window={'annual_years':[2023,2024,2025],'interim_periods':['2025-H1','2026-H1']}))
    run_id=research['runs'][0]['id'];imports=[]
    for path in sorted((case/'raw').glob('financial_*.json')):
        wrapper=json.loads(path.read_text());records=wrapper['response']['list']
        receipts={row['rcept_no'] for row in records};require(len(receipts)==1,'multiple_receipts_in_one_api_source')
        receipt=next(iter(receipts))
        source=store.save_source(run_id,SourceInput(kind='dart',title=path.name,publisher='금융감독원 OpenDART',
            url='https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json',
            receipt_number=receipt,published_at=date_string(receipt),retrieved_at=wrapper['retrieved_at_utc'],
            reporting_period={'operation':'financials',**wrapper['query']},media_type='application/json',rights_status='public_disclosure'),path.read_bytes())
        imports.append({'file':str(path.relative_to(ROOT)),'sha256':sha(path),'source_id':source['id'],'receipt':receipt})
    for receipt in ('20260318001187','20250813000967','20260813000874'):
        path=case/'raw'/f'{receipt}_{receipt}.xml'
        source=store.save_source(run_id,SourceInput(kind='dart',title=path.name,publisher='금융감독원 DART',
            url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+receipt,receipt_number=receipt,
            published_at=date_string(receipt),retrieved_at=datetime.now(timezone.utc).isoformat(),
            reporting_period={'operation':'document_member','member_name':receipt+'.xml','imported_local_case':True,
                'retrieval_time_note':'로컬 재수신 시각이며 최초 다운로드 시각은 미확인',
                'previous_file_modified_at':datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat()},
            media_type='application/xml',rights_status='public_disclosure'),path.read_bytes())
        imports.append({'file':str(path.relative_to(ROOT)),'sha256':sha(path),'source_id':source['id'],'receipt':receipt})
    (target/'imports.json').write_text(safe_payload({'run_id':run_id,'imports':imports}))
    print(json.dumps({'target':str(target.relative_to(ROOT)),'imported_sources':len(imports)},ensure_ascii=False),flush=True)
    client=create_finance_client(store.db_path,store.source_root);await client.start()
    try:
        result=await client.call_finance('validate_numbers',{'run_id':run_id})
        if result.get('status')!='ok':
            (target/'failure.json').write_text(safe_payload(result));raise AssertionError('local_mcp_validation_failed')
        repeated=await client.call_finance('validate_numbers',{'run_id':run_id})
        require(repeated['reused'] and repeated['batch_id']==result['batch_id'],'batch_reuse_mismatch')
    finally:await client.stop()
    batch=NumericValidationService(store).batch(result['batch_id'])
    (target/'batch.json').write_text(safe_payload(batch))
    # 새 실제 프로세스에서 같은 DB·원문을 열어 재사용을 확인한다.
    restarted=create_finance_client(store.db_path,store.source_root);await restarted.start()
    try:
        readback=await restarted.call_finance('validate_numbers',{'run_id':run_id})
        require(readback['reused'] and readback['batch_id']==result['batch_id'],'mcp_restart_readback_mismatch')
    finally:await restarted.stop()
    http_result=http_restart_readback(store,target,run_id,result['batch_id'])
    (target/'restart_readback.json').write_text(safe_payload(http_result))
    reference=reference_comparison(store,batch,case)
    (target/'reference_comparison.json').write_text(safe_payload(reference))
    require(all(sha(ROOT/name)==value for name,value in protected.items()),'protected_source_changed')
    acceptance=assess_stage04(batch,reference,store=store)
    (target/'acceptance.json').write_text(safe_payload(acceptance))
    output={'acceptance':acceptance,'run_id':run_id,'batch_id':result['batch_id'],'summary':batch['summary'],
            'external_data_requests':0,'model_calls':0,'new_mcp_process_readback':True,'source_case_unchanged':True,
            'same_batch_reused':True,'http_restart_readback':http_result,'reference_counts':reference['counts'],'imports':imports,
            'note':'보관 자료의 한정 검사. 합성 모델 성공·최신 공시 확인·은행 전문가 정답으로 해석하지 않음'}
    (target/'result.json').write_text(safe_payload(output))
    print(json.dumps({'result_file':str((target/'result.json').relative_to(ROOT)), 'reference_counts':reference['counts'],'acceptance':acceptance,**batch['summary']},ensure_ascii=False),flush=True)
    return acceptance['exit_code']


def reference_comparison(store,batch,case):
    """기존 수작업 사례와의 회귀 대조. 이 자료를 은행 전문가 정답으로 취급하지 않는다."""
    index=defaultdict(list)
    for v in batch['validations']:
        if v['status']!='pass' or not v['fact_id']:continue
        d=store.get_evidence(v['fact_id'])['data']
        index[(d['account'],d['scope'],json.dumps(period_key(d['period']),sort_keys=True))].append(
            (v['fact_id'],decimal(v['data']['amount_comparison']['original_normalized'])))
    names={'current_receivables':'receivables','short_borrowings':'short_term_borrowings',
           'current_long_borrowings':'current_long_term_borrowings','noncurrent_borrowings':'long_term_borrowings'}
    flows={'revenue','operating_profit','net_profit','operating_cashflow'};results=[]
    reference_path=case/'financial_evidence.json'
    for r in json.loads(reference_path.read_text())['records']:
        for name,metric in r['metrics'].items():
            if name in ('total_borrowings','current_borrowings'):continue
            account=names.get(name,name)
            period=({'kind':'duration','start':r['balance_date'][:4]+'-01-01','end':r['balance_date']}
                    if account in flows else {'kind':'instant','as_of':r['balance_date']})
            values=index.get((account,r['scope'],json.dumps(period,sort_keys=True)),[])
            outcome=('pass' if {value for fid,value in values}=={decimal(str(metric['value_krw']))}
                     else ('not_checked' if not values else 'fail'))
            results.append({'period':r['period'],'scope':r['scope'],'account':account,'status':outcome,
                            'expected':str(metric['value_krw']),'fact_ids':[fid for fid,value in values]})
    return {'counts':dict(Counter(r['status'] for r in results)),'items':results,
            'reference_sha256':sha(reference_path),'note':'기존 수작업 사례 13개 계정 회귀 대조이며 은행 전문가 정답이 아님'}


def http_restart_readback(store,target,run_id,batch_id):
    opener=build_opener(ProxyHandler({}));snapshots=[]
    with closing(socket.socket()) as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env={**os.environ,'APP_MODE':'research','APP_DB_PATH':str(store.db_path),'APP_SOURCE_ROOT':str(store.source_root),
         'OPENDART_API_KEY':'','APP_ALLOW_EXTERNAL_MODEL':'false','APP_ALLOW_EXTERNAL_TRACE':'false',
         'LANGCHAIN_TRACING_V2':'false','LANGSMITH_TRACING':'false'}
    def get(path):
        with opener.open(f'http://127.0.0.1:{port}'+path,timeout=5) as response:return json.load(response)
    for attempt in range(2):
        with (target/f'http_server_{attempt+1}.log').open('w') as logfile:
            process=subprocess.Popen([sys.executable,'-m','uvicorn','server:app','--host','127.0.0.1','--port',str(port),
                                      '--log-level','warning'],cwd=ROOT,env=env,stdout=logfile,stderr=subprocess.STDOUT)
            try:
                deadline=time.monotonic()+20
                while time.monotonic()<deadline:
                    if process.poll() is not None:raise AssertionError('http_server_start_failed')
                    try:
                        health=get('/health')
                        if health['status']=='ok':break
                    except OSError:time.sleep(0.05)
                else:raise AssertionError('http_server_start_timeout')
                require(health['llm_backend']=='disabled' and health['implementation_stage']=='4','unexpected_server_mode')
                snapshots.append({'history':get(f'/runs/{run_id}/validations'),
                                  'batch':get(f'/validation-batches/{batch_id}')})
            finally:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
    require(snapshots[0]==snapshots[1],'http_restart_result_mismatch')
    require(snapshots[1]['batch']['id']==batch_id,'http_restart_batch_mismatch')
    return {'fresh_http_server_processes':2,'history_and_batch_equal':True,'batch_id':batch_id,
            'llm_backend':'disabled','external_data_access':'disabled'}


def date_string(receipt):return receipt[:4]+'-'+receipt[4:6]+'-'+receipt[6:8]


if __name__=='__main__':
    try:sys.exit(asyncio.run(main()))
    except Exception as error:
        print(json.dumps({'error_type':type(error).__name__,'message':'수치 검증 중단. 기존 원문은 보존하며 예외 전문은 출력하지 않습니다.'},ensure_ascii=False))
        sys.exit(1)
