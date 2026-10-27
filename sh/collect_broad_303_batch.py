"""Bounded, resumable OpenDART verification in an isolated cohort database.

Each target is a new research with a 60 request cap and no model calls.
Failures stay explicit; no candidate is marked verified just because it was fetched.
"""
import argparse
import asyncio
from datetime import date
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.finance.general_company import GeneralStartInput, receive, create_collection_retry
from agent.finance.general_collection import GeneralCollectionService
from agent.finance.numeric_validation import NumericValidationService
from agent.finance.xbrl_mapping import XbrlMappingService
from agent.finance.verified_run_peer import build_run
from agent.research_api import _run_verified_analysis_payload
from agent.research_models import Budget
from agent.research_store import ResearchStore

PLAN=ROOT/'doc/finance/BROAD_303_COLLECTION_PLAN_20261027.json'
AUDIT=ROOT/'doc/finance/BROAD_303_CANDIDATES_20261027.json'
DATA=ROOT/'data/research/broad_303_20261027'
PROGRESS=DATA/'progress.json'


def save_progress(progress):
    DATA.mkdir(parents=True,exist_ok=True)
    temp=PROGRESS.with_suffix('.tmp')
    temp.write_text(json.dumps(progress,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n')
    temp.replace(PROGRESS)


def existing_batch(store,run_id,prefix):
    history=NumericValidationService(store).history(run_id)
    matches=[item['id'] for item in history if item['policy_version'].startswith(prefix)]
    if len(matches)>1:raise ValueError('duplicate_validation_batch')
    return matches[0] if matches else None


async def process(store,target,candidate,cutoff,*,retry_no_data=False):
    code=target['corp_code']
    request=GeneralStartInput(company_name=target['corp_name'],corp_code=code,
        stock_code=target.get('stock_code'),public_cutoff=date.fromisoformat(cutoff),
        request_key='broad-303-20261027:'+code,
        budget=Budget(external_requests=60,model_calls=0,writing_reserve_calls=0,
                      execution_seconds=1800))
    receipt=receive(store,request);run_id=receipt['run_id']
    run=store.get_run(run_id)
    if retry_no_data and run['status']=='failed':
        used=store.get_research(receipt['research_id'])['cumulative_usage']['external_requests']
        remaining=60-used
        if remaining<=0:raise ValueError('fixed_company_external_request_cap_exhausted')
        retry=create_collection_retry(store,receipt['research_id'],
                                      external_request_budget=remaining)
        run_id=retry['run_id'];run=store.get_run(run_id)
    if run['status']=='queued':
        collection=await GeneralCollectionService(store).run(run_id)
    else:
        collection=run['trigger'].get('collection_result',{})
    run=store.get_run(run_id)
    result={'corp_code':code,'corp_name':target['corp_name'],'run_id':run_id,
            'research_id':receipt['research_id'],'run_status':run['status'],
            'external_requests':run['usage']['external_requests'],
            'model_calls':run['usage']['model_calls'],
            'collection_next_phase':collection.get('next_phase'),
            'financial_verification':'not_performed',
            'cohort_status':'not_included'}
    if run['status']!='finished' or collection.get('next_phase')!='numeric_and_xbrl_validation':
        result['reason']='collection_or_fiscal_period_not_ready'
        return result
    numeric_id=existing_batch(store,run_id,'numeric-validation-')
    numeric=NumericValidationService(store).batch(numeric_id) if numeric_id else NumericValidationService(store).validate_run(run_id)
    mapping_id=existing_batch(store,run_id,'xbrl-mapping-')
    mapping=XbrlMappingService(store).batch(mapping_id) if mapping_id else XbrlMappingService(store).validate_run(run_id)
    result.update(numeric_batch_id=numeric['id'],mapping_batch_id=mapping['id'])
    if store.get_run(run_id)['usage']['external_requests']!=result['external_requests']:
        raise ValueError('validation_used_external_request')
    preview=_run_verified_analysis_payload(store,run_id)
    result.update(financial_verification='numeric_and_xbrl_checked',
                  verified_rows=preview['row_counts']['verified'],
                  withheld_rows=preview['row_counts']['withheld'],
                  xbrl_gate=preview['xbrl_gate']['status'],
                  disclosure_history=preview['disclosure_history']['status'])
    peer=build_run(store,run_id,candidate)
    result.update(cohort_status='exploratory_verified_candidate' if peer['records'] else 'withheld',
                  eligible_observations=len(peer['records']),peer_exclusions=peer['excluded'])
    return result


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--limit',type=int,default=24)
    parser.add_argument('--retry-no-data',action='store_true')
    parser.add_argument('--retry-receipt-mismatch',action='store_true')
    args=parser.parse_args()
    if not 1<=args.limit<=24:raise ValueError('limit_must_be_1_to_24')
    load_dotenv(ROOT/'.env')
    if not os.environ.get('OPENDART_API_KEY'):raise ValueError('opendart_key_missing')
    plan=json.loads(PLAN.read_text())
    candidates={item['corp_code']:item for item in json.loads(AUDIT.read_text())['records']}
    store=ResearchStore(DATA/'session.db',DATA/'sources')
    progress=json.loads(PROGRESS.read_text()) if PROGRESS.exists() else {
        'schema_version':'broad-303-collection-progress-1.0',
        'plan':'BROAD_303_COLLECTION_PLAN_20261027.json','results':{}}
    for index,target in enumerate(plan['targets'][:args.limit],1):
        code=target['corp_code']
        previous=progress['results'].get(code)
        retry_missing = (args.retry_no_data and previous and
                         previous.get('reason')=='DartFailure:no_data' and
                         not previous.get('retry_attempted'))
        retry_receipt = (args.retry_receipt_mismatch and previous and
                         previous.get('reason')=='DartFailure:receipt_not_in_research' and
                         not previous.get('receipt_retry_attempted'))
        if previous and not (retry_missing or retry_receipt):
            print(json.dumps({'index':index,'corp_code':code,'status':'already_recorded'},ensure_ascii=False),flush=True)
            continue
        try:
            result=await process(store,target,candidates[code],plan['public_cutoff'],
                                 retry_no_data=bool(retry_missing or retry_receipt))
        except Exception as error:
            result={'corp_code':code,'corp_name':target['corp_name'],'status':'review_required',
                    'reason':type(error).__name__+':'+str(error)[:240],
                    'cohort_status':'not_included'}
        if retry_missing or retry_receipt:
            result['retry_attempted']=bool(previous.get('retry_attempted') or retry_missing)
            result['receipt_retry_attempted']=bool(retry_receipt)
            result['previous_result']=previous
        progress['results'][code]=result
        save_progress(progress)
        print(json.dumps({'index':index,'corp_code':code,'status':result.get('cohort_status'),
                          'verified_rows':result.get('verified_rows'),
                          'external_requests':result.get('external_requests'),
                          'reason':result.get('reason')},ensure_ascii=False),flush=True)
        if any(term in result.get('reason','').lower() for term in ('limit exceeded','too many requests','429','quota')):
            break


if __name__=='__main__':asyncio.run(main())
