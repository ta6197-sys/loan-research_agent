"""Isolated OpenDART replay of a known original-to-correction transition.

The seeded prior run is a receipt baseline only. It is never called validated.
"""
import asyncio
from datetime import date
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.finance.general_company import GeneralStartInput, receive
from agent.finance.general_collection import GeneralCollectionService
from agent.research_models import Budget, SourceInput
from agent.research_store import ResearchStore, safe_payload

DATA=ROOT/'data/research/validation_refresh_replay_20261027'
OUTPUT=ROOT/'doc/finance/SAERON_HISTORICAL_REFRESH_20261027.json'
ORIGINAL='20260813000914'
CORRECTED='20260814002314'


async def main():
    if OUTPUT.exists():
        print(safe_payload(json.loads(OUTPUT.read_text())))
        return
    load_dotenv(ROOT/'.env')
    if not os.environ.get('OPENDART_API_KEY'):
        raise ValueError('opendart_key_missing')
    store=ResearchStore(DATA/'session.db',DATA/'sources')
    initial=receive(store,GeneralStartInput(company_name='새론오토모티브',corp_code='00305668',
        stock_code='075180',public_cutoff=date(2026,8,13),
        request_key='historical-refresh-saeron-20261027',
        budget=Budget(external_requests=60,model_calls=0,writing_reserve_calls=0)))
    baseline=store.get_run(initial['run_id'])
    if baseline['status']=='queued':
        raw=(ROOT/'data/research/audits/saeron_20260813_original.zip').read_bytes()
        store.save_source(initial['run_id'],SourceInput(kind='dart',title='2026-08-13 원본 접수건 ZIP',
            publisher='금융감독원 OpenDART',receipt_number=ORIGINAL,
            published_at='2026-08-13',retrieved_at='2026-10-27T00:00:00+09:00',
            reporting_period={'operation':'historical_receipt_baseline'},
            rights_status='public_disclosure'),raw)
        GeneralCollectionService(store)._finish(initial['run_id'],{
            'target_reports':[{'rcept_no':ORIGINAL,'report_code':'11012','year':2026}],
            'fiscal_period':{'status':'supported','month':12},
            'next_phase':'non_calendar_numeric_mapping_pending',
            'financial_verification':'not_performed',
            'limitation':'원본 접수건만 재생한 이전 상태. 수치·XBRL 검증 이전 자료가 아님'})
    result=await GeneralCollectionService(store).refresh(initial['research_id'],date(2026,8,14))
    run=store.get_run(result['run_id'])
    summary={'scenario':'official_original_to_correction_refresh','research_id':initial['research_id'],
             'baseline_run_id':initial['run_id'],'baseline_financial_verification':'not_performed',
             'baseline_cutoff':'2026-08-13','refresh_cutoff':'2026-08-14',
             'refresh_run_id':result['run_id'],'refresh_status':result['status'],
             'changes':result.get('changes',[]),'numeric_batch_id':result.get('numeric_batch_id'),
             'mapping_batch_id':result.get('mapping_batch_id'),'external_requests':run['usage']['external_requests'],
             'model_calls':run['usage']['model_calls'],
             'expected_correction_receipt':CORRECTED,
             'correction_discovered':CORRECTED in {item['rcept_no'] for item in result.get('changes',[])},
             'collection_result':result.get('collection_result'),
             'limitation':'과거 기준일을 재생한 독립 개발 검증. 원래 운영 조사·게시본·코호트는 변경하지 않음'}
    content=(safe_payload(summary)+'\n').encode()
    if OUTPUT.exists() and OUTPUT.read_bytes()!=content:
        raise ValueError('immutable_result_conflict')
    if not OUTPUT.exists():OUTPUT.write_bytes(content)
    print(safe_payload({'status':summary['refresh_status'],
                        'correction_discovered':summary['correction_discovered'],
                        'external_requests':summary['external_requests'],
                        'numeric_batch_id':summary['numeric_batch_id'],
                        'mapping_batch_id':summary['mapping_batch_id']}))


if __name__=='__main__':
    asyncio.run(main())
