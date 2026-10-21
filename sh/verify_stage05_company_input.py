"""빈 조사 DB에서 회사 입력 한 번→보관 원문 준비→실모델 초안→재시작 조회를 검증한다."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agent.finance.cached_case import CachedCaseLibrary
from agent.finance.report_context import numeric_rows
from agent.research_store import safe_payload


def fingerprints(root):return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


def main():
    library=CachedCaseLibrary(ROOT,ROOT/'data/research/case_library/sangsin-20261020.json');library.verify()
    existing=len(sys.argv)>2 and sys.argv[1]=='--existing'
    if existing:
        target=Path(sys.argv[2]).resolve()
        if not target.is_relative_to(ROOT/'data/research/validation_stage05_input'):raise ValueError('input_analysis_required')
        before=json.loads((target/'source_fingerprint_before.json').read_text())
    else:
        before=fingerprints(library.directory);before[str(library.path)]=hashlib.sha256(library.path.read_bytes()).hexdigest()
        tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
        target=ROOT/'data/research/validation_stage05_input'/tag;target.mkdir(parents=True)
        (target/'source_fingerprint_before.json').write_text(safe_payload(before))
    env=os.environ.copy();env.update(APP_MODE='research',APP_DB_PATH=str(target/'session.db'),APP_SOURCE_ROOT=str(target/'sources'),
        OPENDART_API_KEY='',LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false',APP_ALLOW_EXTERNAL_MODEL='false',APP_ALLOW_EXTERNAL_TRACE='false')
    process=None;client=None;pids=[]
    def start():
        nonlocal process,client
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        log=(target/('server-'+str(len(pids)+1)+'.log')).open('w')
        process=subprocess.Popen([sys.executable,'-m','uvicorn','server:app','--host','127.0.0.1','--port',str(port)],
            cwd=ROOT,env=env,stdout=log,stderr=log);log.close();pids.append(process.pid)
        client=httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=10)
        for _ in range(300):
            if process.poll() is not None:raise ValueError('server_failed')
            try:
                response=client.get('/health')
                if response.status_code==200:
                    assert response.json()['cached_case_start_available'];return
            except httpx.HTTPError:pass
            time.sleep(.05)
        raise TimeoutError('startup_timeout')
    def stop():
        nonlocal process,client
        if client:client.close();client=None
        if process:
            process.terminate()
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            process=None
    try:
        start()
        response=client.get('/researches');response.raise_for_status()
        if not existing:assert response.json()['items']==[]
        companies=client.get('/case-companies',params={'query':'상신브레이크'}).json()
        assert companies['external_requests']==0 and companies['items'][0]['company']['corp_code']=='00129642'
        value={'company':'상신브레이크','purpose':'공개 공시만 사용하는 가상 운전자금 사전조사 입력 연결 시험. 실제 대출 신청이나 내부자료가 아니다.',
            'request_key':'company-input-first-draft','public_cutoff':'2026-10-20'}
        if existing:receipt=json.loads((target/'receipt.json').read_text())
        else:
            response=client.post('/researches/start',json=value);response.raise_for_status();receipt=response.json()
        duplicate=client.post('/researches/start',json=value);duplicate.raise_for_status()
        assert duplicate.json()['research_id']==receipt['research_id'] and duplicate.json()['run_id']==receipt['run_id']
        if not existing:(target/'receipt.json').write_text(safe_payload(receipt))
        print(json.dumps({'company_input':'accepted','run_id':receipt['run_id'],'empty_database':True,'duplicate_same_run':True}),flush=True)
        report=None;last_phase=None
        for _ in range(6000):
            history=client.get(receipt['reports_url']);history.raise_for_status();items=history.json()['items']
            if items:
                report=client.get('/reports/'+items[-1]['id']).json();break
            run=client.get('/runs/'+receipt['run_id']).json()
            execution=client.get(receipt['execution_url']).json()['items']
            phase=execution[-1]['phase'] if execution else 'queued'
            if phase!=last_phase:
                print(json.dumps({'phase':phase,'usage':run['usage']},ensure_ascii=False),flush=True);last_phase=phase
            if run['status'] in {'failed','stopped'} or (execution and execution[-1]['phase']=='limited'):raise ValueError('input_workflow_did_not_publish')
            time.sleep(.1)
        if report is None:raise TimeoutError('company_input_report_timeout')
        assert report['status']=='published' and report['version']==1
        context=report['content']['context'];assert len(context['sections'])==7
        expected=numeric_rows(json.loads((library.directory/'numeric_batch.json').read_text()))
        def semantic(rows):return [{k:v for k,v in r.items() if k not in {'fact_ids','validation_ids','source_ids'}} for r in rows]
        assert semantic(context['financial_rows'])==semantic(expected)
        assert len(context['financial_rows'])==130
        unknown=[u['evidence_id'] for u in context['unknowns']];assert len(unknown)==4
        run=client.get('/runs/'+receipt['run_id']).json();assert run['usage']['external_requests']==0
        state=client.get('/researches/'+receipt['research_id']).json()
        assert len(state['runs'])==1 and len(state['reports'])==1
        events=client.get('/researches/'+receipt['research_id']+'/events').json()['items']
        prepared=[e for e in events if e['kind']=='cached_sources_prepared'];assert len(prepared)==1
        assert prepared[0]['payload']['source_count']==len(library.manifest['sources'])
        assert prepared[0]['payload']['validation_flags_copied'] is False
        assert run['usage']['model_calls']<=3
        (target/'report-v1.json').write_text(safe_payload(report));(target/'run.json').write_text(safe_payload(run))
        prior_usage=run['usage'];stop();start()
        restored=client.get('/reports/'+report['id']);restored.raise_for_status();assert restored.json()==report
        assert client.get('/runs/'+receipt['run_id']).json()['usage']==prior_usage
        assert len(client.get(receipt['reports_url']).json()['items'])==1
        assert all(Path(p).exists() and hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in before.items())
        summary={'status':'pass','scope':'sangsin_company_input_archived_public_sources','research_id':receipt['research_id'],
            'run_id':receipt['run_id'],'report_id':report['id'],'report_version':1,'http_processes':pids,'empty_database_start':True,
            'single_input_creates_and_executes':True,'duplicate_same_run':True,'source_count':len(library.manifest['sources']),
            'validation_flags_copied':False,'financial_rows':130,'verified_rows':sum(r['status']=='verified' for r in context['financial_rows']),
            'withheld_rows':sum(r['status']=='withheld' for r in context['financial_rows']),'unknowns_preserved':unknown,
            'model_status':report['content']['model_status'],'usage':prior_usage,'external_requests':0,'external_trace':False,
            'restart_same_report_and_usage':True,'original_file_count':len(before),'original_changed':0,'original_deleted':0,
            'financial_confirmation':'incomplete','general_new_company_collection':False,
            'existing_execution_rechecked':existing}
        (target/'result.json').write_text(safe_payload(summary))
        print(json.dumps({'result_file':str((target/'result.json').relative_to(ROOT)),**summary},ensure_ascii=False),flush=True)
        return 0 if report['content']['model_status']=='validated' else 1
    finally:stop()


if __name__=='__main__':sys.exit(main())
