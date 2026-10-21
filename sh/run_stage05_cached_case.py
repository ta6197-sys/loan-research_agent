"""보관 사례의 분석 복사본에서 5단계 일곱 항목·로컬 초안·버전 게시를 실행한다."""
import asyncio
from contextlib import closing
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ['LANGCHAIN_TRACING_V2']='false';os.environ['LANGSMITH_TRACING']='false'
os.environ['APP_ALLOW_EXTERNAL_TRACE']='false';os.environ['APP_ALLOW_EXTERNAL_MODEL']='false'
os.environ['OPENDART_API_KEY']=''
from agent.finance.research_stage05 import Stage05Service
from agent.research_store import ResearchStore,safe_payload


async def main():
    existing=len(sys.argv)>1 and sys.argv[1]=='--existing'
    source=(Path(sys.argv[3]).resolve() if existing else Path(sys.argv[1]).resolve() if len(sys.argv)>1
            else sorted((ROOT/'data/research/validation_stage04_integrated').glob('*/result.json'))[-1].parent)
    result=json.loads((source/'result.json').read_text())
    if result['acceptance']['exit_code']!=0:raise ValueError('stage04_regression_not_passed')
    tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    target=Path(sys.argv[2]).resolve() if existing else ROOT/'data/research/validation_stage05'/tag
    if existing and not target.is_relative_to(ROOT/'data/research/validation_stage05'):raise ValueError('analysis_copy_required')
    if not existing:target.mkdir(parents=True)
    before=hashlib.sha256((source/'session.db').read_bytes()).hexdigest()
    old_reports={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in target.glob('report*.json')}
    if not existing:
        with closing(sqlite3.connect((source/'session.db').as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target/'session.db')) as dst:src.backup(dst)
        shutil.copytree(source/'sources',target/'sources')
    store=ResearchStore(target/'session.db',target/'sources')
    key=sys.argv[4] if existing and len(sys.argv)>4 else 'stage05-question-completion' if existing else 'stage05-initial'
    report=await Stage05Service(store).run(result['run_id'],key)
    suffix='-v'+str(report['version']) if existing else ''
    report_file=target/('report'+suffix+'.json')
    text=safe_payload(report)
    if report_file.exists() and report_file.read_text()!=text:raise ValueError('existing_report_artifact_preserved')
    report_file.write_text(text)
    chart_dir=target/('charts'+suffix);chart_dir.mkdir(exist_ok=True)
    for chart in report['content']['context']['charts']:
        if chart.get('svg'):(chart_dir/(chart['id']+'.svg')).write_text(chart['svg'])
    unchanged=hashlib.sha256((source/'session.db').read_bytes()).hexdigest()==before
    output={'run_id':result['run_id'],'report_id':report['id'],'report_version':report['version'],
        'status':report['status'],'model_status':report['content']['model_status'],
        'sections':len(report['content']['context']['sections']),
        'financial_rows':len(report['content']['context']['financial_rows']),
        'model_usage':store.get_run(result['run_id'])['usage'],
        'external_requests_during_stage05':0,'external_trace':False,'source_database_unchanged':unchanged,
        'source_directory':str(source.relative_to(ROOT)),
        'previous_report_files_preserved':all(p.exists() and hashlib.sha256(p.read_bytes()).hexdigest()==h for p,h in old_reports.items()),
        'scope':'stored_sangsin_case_only','full_stage05_complete':False}
    result_file=target/('result'+suffix+'.json')
    if result_file.exists() and result_file.read_text()!=safe_payload(output):raise ValueError('existing_result_artifact_preserved')
    result_file.write_text(safe_payload(output))
    print(json.dumps({'result_file':str(result_file.relative_to(ROOT)),**output},ensure_ascii=False),flush=True)
    return 0 if unchanged and report['content']['model_status']=='validated' else 1


if __name__=='__main__':
    try:sys.exit(asyncio.run(main()))
    except Exception as error:
        print(json.dumps({'status':'stage05_stopped','error_type':type(error).__name__}),flush=True);sys.exit(1)
