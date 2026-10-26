"""비12월 결산 후보의 최신 사업보고서 XBRL 자체 업종코드를 확인한다."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from dotenv import load_dotenv
from agent.finance.dart_protocol import DartFailure, DartHTTP, zip_members
from agent.finance.dart_service import DartService
from agent.research_store import safe_payload

INPUT=ROOT/'doc/finance/COHORT_FISCAL_AUDIT_20261025.json'
RAW=ROOT/'data/research/cohort_fiscal_audit_20261025/xbrl'
OUTPUT=ROOT/'doc/finance/COHORT_FISCAL_XBRL_20261025.json'
CUTOFF='2026-10-25'
PATTERN=rb'<dart-gcd:StandardIndustryCode\b[^>]*>(\d{3,5})</dart-gcd:StandardIndustryCode>'


async def request(http,operation,params,path):
    if path.exists():
        content=path.read_bytes()
        return DartHTTP._decode(content,operation)
    result=await http.request(operation,params,before_request=lambda: asyncio.sleep(0))
    DartService._atomic_write(path,result.content)
    return result


async def main():
    load_dotenv(ROOT/'.env')
    key=os.environ.get('OPENDART_API_KEY')
    if not key:
        print('{"status":"missing_configuration"}')
        return 1
    profiles=json.loads(INPUT.read_text(encoding='utf-8'))
    RAW.mkdir(parents=True,exist_ok=True)
    http=DartHTTP(key,max_response_bytes=100*1024*1024)
    records=[]
    for profile in profiles['records']:
        if profile['status']!='profile_obtained' or profile.get('acc_mt')=='12':continue
        corp_code=profile['corp_code']
        record={'corp_code':corp_code,'corp_name':profile['corp_name'],
                'acc_mt':profile.get('acc_mt'),'status':'not_checked'}
        for scope in ('OFS','CFS'):
            params={'corp_code':corp_code,'bsns_year':'2026','reprt_code':'11011','fs_div':scope}
            path=RAW/f'{corp_code}_2026_11011_{scope}.json'
            try:response=await request(http,'financials',params,path)
            except DartFailure as error:
                record[scope+'_status']=error.kind
                continue
            rows=response.data.get('list',[])
            receipts={row.get('rcept_no') for row in rows}
            if len(receipts)!=1 or not all(row.get('corp_code')==corp_code for row in rows):
                record[scope+'_status']='receipt_or_company_mismatch'
                continue
            receipt=next(iter(receipts))
            if not re.fullmatch(r'\d{14}',str(receipt)) or receipt[:8]>CUTOFF.replace('-',''):
                record[scope+'_status']='outside_cutoff_or_invalid_receipt'
                continue
            record.update(status='financial_receipt_found',receipt=receipt,financial_scope=scope,
                          financial_source_sha256=hashlib.sha256(response.content).hexdigest())
            break
        if record['status']!='financial_receipt_found':
            params={'corp_code':corp_code,'bgn_de':'2023-01-01','end_de':CUTOFF,
                    'last_reprt_at':'Y','page_no':1,'page_count':100}
            path=RAW/f'{corp_code}_disclosures.json'
            try:disclosed=await request(http,'disclosures',params,path)
            except DartFailure as error:
                record['disclosure_status']=error.kind
                records.append(record);continue
            if int(disclosed.data.get('total_page') or 1)!=1:
                record['disclosure_status']='pagination_review_required'
                records.append(record);continue
            report_pattern=r'^사업보고서\(20\d{2}\.'+str(profile['acc_mt'])+r'\)'
            annual=sorted((row for row in disclosed.data.get('list',[])
                           if re.match(report_pattern,str(row.get('report_nm','')))
                           and re.fullmatch(r'\d{14}',str(row.get('rcept_no','')))
                           and row['rcept_no'][:8]<=CUTOFF.replace('-','')),
                          key=lambda row:row['rcept_no'])
            record['disclosure_source_sha256']=hashlib.sha256(disclosed.content).hexdigest()
            if not annual:
                record['disclosure_status']='matching_annual_not_found'
                records.append(record);continue
            record.update(status='disclosure_receipt_found',receipt=annual[-1]['rcept_no'],
                          disclosure_status='annual_found')
        receipt=record['receipt']
        xbrl_path=RAW/f'{corp_code}_{receipt}_11011.zip'
        try:response=await request(http,'xbrl',{'rcept_no':receipt,'reprt_code':'11011'},xbrl_path)
        except DartFailure as error:
            record.update(status=error.kind)
            records.append(record);continue
        try:members=zip_members(response.content,max_expanded_bytes=256*1024*1024,max_members=100)
        except DartFailure as error:
            record.update(status=error.kind)
            records.append(record);continue
        codes=[]
        member_hashes=[]
        for name,content in members:
            if name.startswith('entity'+corp_code+'_') and name.endswith('.xbrl'):
                member_hashes.append({'name':name,'sha256':hashlib.sha256(content).hexdigest()})
                codes.extend(code.decode() for code in re.findall(PATTERN,content))
        record.update(status='xbrl_checked' if codes else 'xbrl_code_absent',
                      xbrl_codes=sorted(set(codes)),xbrl_sha256=hashlib.sha256(response.content).hexdigest(),
                      entity_members=member_hashes)
        records.append(record)
    result={'status':'candidate_xbrl_only','as_of':CUTOFF,
            'profile_audit_sha256':hashlib.sha256(INPUT.read_bytes()).hexdigest(),
            'records':records,
            'limitation':'기업의 XBRL 자체 업종 확인만 수행. 재무 수치 검증·코호트 편입은 별도.'}
    DartService._atomic_write(OUTPUT,safe_payload(result).encode())
    print(safe_payload({'records':records}))
    return 0


if __name__=='__main__':
    raise SystemExit(asyncio.run(main()))
