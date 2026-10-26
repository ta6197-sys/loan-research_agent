"""신규 30392 후보만 정기공시 존재 여부를 확인한다."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from dotenv import load_dotenv
from agent.finance.dart_protocol import DartFailure,DartHTTP
from agent.finance.dart_service import DartService
from agent.finance.general_collection import report_code,report_month
from agent.research_store import safe_payload

INPUT=ROOT/'doc/finance/KSIC30392_OFFICIAL_CANDIDATES_20261025.json'
RAW=ROOT/'data/research/cohort_official_30392_20261026/disclosures'
OUTPUT=ROOT/'doc/finance/KSIC30392_OFFICIAL_DISCLOSURES_20261026.json'
CUTOFF='2026-10-26'


async def main():
    load_dotenv(ROOT/'.env')
    key=os.environ.get('OPENDART_API_KEY')
    if not key:
        print('{"status":"missing_configuration"}')
        return 1
    candidates=json.loads(INPUT.read_text(encoding='utf-8'))['records']
    http=DartHTTP(key,max_response_bytes=4*1024*1024)
    RAW.mkdir(parents=True,exist_ok=True)
    records=[]
    for candidate in candidates:
        code=candidate['corp_code'];path=RAW/(code+'.json')
        params={'corp_code':code,'bgn_de':'2023-01-01','end_de':CUTOFF,
                'last_reprt_at':'Y','page_no':1,'page_count':100}
        try:
            if path.exists():
                content=path.read_bytes();response=DartHTTP._decode(content,'disclosures')
            else:
                response=await http.request('disclosures',params,before_request=lambda: asyncio.sleep(0))
                content=response.content;DartService._atomic_write(path,content)
        except DartFailure as error:
            records.append({'corp_code':code,'corp_name':candidate['corp_name'],
                            'status':error.kind,'eligible_report_count':0})
            if error.kind in {'rate_limited','authentication_error','access_denied'}:break
            continue
        data=response.data
        try:pages=int(data.get('total_page') or 1)
        except (TypeError,ValueError):pages=0
        if pages!=1:
            records.append({'corp_code':code,'corp_name':candidate['corp_name'],
                            'status':'pagination_review_required','page_count':pages,
                            'source_sha256':hashlib.sha256(content).hexdigest()})
            continue
        reports=[]
        for item in data.get('list',[]):
            name=item.get('report_nm','');receipt=item.get('rcept_no','')
            if (report_code(name) in {'11011','11012'} and len(receipt)==14
                    and receipt[:8]<=CUTOFF.replace('-','')):
                reports.append({'report_nm':name,'report_code':report_code(name),
                                'report_month':report_month(name),'rcept_no':receipt})
        records.append({'corp_code':code,'corp_name':candidate['corp_name'],
                        'status':'reports_found' if reports else 'no_annual_or_half_report',
                        'eligible_report_count':len(reports),'reports':sorted(reports,key=lambda x:x['rcept_no']),
                        'source_sha256':hashlib.sha256(content).hexdigest()})
    output={'status':'candidate_report_discovery_only','as_of':CUTOFF,
            'candidate_source_sha256':hashlib.sha256(INPUT.read_bytes()).hexdigest(),
            'checked_count':len(records),'records':records,
            'limitation':'공시목록은 원문·XBRL·재무 수치 검증 또는 코호트 편입 근거가 아니다.'}
    DartService._atomic_write(OUTPUT,safe_payload(output).encode())
    print(safe_payload({'checked':len(records),'with_reports':sum(x['status']=='reports_found' for x in records),
                        'errors':sum(x['status'] not in {'reports_found','no_annual_or_half_report'} for x in records)}))
    return 0 if len(records)==len(candidates) else 1


if __name__=='__main__':
    raise SystemExit(asyncio.run(main()))
