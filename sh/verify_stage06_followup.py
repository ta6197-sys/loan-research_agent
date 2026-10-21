"""원본을 보존한 새 분석 복사본에서 합성 답변·정정→실모델→게시판 갱신을 검증한다."""
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
from agent.finance.research_followup import FollowupInput,FollowupService
from agent.finance.local_model import checked_draft
from agent.research_store import ResearchStore,safe_payload


def fingerprints(directory):
    return {str(p.relative_to(directory)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.rglob('*') if p.is_file() and not p.is_symlink()}


async def main():
    source=Path(sys.argv[1]).resolve()
    if not source.is_relative_to(ROOT/'data/research/validation_stage05'):raise ValueError('stage05_analysis_required')
    before=fingerprints(source)
    tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    target=ROOT/'data/research/validation_stage06'/tag;target.mkdir(parents=True)
    (target/'source_fingerprint_before.json').write_text(safe_payload(before))
    with closing(sqlite3.connect((source/'session.db').as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target/'session.db')) as dst:src.backup(dst)
    shutil.copytree(source/'sources',target/'sources')
    store=ResearchStore(target/'session.db',target/'sources');service=FollowupService(store)
    with store.connection() as conn:
        rid=conn.execute('SELECT research_id FROM research_reports ORDER BY version DESC LIMIT 1').fetchone()['research_id']
    previous=store.get_research(rid);old_reports={r['id']:safe_payload(store.get_report(r['id'])) for r in previous['reports']}
    first=previous['reports'][-1];first_context=first['content']['context']
    for report in previous['reports']:(target/('report-v'+str(report['version'])+'.json')).write_text(safe_payload(report))
    receipts=[];results=[]
    try:
        for key,kind,text,issue in (
            ('synthetic-answer','answer','합성 시험 답변입니다. 거래처별 회수 일정과 원리금 상환표는 아직 제공하지 못했습니다. 실제 회사 내부자료가 아닙니다.','U_repayment'),
            ('synthetic-correction','correction','합성 시험 정정 요청입니다. 이전 답변으로 상환재원이 확인됐다고 해석하지 말고 차입 명세·만기 원문을 다시 확인해주세요. 실제 회사 내부자료가 아닙니다.','U_borrowing')):
            parent=store.get_research(rid)['reports'][-1]
            value=FollowupInput(parent_report_id=parent['id'],request_key=key,kind=kind,text=text,question_ids=[issue])
            receipt=service.receive(rid,value);receipts.append(receipt)
            again=service.receive(rid,value)
            assert receipt==again
            await service.drain()
            report=store.get_research(rid)['reports'][-1]
            context=report['content']['context']
            assert report['id']!=parent['id'] and report['version']==parent['version']+1
            assert context['parent_report_id']==parent['id']
            assert context['financial_rows']==first_context['financial_rows']
            assert context['charts']==first_context['charts']
            assert all(u['state']=='withheld' for u in context['unknowns'])
            assert report['content']['confirmed_facts']==first['content']['confirmed_facts']
            if report['content']['model_status']=='validated':
                checked_draft(report['content']['model']['draft'],context['model_evidence'])
            (target/('report-v'+str(report['version'])+'.json')).write_text(safe_payload(report))
            run=store.get_run(receipt['run_id']);assert run['usage']['external_requests']==0
            results.append({'report_id':report['id'],'version':report['version'],'run_id':run['id'],
                'model_status':report['content']['model_status'],'usage':run['usage'],
                'affected_sections':context['affected_sections'],'unknowns_preserved':True,'table_chart_unchanged':True})
        calls_before=store.get_research(rid)['cumulative_usage']
        memo=service.receive(rid,FollowupInput(parent_report_id=report['id'],request_key='synthetic-memo',kind='memo',text='합성 시험 메모: 조사에 반영하지 않습니다.'))
        await service.drain()
        assert memo['run_id'] is None and store.get_research(rid)['cumulative_usage']==calls_before
        assert all(safe_payload(store.get_report(identifier))==content for identifier,content in old_reports.items())
        assert fingerprints(source)==before
        summary={'scope':'stored_sangsin_case_synthetic_followup_test','source_directory':str(source.relative_to(ROOT)),
            'research_id':rid,'receipts':receipts,'reports':results,'memo_stored_only':True,
            'prior_report_versions_preserved':len(old_reports),'source_files_preserved':len(before),
            'source_files_changed':0,'source_files_deleted':0,'external_requests':0,'external_trace':False,
            'financial_confirmation':'incomplete','full_stage06_complete':False,
            'cumulative_usage':store.get_research(rid)['cumulative_usage']}
        (target/'result.json').write_text(safe_payload(summary))
        print(json.dumps({'result_file':str((target/'result.json').relative_to(ROOT)),**summary},ensure_ascii=False),flush=True)
        return 0 if all(r['model_status']=='validated' for r in results) else 1
    finally:await service.close()


if __name__=='__main__':
    try:sys.exit(asyncio.run(main()))
    except Exception as error:
        print(json.dumps({'status':'stage06_test_stopped','error_type':type(error).__name__}),flush=True);sys.exit(1)
