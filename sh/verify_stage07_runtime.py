"""새 분석 복사본/실제 HTTP 프로세스8개로 강제 종료·재개·복구2회 실패를 검증한다.

모델은 시험 전용 합성 응답이며 실제 금융 해석 성능 시험이 아니다.
"""
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4
import httpx

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agent.research_store import safe_payload


def rows(target,query,params=()):
    with closing(sqlite3.connect((target/'session.db').as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        return [dict(r) for r in conn.execute(query,params)]


def fingerprints(target):return {str(p.relative_to(target)):hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob('*') if p.is_file()}


def wait_for(predicate,seconds=30):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        value=predicate()
        if value:return value
        time.sleep(.05)
    raise TimeoutError('stage07_condition_timeout')


def main():
    source=Path(sys.argv[1]).resolve()
    if not source.is_relative_to(ROOT/'data/research/validation_stage06'):raise ValueError('stage06_analysis_required')
    before=fingerprints(source)
    target=ROOT/'data/research/validation_stage07'/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8])
    target.mkdir(parents=True);(target/'source_fingerprint_before.json').write_text(safe_payload(before))
    with closing(sqlite3.connect((source/'session.db').as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target/'session.db')) as dst:src.backup(dst)
    shutil.copytree(source/'sources',target/'sources')
    original_reports=rows(target,'SELECT * FROM research_reports ORDER BY version');initial_version=original_reports[-1]['version']
    rid=original_reports[-1]['research_id'];pids=[];process=None;client=None;port=None
    env=os.environ.copy();env.update(APP_MODE='research',APP_DB_PATH=str(target/'session.db'),APP_SOURCE_ROOT=str(target/'sources'),
        OPENDART_API_KEY='',LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false',APP_ALLOW_EXTERNAL_TRACE='false',APP_ALLOW_EXTERNAL_MODEL='false')
    def start(gate='none'):
        nonlocal process,client,port
        ready=target/'gate-ready'
        if ready.exists():ready.unlink()
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        log=(target/('server-'+str(len(pids)+1)+'.log')).open('w')
        process=subprocess.Popen([sys.executable,str(ROOT/'tests/finance_runtime_server.py'),str(target),gate,str(port)],
            cwd=ROOT,env=env,stdout=log,stderr=log);log.close();pids.append(process.pid)
        client=httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=10)
        def health():
            if process.poll() is not None:raise ValueError('test_server_failed')
            try:return client.get('/health').status_code==200
            except httpx.HTTPError:return False
        wait_for(health)
    def stop(kill=False):
        nonlocal process,client
        if client:client.close();client=None
        if process:
            process.kill() if kill else process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            process=None
    def post_input(key):
        parent=rows(target,'SELECT id FROM research_reports ORDER BY version DESC LIMIT 1')[0]['id']
        response=client.post('/researches/'+rid+'/inputs',json={'parent_report_id':parent,'request_key':key,'kind':'correction',
            'text':'합성 수명주기 시험 입력입니다. 공시 수치와 미확인을 그대로 보존하고 관련 근거를 확인해주세요. 실제 회사 내부자료가 아닙니다.',
            'question_ids':['U_repayment']})
        response.raise_for_status();return response.json()['run_id']
    def run_record(run_id):return rows(target,'SELECT * FROM research_runs WHERE id=?',(run_id,))[0]
    try:
        start('written');first=post_input('stage07-after-draft')
        wait_for(lambda:(target/'gate-ready').exists());before_crash=json.loads(run_record(first)['usage_json']);stop(True)
        assert before_crash['model_calls']==1
        start();wait_for(lambda:len(rows(target,'SELECT id FROM research_reports'))==len(original_reports)+1)
        restored=json.loads(run_record(first)['usage_json']);assert restored['model_calls']==1
        assert restored['execution_seconds']>=before_crash['execution_seconds']
        stop();print(json.dumps({'saved_draft_recovery':'pass','model_repeated':False}),flush=True)

        start('model');second=post_input('stage07-stop-resume');wait_for(lambda:(target/'gate-ready').exists())
        response=client.post('/researches/'+rid+'/stop');response.raise_for_status()
        stopped=json.loads(run_record(second)['usage_json']);assert run_record(second)['status']=='stopped';stop()
        start();time.sleep(.3);assert run_record(second)['status']=='stopped'
        assert json.loads(run_record(second)['usage_json'])==stopped
        response=client.post('/researches/'+rid+'/resume',json={'request_key':'resume-stage07'});response.raise_for_status()
        wait_for(lambda:len(rows(target,'SELECT id FROM research_reports'))==len(original_reports)+2)
        resumed=json.loads(run_record(second)['usage_json']);assert resumed['model_calls']==2
        assert resumed['execution_seconds']>=stopped['execution_seconds'];stop()
        print(json.dumps({'stop_restart_resume':'pass','stopped_not_auto_started':True}),flush=True)

        start('model');third=post_input('stage07-recovery-failures');wait_for(lambda:(target/'gate-ready').exists());stop(True)
        for attempt in range(2):
            start('recovery');wait_for(lambda:(target/'gate-ready').exists());stop(True)
        start();wait_for(lambda:run_record(third)['status']=='stopped')
        blocked=client.get('/runs/'+third+'/execution').json()['items'][0]
        assert blocked['recovery_failures']==2
        assert json.loads(run_record(third)['usage_json'])['recovery_failures']==2
        assert json.loads(run_record(third)['usage_json'])['model_calls']==1
        current=rows(target,'SELECT * FROM research_reports ORDER BY version')
        assert current[:len(original_reports)]==original_reports
        assert len(current)==len(original_reports)+2
        for row in current:
            value=client.get('/reports/'+row['id']);value.raise_for_status()
            assert value.json()['status']=='published' and value.json()['content']==json.loads(row['content_json'])
        stop()
        assert fingerprints(source)==before
        model_calls=len((target/'synthetic-model-calls.jsonl').read_text().splitlines());assert model_calls==4
        summary={'status':'pass','scope':'stored_case_real_HTTP_processes_synthetic_model','http_processes':pids,
            'saved_draft_recovery_without_new_model_call':True,'stop_survives_restart':True,'resume_same_run_same_budget':True,
            'consecutive_recovery_failures_blocked':2,'synthetic_model_calls':model_calls,'external_requests':0,'external_trace':False,
            'old_report_count':len(original_reports),'new_versions':[initial_version+1,initial_version+2],
            'old_reports_unchanged':True,'source_files_preserved':len(before),'source_files_changed':0,'source_files_deleted':0,
            'runs':[{'run_id':r,'status':run_record(r)['status'],'usage':json.loads(run_record(r)['usage_json'])} for r in (first,second,third)],
            'financial_confirmation':'incomplete'}
        (target/'result.json').write_text(safe_payload(summary))
        print(json.dumps({'result_file':str((target/'result.json').relative_to(ROOT)),**summary},ensure_ascii=False),flush=True)
    finally:stop()


if __name__=='__main__':main()
