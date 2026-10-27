"""Resumable, bounded verification of the frozen 12-company increment."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent.research_store import ResearchStore
from sh.collect_broad_303_batch import process

PLAN=ROOT/'doc/finance/BROAD_303_INCREMENT_PLAN_20261027.json'
CANDIDATES=ROOT/'doc/finance/BROAD_303_CANDIDATES_20261027.json'
DATA=ROOT/'data/research/broad_303_increment_20261027'
PROGRESS=DATA/'progress.json'


def save(progress):
    DATA.mkdir(parents=True,exist_ok=True)
    temporary=PROGRESS.with_suffix('.tmp')
    temporary.write_text(json.dumps(progress,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n')
    temporary.replace(PROGRESS)


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--limit',type=int,default=12)
    parser.add_argument('--retry-no-data',action='store_true')
    parser.add_argument('--retry-missing-file',action='store_true')
    parser.add_argument('--retry-receipt-mismatch',action='store_true')
    args=parser.parse_args()
    if not 1<=args.limit<=12:raise ValueError('limit_must_be_1_to_12')
    load_dotenv(ROOT/'.env')
    if not os.environ.get('OPENDART_API_KEY'):raise ValueError('opendart_key_missing')
    plan=json.loads(PLAN.read_text())
    candidates={item['corp_code']:item for item in json.loads(CANDIDATES.read_text())['records']}
    store=ResearchStore(DATA/'session.db',DATA/'sources')
    progress=json.loads(PROGRESS.read_text()) if PROGRESS.exists() else {
        'schema_version':'broad-303-increment-progress-1.0',
        'plan':PLAN.name,'results':{}}
    if progress['plan']!=PLAN.name:raise ValueError('increment_plan_mismatch')
    for index,target in enumerate(plan['targets'][:args.limit],1):
        code=target['corp_code'];previous=progress['results'].get(code)
        retry_missing=(args.retry_no_data and previous and previous.get('reason')=='DartFailure:no_data'
                       and not previous.get('retry_attempted'))
        retry_receipt=(args.retry_receipt_mismatch and previous and
                       previous.get('reason')=='DartFailure:receipt_not_in_research'
                       and not previous.get('receipt_retry_attempted'))
        retry_file=(args.retry_missing_file and previous and
                    previous.get('reason')=='DartFailure:file_not_found' and
                    not previous.get('file_retry_attempted'))
        if previous and not (retry_missing or retry_receipt or retry_file):
            print(json.dumps({'index':index,'corp_code':code,'status':'already_recorded'}),flush=True)
            continue
        try:
            result=await process(store,target,candidates[code],plan['public_cutoff'],
                                 retry_no_data=bool(retry_missing or retry_receipt or retry_file))
        except Exception as error:
            result={'corp_code':code,'corp_name':target['corp_name'],
                    'status':'review_required','reason':type(error).__name__+':'+str(error)[:240],
                    'cohort_status':'not_included'}
        if retry_missing or retry_receipt or retry_file:
            result['retry_attempted']=bool(previous.get('retry_attempted') or retry_missing)
            result['receipt_retry_attempted']=bool(retry_receipt)
            result['file_retry_attempted']=bool(retry_file)
            result['previous_result']=previous
        progress['results'][code]=result;save(progress)
        print(json.dumps({'index':index,'corp_code':code,'status':result.get('cohort_status'),
                          'verified_rows':result.get('verified_rows'),
                          'reason':result.get('reason')},ensure_ascii=False),flush=True)
        if any(term in result.get('reason','').lower() for term in
               ('limit exceeded','too many requests','429','quota')):break


if __name__=='__main__':asyncio.run(main())
