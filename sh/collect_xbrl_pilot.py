"""승인된 동일 보고서 XBRL·사전 확보. 작업 재실행에도 누적 HTTP 최대20회."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from agent.finance.dart_service import DartService
from agent.research_models import ResearchInput, SourceInput, Budget
from agent.research_store import ResearchStore, now, safe_payload

TARGET=ROOT/'data/research/validation_xbrl/20261021-authorized-pilot'
PLAN=[('xbrl',{'receipt':'20260318001187','report_code':'11011'}),
      ('xbrl',{'receipt':'20260813000874','report_code':'11012'})]
PLAN += [('taxonomy',{'statement':s}) for s in ('BS1','BS2','CF3','CF4','DCIS1','DCIS2')]


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def preserve_baseline():
    paths=[ROOT/'.env',ROOT/'session.db']
    paths += [p for base in (ROOT/'data/research',ROOT/'doc/finance/cases') for p in base.rglob('*')
              if p.is_file() and not p.is_relative_to(TARGET)]
    return {str(p.relative_to(ROOT)):digest(p) for p in paths if p.is_file()}


async def main():
    load_dotenv(ROOT/'.env')
    os.environ['LANGCHAIN_TRACING_V2']='false'
    os.environ['LANGSMITH_TRACING']='false'
    if not os.environ.get('OPENDART_API_KEY'):
        print('{"status":"missing_configuration"}')
        return 1
    TARGET.mkdir(parents=True,exist_ok=True)
    manifest=TARGET/'collection.json'
    baseline=TARGET/'preservation_baseline.json'
    if not baseline.exists():DartService._atomic_write(baseline,safe_payload(preserve_baseline()).encode())
    store=ResearchStore(TARGET/'session.db',TARGET/'sources')
    if manifest.exists():
        state=json.loads(manifest.read_text())
    else:
        r=store.create_research(ResearchInput(company={'name':'상신브레이크','corp_code':'00129642','stock_code':'041650'},
            purpose='4단계 공식 개념 매핑: 승인된 XBRL·사전 대표 자료 확보',public_cutoff='2026-10-20',
            comparison_window={'annual_years':[2025],'interim_periods':['2026-H1']},
            request_key='xbrl-sangsin-20261021',budget=Budget(external_requests=20,model_calls=0,writing_reserve_calls=0)))
        state={'research_id':r['id'],'run_id':r['runs'][0]['id'],'results':[],
               'maximum_external_requests':20,'model_calls':0,'public_cutoff':'2026-10-20'}
        for year,report in [(2025,'11011'),(2026,'11012')]:
            p=ROOT/f'doc/finance/cases/sangsin_brake/raw/financial_{year}_{report}_OFS.json'
            wrapper=json.loads(p.read_text())
            receipt=wrapper['response']['list'][0]['rcept_no']
            store.save_source(state['run_id'],SourceInput(kind='dart',title=p.name,publisher='금융감독원 OpenDART',
                receipt_number=receipt,retrieved_at=wrapper['retrieved_at_utc'],
                reporting_period={'operation':'financials',**wrapper['query']},
                media_type='application/json',rights_status='public_disclosure'),p.read_bytes())
        DartService._atomic_write(manifest,safe_payload(state).encode())
    service=DartService(store,os.environ['OPENDART_API_KEY'])
    for operation,args in PLAN:
        existing=[r for r in state['results'] if r['operation']==operation and r['arguments']==args]
        if existing:continue
        result=await service.call(operation,{'run_id':state['run_id'],**args})
        state['results'].append({'operation':operation,'arguments':args,**result})
        state['external_requests']=store.get_run(state['run_id'])['usage']['external_requests']
        DartService._atomic_write(manifest,safe_payload(state).encode())
        print(json.dumps({'operation':operation,'arguments':args,'status':result['status'],
                          'external_requests':state['external_requests']},ensure_ascii=False),flush=True)
        if result['status'] in {'budget_exhausted','authentication_error','rate_limited'}:break
    # 13개 계정의 IFRS/DART 핵심 정의를 우선 확인한다. 전 DTS 자동 순회는 하지 않는다.
    from lxml import etree
    schemas=[]
    for result in state['results']:
        if result['operation']!='xbrl' or result['status']!='ok':continue
        for member in result['members']:
            if not member['name'].endswith('.xsd'):continue
            doc=etree.fromstring(store.source_content(member['source_id']),
                                etree.XMLParser(resolve_entities=False,no_network=True))
            for node in doc.findall('{http://www.w3.org/2001/XMLSchema}import'):
                uri=node.get('schemaLocation','')
                if '/full_ifrs/full_ifrs-cor_' in uri or '/dart_20' in uri:
                    schemas.append((uri,member['source_id']))
    for uri,referrer in schemas:
        args={'url':uri,'referring_source_id':referrer}
        if any(r['operation']=='taxonomy_reference' and r['arguments']==args for r in state['results']):continue
        result=await service.call('taxonomy_reference',{'run_id':state['run_id'],**args})
        state['results'].append({'operation':'taxonomy_reference','arguments':args,**result})
        state['external_requests']=store.get_run(state['run_id'])['usage']['external_requests']
        DartService._atomic_write(manifest,safe_payload(state).encode())
        print(json.dumps({'operation':'taxonomy_reference','status':result['status'],
                          'external_requests':state['external_requests']},ensure_ascii=False),flush=True)
        if result['status']=='budget_exhausted':break
    before=json.loads(baseline.read_text())
    changed=[p for p,h in before.items() if not (ROOT/p).is_file() or digest(ROOT/p)!=h]
    check={'existing_file_count':len(before),'changed_or_missing':changed,'unchanged':not changed}
    DartService._atomic_write(TARGET/'preservation_check.json',safe_payload(check).encode())
    print(json.dumps({'collection_file':str(manifest.relative_to(ROOT)),
                      'external_requests':store.get_run(state['run_id'])['usage']['external_requests'],
                      'protected_files_unchanged':not changed},ensure_ascii=False))
    return 0 if len(state['results'])>=len(PLAN) and all(r['status']=='ok' for r in state['results']) and not changed else 1


if __name__=='__main__':
    try:sys.exit(asyncio.run(main()))
    except Exception as error:
        print(json.dumps({'status':'collection_stopped','error_type':type(error).__name__}))
        sys.exit(1)
