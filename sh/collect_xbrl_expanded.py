"""추가 승인된 과거 XBRL4개 수집. 별도 누적 최대20회, 키/원본 보존."""
import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from dotenv import load_dotenv
from agent.finance.dart_service import DartService
from agent.research_models import ResearchInput,SourceInput,Budget
from agent.research_store import ResearchStore,safe_payload,now

TARGET=ROOT/'data/research/validation_xbrl/20261021-expanded-periods'
ORIGINAL=ROOT/'data/research/validation_hardening/live/20261020T233956Z-db52d0dc'
PILOT=ROOT/'data/research/validation_xbrl/20261021-authorized-pilot'
PLAN=[('20240315000633','11011'),('20250314001195','11011'),
      ('20240813000864','11012'),('20250813000967','11012')]


def readonly_sources(base):
    with closing(sqlite3.connect((base/'session.db').resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.row_factory=sqlite3.Row
        rows=[dict(r) for r in conn.execute('SELECT * FROM research_sources')]
    output=[]
    for row in rows:
        meta=json.loads(row['metadata_json'])
        raw=(base/'sources'/row['storage_key']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=row['content_hash']:raise ValueError('original_source_hash_mismatch')
        output.append((meta,raw,row['content_hash']))
    return output


async def main():
    load_dotenv(ROOT/'.env')
    os.environ['LANGCHAIN_TRACING_V2']='false';os.environ['LANGSMITH_TRACING']='false'
    if not os.environ.get('OPENDART_API_KEY'):
        print('{"status":"missing_configuration"}');return 1
    TARGET.mkdir(parents=True,exist_ok=True)
    store=ResearchStore(TARGET/'session.db',TARGET/'sources')
    path=TARGET/'collection.json'
    original_sources=readonly_sources(ORIGINAL)
    cached_references={m['reporting_period']['declared_uri']:(m,raw,h) for m,raw,h in readonly_sources(PILOT)
                       if m['reporting_period'].get('operation')=='taxonomy_reference'}
    if path.exists():state=json.loads(path.read_text())
    else:
        r=store.create_research(ResearchInput(company={'name':'상신브레이크','corp_code':'00129642','stock_code':'041650'},
            purpose='4단계 잔여 검증: 승인된 과거 XBRL·공식 대응 근거 추가 확보',
            public_cutoff='2026-10-20',comparison_window={'annual_years':[2023,2024],'interim_periods':['2024-H1','2025-H1']},
            request_key='xbrl-expanded-20261021',budget=Budget(external_requests=20,model_calls=0,writing_reserve_calls=0)))
        state={'research_id':r['id'],'run_id':r['runs'][0]['id'],'results':[],'imports':[],
               'maximum_external_requests':20,'previous_pilot_requests':12,'model_calls':0}
        for receipt,report in PLAN:
            matches=[]
            for meta,raw,h in original_sources:
                if meta['reporting_period'].get('operation')!='financials':continue
                data=json.loads(raw);rows=data.get('response',data).get('list',[])
                if (rows and rows[0].get('rcept_no')==receipt and rows[0].get('reprt_code')==report
                        and meta['reporting_period'].get('fs_div')=='OFS'):matches.append((meta,raw,h))
            if len(matches)!=1:raise ValueError('unique_prior_receipt_evidence_required')
            meta,raw,h=matches[0];source=store.save_source(state['run_id'],SourceInput(**meta),raw)
            state['imports'].append({'receipt':receipt,'source_id':source['id'],'content_hash':h})
        DartService._atomic_write(path,safe_payload(state).encode())
    service=DartService(store,os.environ['OPENDART_API_KEY'])
    async def record(operation,args):
        existing=next((x for x in state['results'] if x['operation']==operation and x['arguments']==args),None)
        if existing:return existing
        result=await service.call(operation,{'run_id':state['run_id'],**args})
        item={'operation':operation,'arguments':args,**result}
        state['results'].append(item)
        state['external_requests']=store.get_run(state['run_id'])['usage']['external_requests']
        DartService._atomic_write(path,safe_payload(state).encode())
        print(json.dumps({'operation':operation,'status':result['status'],'external_requests':state['external_requests']},ensure_ascii=False),flush=True)
        return item
    for receipt,report in PLAN:
        await record('xbrl',{'receipt':receipt,'report_code':report})
    from lxml import etree
    archives=list(state['results'])
    for result in archives:
        if result['operation']!='xbrl' or result['status']!='ok':continue
        for member in result['members']:
            if not member['name'].endswith('.xsd'):continue
            doc=etree.fromstring(store.source_content(member['source_id']),etree.XMLParser(resolve_entities=False,no_network=True))
            for node in doc.findall('{http://www.w3.org/2001/XMLSchema}import'):
                uri=node.get('schemaLocation','')
                if '/full_ifrs/full_ifrs-cor_' not in uri and '/dart_20' not in uri:continue
                args={'url':uri,'referring_source_id':member['source_id']}
                if any(x['operation']=='taxonomy_reference' and x['arguments']==args for x in state['results']):continue
                if uri in cached_references:
                    meta,raw,h=cached_references[uri]
                    updated=dict(meta);updated['reporting_period']={**meta['reporting_period'],'referring_source_id':member['source_id']}
                    source=store.save_source(state['run_id'],SourceInput(**updated),raw)
                    state['results'].append({'operation':'taxonomy_reference','arguments':args,'status':'ok',
                        'source_id':source['id'],'declared_uri':uri,'content_hash':h,'reused_cached_bytes':True})
                    DartService._atomic_write(path,safe_payload(state).encode())
                else:
                    item=await record('taxonomy_reference',args)
                    if item['status']=='ok':
                        source=store.get_source(item['source_id'])
                        cached_references[uri]=(source['metadata'],store.source_content(item['source_id']),source['content_hash'])
    state['external_requests']=store.get_run(state['run_id'])['usage']['external_requests']
    DartService._atomic_write(path,safe_payload(state).encode())
    print(json.dumps({'collection':str(path.relative_to(ROOT)),'new_external_requests':state['external_requests'],
                      'previous_pilot_requests':12},ensure_ascii=False))
    return 0 if all(x['status']=='ok' for x in state['results']) else 1


if __name__=='__main__':
    try:sys.exit(asyncio.run(main()))
    except Exception as error:
        print(json.dumps({'status':'collection_stopped','error_type':type(error).__name__}))
        sys.exit(1)
