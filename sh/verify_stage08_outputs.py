"""分析 복사본에서 같은 게시본의 화면/출력과 실제 HTTP 다운로드를 검증한다."""
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
from agent.research_store import ResearchStore,safe_payload
from agent.finance.report_view import build_view,compare_views
from agent.finance.report_exports import ReportExports


def main():
    source=Path(sys.argv[1]).resolve()
    if not any(source.is_relative_to(ROOT/'data/research'/folder) for folder in ('validation_stage06','validation_stage08')):raise ValueError('analysis_required')
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()}
    tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    target=ROOT/'data/research/validation_stage08'/tag;target.mkdir(parents=True)
    with closing(sqlite3.connect((source/'session.db').as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target/'session.db')) as dst:src.backup(dst)
    shutil.copytree(source/'sources',target/'sources')
    store=ResearchStore(target/'session.db',target/'sources')
    with store.connection() as conn:ids=[r[0] for r in conn.execute('SELECT id FROM research_reports ORDER BY version')]
    if len(sys.argv)>2:
        with store.connection() as conn:report_id=conn.execute('SELECT id FROM research_reports WHERE version=?',(int(sys.argv[2]),)).fetchone()[0]
    else:report_id=ids[-1]
    previous_id=ids[ids.index(report_id)-1]
    view=build_view(store,report_id);service=ReportExports(store)
    pdf=service.create(report_id,'pdf');bundle=service.create(report_id,'markdown_zip')
    assert pdf['view_hash']==bundle['view_hash']==view['view_hash']
    _,pdf_bytes=service.download(pdf['id']);reader=PdfReader(io.BytesIO(pdf_bytes));all_text='\n'.join(p.extract_text() for p in reader.pages)
    assert '상신브레이크' in all_text and '미확인' in all_text and '사용자 제공 미검증 입력' in all_text
    from decimal import Decimal
    for row in view['financial_rows']:
        if row['status']=='verified':assert format(Decimal(row['value']),',f') in all_text
    _,zip_bytes=service.download(bundle['id'])
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        manifest=json.loads(archive.read('manifest.json'));assert manifest['view_hash']==view['view_hash']
        assert not manifest['raw_sources_included'] and not manifest['user_attachments_included']
        for name in archive.namelist():assert '..' not in Path(name).parts and not name.startswith('/')
        md=archive.read('report.md').decode();assert str(ROOT) not in md
        assert len([p for p in archive.namelist() if p.endswith('.svg')])==2
    comparison=compare_views(build_view(store,previous_id),view)
    assert comparison['financial_changes']==[] and comparison['added_user_input_count']>=1
    if view.get('business_review'):
        assert len(view['business_review'])==7 and all(i.get('quote') for i in view['business_review'])
        assert len(view['minimum_review'])==7
    output=ROOT/'output/pdf';output.mkdir(parents=True,exist_ok=True)
    artifact=output/('sangsin-brake-v'+str(view['version'])+'-'+tag+'.pdf');artifact.write_bytes(pdf_bytes)
    (target/'view.json').write_text(safe_payload(view));(target/'report.zip').write_bytes(zip_bytes)
    processes=[]
    env=os.environ.copy();env.update(APP_MODE='research',APP_DB_PATH=str(target/'session.db'),APP_SOURCE_ROOT=str(target/'sources'),
        OPENDART_API_KEY='',LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false',APP_ALLOW_EXTERNAL_MODEL='false',APP_ALLOW_EXTERNAL_TRACE='false')
    process=None
    try:
        for _ in range(2):
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            log=(target/('http-'+str(len(processes))+'.log')).open('w')
            process=subprocess.Popen([sys.executable,'-m','uvicorn','server:app','--host','127.0.0.1','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log);log.close()
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=60) as client:
                for i in range(200):
                    try:
                        r=client.get('/health')
                        if r.status_code==200:break
                    except httpx.HTTPError:pass
                    if process.poll() is not None:raise ValueError('server_failed')
                    time.sleep(.05)
                else:raise TimeoutError('startup')
                r=client.get('/reports/'+report_id+'/view');r.raise_for_status();assert r.json()==view
                for kind,expected,raw in [('pdf',pdf,pdf_bytes),('markdown_zip',bundle,zip_bytes)]:
                    r=client.post('/reports/'+report_id+'/exports',json={'format':kind});r.raise_for_status()
                    assert r.json()==expected
                    downloaded=client.get(r.json()['download_url']);downloaded.raise_for_status();assert downloaded.content==raw
                r=client.get('/reports/'+report_id+'/compare/'+previous_id);r.raise_for_status();assert r.json()==comparison
                processes.append(process.pid)
            process.terminate();process.wait(timeout=15);process=None
    finally:
        if process:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
    assert all(Path(p).exists() and hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in before.items())
    result={'status':'pass','scope':'stored_case_report_view_and_exports','analysis_directory':str(target.relative_to(ROOT)),
        'report_id':report_id,'version':view['version'],'view_hash':view['view_hash'],'report_hash':view['report_hash'],
        'pdf_file':str(artifact.relative_to(ROOT)),'pdf_pages':len(reader.pages),'pdf_bytes':len(pdf_bytes),
        'zip_file':str((target/'report.zip').relative_to(ROOT)),'screen_pdf_zip_same_version':True,
        'verified_values_preserved':sum(r['status']=='verified' for r in view['financial_rows']),
        'withheld_values_preserved':sum(r['status']=='withheld' for r in view['financial_rows']),
        'http_processes':processes,'raw_sources_included':False,'original_file_count':len(before),'original_changed':0,'original_deleted':0,
        'model_calls':0,'external_requests':0,'external_trace':False,'financial_confirmation':'incomplete','visual_review':'pending'}
    (target/'result.json').write_text(safe_payload(result));print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
