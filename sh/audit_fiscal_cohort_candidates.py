"""기존 30392 후보의 공식 결산월을 재조회하고 원문 해시를 로컬에 보존한다.

후보 탐색 전용이다. 기업개황 코드·결산월을 재무 검증이나 코호트 회원 자격으로
승격하지 않는다. 인증키와 원문은 Git에 넣지 않는다.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from dotenv import load_dotenv
from agent.finance.dart_protocol import DartFailure, DartHTTP
from agent.finance.dart_service import DartService
from agent.research_store import safe_payload


INPUT=ROOT/'doc/finance/COHORT_CANDIDATE_AUDIT_20261025.json'
RAW=ROOT/'data/research/cohort_fiscal_audit_20261025/company'
OUTPUT=ROOT/'doc/finance/COHORT_FISCAL_AUDIT_20261025.json'


async def main():
    load_dotenv(ROOT/'.env')
    key=os.environ.get('OPENDART_API_KEY')
    if not key:
        print('{"status":"missing_configuration"}')
        return 1
    source=json.loads(INPUT.read_text(encoding='utf-8'))
    raw_hash=hashlib.sha256(INPUT.read_bytes()).hexdigest()
    RAW.mkdir(parents=True,exist_ok=True)
    http=DartHTTP(key,max_response_bytes=2*1024*1024)
    records=[]
    for index,candidate in enumerate(source['records'],1):
        corp_code=candidate['corp_code']
        path=RAW/(corp_code+'.json')
        if path.exists():
            content=path.read_bytes()
            response=DartHTTP._decode(content,'company')
        else:
            try:
                response=await http.request('company',{'corp_code':corp_code},before_request=lambda: asyncio.sleep(0))
            except DartFailure as error:
                records.append({'corp_code':corp_code,'corp_name':candidate['corp_name'],
                                'status':error.kind})
                if error.kind in {'rate_limited','authentication_error','access_denied'}:break
                continue
            content=response.content
            DartService._atomic_write(path,content)
        data=response.data
        if data.get('corp_code')!=corp_code:
            records.append({'corp_code':corp_code,'corp_name':candidate['corp_name'],
                            'status':'company_mismatch'})
            continue
        records.append({'corp_code':corp_code,'corp_name':candidate['corp_name'],
                        'status':'profile_obtained','acc_mt':data.get('acc_mt'),
                        'company_induty_code':data.get('induty_code'),
                        'source_sha256':hashlib.sha256(content).hexdigest(),
                        'existing_annual_status':candidate['annual_2025']['status'],
                        'existing_half_status':candidate['half_2026']['status']})
        if index%20==0:print(safe_payload({'checked':index,'total':len(source['records'])}),flush=True)
    result={'status':'candidate_profiles_only','as_of':'2026-10-25',
            'source_audit_sha256':raw_hash,'candidate_count':len(source['records']),
            'checked_count':len(records),'records':records,
            'limitation':'결산월·기업개황은 금융 수치 또는 KSIC 30392 자체 분류의 검증이 아니다.'}
    DartService._atomic_write(OUTPUT,safe_payload(result).encode())
    print(safe_payload({'checked':len(records),'non_december':sum(
        str(r.get('acc_mt')) not in ('12','None') for r in records if r['status']=='profile_obtained'),
        'errors':sum(r['status']!='profile_obtained' for r in records)}))
    return 0 if len(records)==len(source['records']) and all(r['status']=='profile_obtained' for r in records) else 1


if __name__=='__main__':
    raise SystemExit(asyncio.run(main()))
