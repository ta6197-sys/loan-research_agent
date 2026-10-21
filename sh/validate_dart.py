"""2단계 실제 연결 확인. 명시적으로 실행할 때만 OpenDART에 요청한다.

공개 상신브레이크 대표 자료를 새 검증 디렉터리에 저장한다. 기존 사례/DB/키는 변경하지 않는다.
"""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')
os.environ['LANGCHAIN_TRACING_V2']='false'
os.environ['LANGSMITH_TRACING']='false'
from agent.finance.runtime import create_finance_client
from agent.research_models import ResearchInput
from agent.research_store import ResearchStore, safe_payload


async def main():
    tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    directory=ROOT/'data/research/validation_stage02'/tag
    directory.mkdir(parents=True)
    store=ResearchStore(directory/'session.db',directory/'sources')
    research=store.create_research(ResearchInput(
        company={'name':'상신브레이크','corp_code':'00129642','stock_code':'041650'},
        purpose='2단계 DART 연결 확인: 공개 자료를 활용한 가상 운전자금 사전조사',
        public_cutoff='2026-10-20',comparison_window={'annual_years':[2023,2024,2025],
            'interim_periods':['2025-H1','2026-H1'],'balance_comparison':'previous_year_end',
            'flow_comparison':'same_period'},request_key='live-stage02-'+tag))
    run_id=research['runs'][0]['id']
    client=create_finance_client(store.db_path,store.source_root)
    results=[]
    async def call(name,arguments):
        result=await client.call_finance(name,arguments)
        summary={'tool':name, 'status':result.get('status'),
                 **{key:result[key] for key in ('source_id','row_count','receipt_number','complete',
                     'catalogue_updated_at','stale','financial_verification') if key in result}}
        if name=='search_companies':
            summary['candidate_match']=any(row.get('corp_code')=='00129642' for row in result.get('items',[]))
        if name=='dart_company':
            summary['company_match']=result.get('company',{}).get('corp_code')=='00129642'
        if 'members' in result:
            summary['members']=result['members']
        results.append(summary)
        print(json.dumps(summary,ensure_ascii=False),flush=True)
        return result
    try:
        await client.start()
        await call('search_companies',{'query':'상신브레이크'})
        common={'run_id':run_id,'corp_code':'00129642'}
        await call('dart_company',common)
        disclosures=await call('dart_disclosures',{**common,'begin':'2026-08-01','end':'2026-10-20'})
        ofs=await call('dart_financials',{**common,'year':2026,'report_code':'11012','scope':'OFS'})
        await call('dart_financials',{**common,'year':2026,'report_code':'11012','scope':'CFS'})
        receipt=ofs.get('receipt_number')
        if not receipt:
            receipt=next((row['rcept_no'] for row in disclosures.get('items',[])
                          if row.get('rcept_no')=='20260813000874'),None)
        if receipt:
            await call('dart_document',{'run_id':run_id,'receipt':receipt})
    finally:
        await client.stop()
    run=store.get_run(run_id)
    summary={'research_id':research['id'],'run_id':run_id,'results':results,
             'investigation_external_requests':run['usage']['external_requests'],
             'investigation_execution_seconds':run['usage']['execution_seconds'],
             'financial_verification':'not_performed',
             'all_representative_tools_ok':len(results)==6 and all(r['status']=='ok' for r in results)
                 and results[0].get('candidate_match') is True and results[1].get('company_match') is True}
    catalogue_log=store.source_root/'_catalogue/requests.jsonl'
    summary['catalogue_external_requests']=len(catalogue_log.read_text().splitlines()) if catalogue_log.exists() else 0
    (directory/'result.json').write_text(safe_payload(summary),encoding='utf-8')
    print(json.dumps({'result_file':str((directory/'result.json').relative_to(ROOT)),
                      'all_representative_tools_ok':summary['all_representative_tools_ok'],
                      'investigation_external_requests':summary['investigation_external_requests'],
                      'catalogue_external_requests':summary['catalogue_external_requests']},ensure_ascii=False))
    return 0 if summary['all_representative_tools_ok'] else 1


if __name__=='__main__':
    try:
        sys.exit(asyncio.run(main()))
    except Exception as error:
        # 인증 URL 등이 포함될 수 있는 예외 전문/traceback을 출력하지 않는다.
        print(json.dumps({'validation_error_type':type(error).__name__,
                          'message':'연결 시험이 중단됐습니다. 인증 주소/예외 전문은 출력하지 않습니다.'},ensure_ascii=False))
        sys.exit(1)
