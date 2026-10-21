"""기존 6단계 실행을 호출/한도 초기화 없이 원본 행·원문·두 HTTP 프로세스로 재확인한다."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import time

import httpx
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agent.finance.local_model import checked_draft
from agent.research_store import safe_payload


def read_rows(directory,table):
    with closing(sqlite3.connect((directory/'session.db').as_uri()+'?mode=ro',uri=True)) as conn:
        return conn.execute('SELECT * FROM "'+table+'"').fetchall()


def main():
    target=Path(sys.argv[1]).resolve();source=Path(sys.argv[2]).resolve()
    if not target.is_relative_to(ROOT/'data/research/validation_stage06'):raise ValueError('analysis_required')
    if not source.is_relative_to(ROOT/'data/research/validation_stage05'):raise ValueError('stage05_analysis_required')
    protected={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for folder in (source,target)
        for p in folder.rglob('*') if p.is_file() and p.name!='publication_readback.json'}
    with closing(sqlite3.connect((source/'session.db').as_uri()+'?mode=ro',uri=True)) as conn:
        tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        raw_sources=conn.execute('SELECT storage_key,content_hash FROM research_sources').fetchall()
    rows_preserved=0
    for table in tables:
        original=read_rows(source,table);current=set(read_rows(target,table))
        assert all(row in current for row in original), 'original_rows_changed:'+table
        rows_preserved+=len(original)
    for key,content_hash in raw_sources:
        for folder in (source,target):
            assert hashlib.sha256((folder/'sources'/key).read_bytes()).hexdigest()==content_hash
    with closing(sqlite3.connect((target/'session.db').as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        reports=[dict(r) for r in conn.execute('SELECT * FROM research_reports ORDER BY version')]
        runs=[dict(r) for r in conn.execute('SELECT * FROM research_runs ORDER BY created_at')]
        receipts=[json.loads(r[0]) for r in conn.execute("SELECT payload_json FROM research_events WHERE kind='followup_received' ORDER BY sequence")]
        memos=conn.execute("SELECT COUNT(*) FROM research_events WHERE kind='followup_memo_saved'").fetchone()[0]
    old_count=len(read_rows(source,'research_reports'));assert len(reports)==old_count+2
    first=json.loads(reports[old_count-1]['content_json']);results=[]
    for row in reports[old_count:]:
        content=json.loads(row['content_json']);context=content['context']
        assert content['model_status']=='validated'
        checked_draft(content['model']['draft'],context['model_evidence'])
        assert context['financial_rows']==first['context']['financial_rows']
        assert context['charts']==first['context']['charts']
        assert content['confirmed_facts']==first['confirmed_facts']
        assert all(u['state']=='withheld' for u in context['unknowns'])
        usage=json.loads(next(r for r in runs if r['id']==row['run_id'])['usage_json'])
        assert usage['external_requests']==0 and usage['model_calls']<=3
        results.append({'version':row['version'],'report_id':row['id'],'run_id':row['run_id'],
            'model_status':content['model_status'],'usage':usage,'affected_sections':context['affected_sections']})
    processes=[]
    for _ in range(2):
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        env=os.environ.copy();env.update(APP_MODE='research',APP_DB_PATH=str(target/'session.db'),
            APP_SOURCE_ROOT=str(target/'sources'),OPENDART_API_KEY='',LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false',
            APP_ALLOW_EXTERNAL_MODEL='false',APP_ALLOW_EXTERNAL_TRACE='false')
        process=subprocess.Popen([sys.executable,'-m','uvicorn','server:app','--host','127.0.0.1','--port',str(port)],
            cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            with httpx.Client(trust_env=False,timeout=2) as client:
                for attempt in range(200):
                    try:
                        response=client.get(f'http://127.0.0.1:{port}/health');response.raise_for_status();break
                    except httpx.HTTPError:
                        if process.poll() is not None:raise ValueError('server_start_failed')
                        time.sleep(.05)
                else:raise ValueError('server_start_timeout')
                for row in reports:
                    response=client.get(f'http://127.0.0.1:{port}/reports/'+row['id']);response.raise_for_status()
                    current=response.json();assert current['status']=='published'
                    assert safe_payload(current['content'])==safe_payload(json.loads(row['content_json']))
                history=client.get(f'http://127.0.0.1:{port}/researches/'+reports[0]['research_id']+'/reports').json()
                assert [r['version'] for r in history['items']]==[r['version'] for r in reports]
                processes.append({'pid':process.pid,'versions':[r['version'] for r in reports],'all_contents_equal':True})
        finally:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    assert len({p['pid'] for p in processes})==2
    assert all(Path(p).exists() and hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in protected.items())
    summary={'status':'pass','scope':'stored_sangsin_case_synthetic_followup_test','reports':results,
        'http_processes':processes,'model_calls_during_readback':0,'external_requests':0,'external_trace':False,
        'original_database_rows_equal_to_copy':rows_preserved,'original_sources_equal_to_copy':len(raw_sources),
        'files_preserved_during_readback':len(protected),'changed_during_readback':0,'deleted_during_readback':0,
        'prior_report_versions_preserved':old_count,'memo_stored_only':memos==1,
        'followup_input_count':len(receipts),'text_followup_verified':True,'financial_confirmation':'incomplete',
        'initial_model_test_file_baseline_saved':(target/'source_fingerprint_before.json').exists(),
        'initial_test_harness_issue':'list_detail_fields_mismatch' if not (target/'result.json').exists() else None,
        'full_stage06_complete':False,'remaining':'general_attachments_external_recollection_UI_and_stage07_recovery'}
    (target/'publication_readback.json').write_text(safe_payload(summary))
    print(json.dumps({'result_file':str((target/'publication_readback.json').relative_to(ROOT)),**summary},ensure_ascii=False))


if __name__=='__main__':main()
