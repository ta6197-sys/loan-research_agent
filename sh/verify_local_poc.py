"""새 분석 복사본에서 HTTP 첨부→새 판→계획/보관/재시작/복원→출력을 확인한다."""
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import io
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
import zipfile
import httpx
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agent.research_store import safe_payload
from agent.finance.stage04_completion import code_fingerprint


def hashes(folder):return {str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob('*') if p.is_file()}


def main():
    source=Path(sys.argv[1]).resolve()
    if not source.is_relative_to(ROOT/'data/research/evaluation_stage09'):raise ValueError('evaluation_analysis_required')
    before=hashes(source);stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    target=ROOT/'data/research/validation_stage08'/('final-'+stamp);target.mkdir(parents=True)
    with closing(sqlite3.connect((source/'session.db').as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target/'session.db')) as dst:src.backup(dst)
    shutil.copytree(source/'sources',target/'sources')
    env=os.environ.copy();env.update(APP_MODE='research',APP_DB_PATH=str(target/'session.db'),APP_SOURCE_ROOT=str(target/'sources'),
        OPENDART_API_KEY='',LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false',APP_ALLOW_EXTERNAL_MODEL='false',APP_ALLOW_EXTERNAL_TRACE='false')
    client=httpx.Client(trust_env=False,timeout=90);process=None;pids=[];base=''
    def call(path,method='GET',**kwargs):
        response=client.request(method,base+path,**kwargs);response.raise_for_status();return response.json()
    def start():
        nonlocal process,base
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        log=(target/f'http-{len(pids)+1}.log').open('w')
        process=subprocess.Popen([sys.executable,'-m','uvicorn','server:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log);log.close()
        pids.append(process.pid);base=f'http://127.0.0.1:{port}'
        for _ in range(200):
            try:call('/health');return
            except httpx.HTTPError:
                if process.poll() is not None:raise ValueError('server_start_failed')
                time.sleep(.05)
        raise ValueError('server_start_timeout')
    def stop():
        if process and process.poll() is None:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
    def wait_report(rid,count):
        for _ in range(240):
            research=call('/researches/'+rid+'/summary')
            if len(research['reports'])>=count:return research
            if any(r['status']=='failed' for r in research['runs']):raise ValueError('research_failed')
            time.sleep(1)
        raise ValueError('report_timeout')
    try:
        start();rid=call('/researches')['items'][0]['id'];research=call('/researches/'+rid+'/summary')
        first_id=research['reports'][-1]['id'];first=call('/reports/'+first_id);oldview=call('/reports/'+first_id+'/view')
        assert len(oldview['business_review'])==7 and all(i.get('quote') for i in oldview['business_review'])
        assert len(oldview['minimum_review'])==7
        bundle=call('/researches/'+rid+'/attachment-bundles','POST',data={'parent_report_id':first_id},files=[
            ('files',('synthetic-note.txt','합성 후속 설명: 실제 회수 일정은 미확인입니다.'.encode(),'text/plain')),
            ('files',('synthetic-values.csv',b'code,value\n000123,999','text/csv'))])
        payload={'parent_report_id':first_id,'request_key':'final-attachment','kind':'answer',
            'text':'합성 시험 답변입니다. 첨부 숫자는 공시 사실로 확정하지 마세요.','question_ids':['U_repayment'],'attachment_bundle_id':bundle['bundle_id']}
        receipt=call('/researches/'+rid+'/inputs','POST',json=payload)
        again=call('/researches/'+rid+'/inputs','POST',json=payload);assert receipt['input_id']==again['input_id'] and receipt['run_id']==again['run_id']
        research=wait_report(rid,2);second_id=research['reports'][-1]['id'];second=call('/reports/'+second_id)
        assert second['content']['confirmed_facts']==first['content']['confirmed_facts']
        assert second['content']['model_status']=='validated'
        attachments=second['content']['context']['user_observations'][0]['attachments'];assert len(attachments)==2
        assert all(a['financial_verification']=='not_performed' for a in attachments)
        assert first==call('/reports/'+first_id)
        exports=[]
        for kind in ('pdf','markdown_zip'):
            item=call('/reports/'+second_id+'/exports','POST',json={'format':kind});response=client.get(base+item['download_url']);response.raise_for_status()
            assert hashlib.sha256(response.content).hexdigest()==item['content_hash']
            if kind=='pdf':
                reader=PdfReader(io.BytesIO(response.content));text='\n'.join(p.extract_text() for p in reader.pages)
                assert 'synthetic-note.txt' in text and '금융 미검증' in text
                output=ROOT/'output/pdf'/('sangsin-brake-final-'+stamp+'.pdf');output.parent.mkdir(exist_ok=True,parents=True);output.write_bytes(response.content)
            else:
                (target/'report.zip').write_bytes(response.content)
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    assert 'synthetic-note.txt' in archive.read('report.md').decode()
                    assert not any(n.startswith(('sources/','raw/')) for n in archive.namelist())
            exports.append(item)
        call('/researches/'+rid+'/stop','POST',json={})
        plan={'request_key':'final-plan','expected_basis_version':1,'purpose':'합성 후속 계획: 차입을 먼저 확인하는 운전자금 사전조사','focus_sections':['borrowing']}
        changed=call('/researches/'+rid+'/plan','POST',json=plan);assert changed['status']=='stopped'
        assert changed==call('/researches/'+rid+'/plan','POST',json=plan)
        usage=call('/researches/'+rid+'/summary')['cumulative_usage']
        call('/researches/'+rid+'/archive','POST',json={});assert not call('/researches')['items']
        assert client.get(base+'/reports/'+first_id).status_code==404
        assert client.get(base+'/researches/'+rid+'/events').status_code==404
        stop();start()
        assert call('/research-archives')['items'][0]['id']==rid
        call('/researches/'+rid+'/restore','POST',json={});restored=call('/researches/'+rid+'/summary')
        assert restored['paused'] and restored['cumulative_usage']==usage
        assert first==call('/reports/'+first_id) and second==call('/reports/'+second_id)
        call('/researches/'+rid+'/resume','POST',json={'request_key':'final-resume'})
        research=wait_report(rid,3);third=call('/reports/'+research['reports'][-1]['id'])
        assert third['content']['model_status']=='validated'
        assert third['content']['context']['sections'][0]['id']=='borrowing'
        compact=lambda report:[(r['scope'],r['end'],r['account'],r['status'],r.get('value')) for r in report['content']['context']['financial_rows']]
        assert compact(first)==compact(third)
        stop();start()
        assert first==call('/reports/'+first_id) and second==call('/reports/'+second_id) and third==call('/reports/'+third['id'])
        for item in exports:
            response=client.get(base+item['download_url']);response.raise_for_status();assert hashlib.sha256(response.content).hexdigest()==item['content_hash']
        result={'status':'pass','code_fingerprint':code_fingerprint(ROOT),'http_processes':pids,'report_versions':[1,2,3],
            'business_quotes':7,'minimum_review_items':7,'public_quotes':13,'attachment_files':2,'attachment_financial_status':'unverified',
            'single_input_and_run':True,'original_versions_preserved':True,'archived_hidden_after_restart':True,
            'restore_requires_resume':True,'plan_priority_applied':True,'same_pdf_zip_after_restart':True,
            'financial_verified':114,'financial_withheld':16,'external_requests':0,'local_model_calls':research['cumulative_usage']['model_calls']-1,
            'pdf_file':str(output.relative_to(ROOT)),'pdf_pages':len(reader.pages),'source_files_preserved':len(before),'visual_review':'pending'}
        assert before==hashes(source)
        (target/'result.json').write_text(safe_payload(result));print(safe_payload({'directory':str(target.relative_to(ROOT)),**result}),flush=True)
    finally:stop();client.close()


if __name__=='__main__':main()
