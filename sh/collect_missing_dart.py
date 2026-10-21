"""승인된 상신브레이크 원문/이력 보완. 최대20회, 기준일2026-10-20, 모델 호출 없음."""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT/'.env')
os.environ['LANGCHAIN_TRACING_V2']='false';os.environ['LANGSMITH_TRACING']='false'
from agent.finance.runtime import create_finance_client
from agent.research_models import ResearchInput, SourceInput, Budget
from agent.research_store import ResearchStore, safe_payload, now


async def main():
    tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    target=ROOT/'data/research/validation_hardening/live'/tag;target.mkdir(parents=True)
    store=ResearchStore(target/'session.db',target/'sources')
    research=store.create_research(ResearchInput(company={'name':'상신브레이크','corp_code':'00129642','stock_code':'041650'},
        purpose='4단계 보완: 누락 원문·정정 이력·현금 연결 검증',public_cutoff='2026-10-20',
        comparison_window={'annual_years':[2023,2024,2025],'interim_periods':['2025-H1','2026-H1']},
        request_key='hardening-'+tag,budget=Budget(external_requests=20)))
    run_id=research['runs'][0]['id'];case=ROOT/'doc/finance/cases/sangsin_brake/raw';imports=[]
    for path in sorted(case.glob('financial_*.json')):
        wrapper=json.loads(path.read_text());receipt=wrapper['response']['list'][0]['rcept_no']
        source=store.save_source(run_id,SourceInput(kind='dart',title=path.name,publisher='금융감독원 OpenDART',
            receipt_number=receipt,published_at=date_string(receipt),retrieved_at=wrapper['retrieved_at_utc'],
            reporting_period={'operation':'financials',**wrapper['query']},media_type='application/json',rights_status='public_disclosure'),path.read_bytes())
        imports.append({'file':str(path.relative_to(ROOT)),'source_id':source['id'],'content_hash':source['content_hash']})
    for receipt in ('20260318001187','20250813000967','20260813000874'):
        path=case/f'{receipt}_{receipt}.xml'
        source=store.save_source(run_id,SourceInput(kind='dart',title=path.name,publisher='금융감독원 DART',
            receipt_number=receipt,published_at=date_string(receipt),retrieved_at=now(),
            reporting_period={'operation':'document_member','member_name':receipt+'.xml','imported_local_case':True},
            media_type='application/xml',rights_status='public_disclosure'),path.read_bytes())
        imports.append({'file':str(path.relative_to(ROOT)),'source_id':source['id'],'content_hash':source['content_hash']})
    client=create_finance_client(store.db_path,store.source_root);results=[];pages=[]
    async def call(tool,args):
        response=await client.call_finance(tool,{'run_id':run_id,**args})
        summary={'tool':tool,'status':response.get('status'),**{k:response[k] for k in (
            'source_id','receipt_number','members','page_no','total_page','total_count') if k in response}}
        results.append(summary);print(json.dumps(summary,ensure_ascii=False),flush=True)
        return response
    await client.start()
    try:
        page=1;total=1
        while page<=total:
            response=await call('dart_disclosures',{'corp_code':'00129642','begin':'2023-01-01','end':'2026-10-20',
                                                   'page_no':page,'page_count':100,'last_report':'N'})
            if response.get('status')!='ok':break
            pages.append(response);total=int(response['total_page']);page+=1
            if total>20:break
        complete=bool(pages) and len(pages)==total
        if complete:
            available={r['rcept_no'] for p in pages for r in p['items']}
            for receipt in ('20240315000633','20250314001195','20240813000864'):
                if receipt in available:await call('dart_document',{'receipt':receipt})
                else:results.append({'tool':'dart_document','receipt_number':receipt,'status':'not_in_complete_history'})
            for scope in ('OFS','CFS'):
                await call('dart_financials',{'corp_code':'00129642','year':2024,'report_code':'11012','scope':scope})
    finally:await client.stop()
    output={'run_id':run_id,'research_id':research['id'],'results':results,'imports':imports,
            'history_pages_complete':complete,'external_requests':store.get_run(run_id)['usage']['external_requests'],
            'maximum_external_requests':20,'model_calls':0,'public_cutoff':'2026-10-20'}
    (target/'collection.json').write_text(safe_payload(output))
    print(json.dumps({'collection_file':str((target/'collection.json').relative_to(ROOT)),
        'external_requests':output['external_requests'],'history_pages_complete':complete},ensure_ascii=False),flush=True)
    return 0 if complete and all(r['status']=='ok' for r in results) else 1


def date_string(receipt):return receipt[:4]+'-'+receipt[4:6]+'-'+receipt[6:8]

if __name__=='__main__':
    try:sys.exit(asyncio.run(main()))
    except Exception as error:
        print(json.dumps({'error_type':type(error).__name__,'message':'추가 조회 중단. 인증 정보와 예외 전문은 출력하지 않습니다.'},ensure_ascii=False))
        sys.exit(1)
