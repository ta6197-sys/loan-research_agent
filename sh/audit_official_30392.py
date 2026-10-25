"""DART 업종별 30392 화면의 법인코드를 보관 목록·기업개황과 대조한다.

화면 결과는 2026-10-25에 직접 조회한 13건이다. 엑셀일괄 파일은 전체
기업 목록이므로 업종 필터의 증빙으로 사용하지 않는다.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from agent.finance.dart_protocol import DartFailure, DartHTTP
from agent.finance.dart_service import DartService
from agent.research_store import safe_payload

CATALOGUE = ROOT / 'data/research/_catalogue/64b834e3e569fdc3a789e5a7287bd8c20cc431fb9365e012952920142853373c.zip'
OLD = ROOT / 'doc/finance/COHORT_CANDIDATE_AUDIT_20261025.json'
RAW = ROOT / 'data/research/cohort_official_30392_20261025/company'
OUTPUT = ROOT / 'doc/finance/KSIC30392_OFFICIAL_CANDIDATES_20261025.json'
DART_LIST = (
    ('01369609', '뉴엠티'), ('02007043', '디디엠'),
    ('01839588', '메가테크'), ('01807534', '모비언트'),
    ('01732230', '미래전자'), ('00306472', '발레오전장시스템스코리아'),
    ('01897625', '산도브레이크'), ('02009625', '삼성특수브레이크'),
    ('01287882', '세양'), ('02008927', '에이치에스씨'),
    ('01839986', '오피모빌리티에이치투파워코리아'),
    ('01824124', '제이디오토모티브'), ('01299188', '투엠모터스'),
)


def legal_name(value):
    text = re.sub(r'^(?:\(주\)|주식회사|㈜)\s*', '', str(value or '')).strip()
    return re.sub(r'\s*(?:주식회사|\(주\)|㈜)$', '', text).strip()


async def main():
    load_dotenv(ROOT / '.env')
    old = json.loads(OLD.read_text(encoding='utf-8'))
    old_codes = {row['corp_code'] for row in old['records']}
    with zipfile.ZipFile(CATALOGUE) as archive:
        catalogue = ET.fromstring(archive.read('CORPCODE.xml'))
    selected = {code for code, _ in DART_LIST}
    catalog_rows = {}
    for row in catalogue:
        code = row.findtext('corp_code')
        if code in selected:
            catalog_rows.setdefault(code, []).append({tag: row.findtext(tag) for tag in
                ('corp_code', 'corp_name', 'stock_code', 'modify_date')})
    key = os.environ.get('OPENDART_API_KEY')
    http = DartHTTP(key, max_response_bytes=2 * 1024 * 1024) if key else None
    RAW.mkdir(parents=True, exist_ok=True)
    records = []
    for code, name in DART_LIST:
        matches = catalog_rows.get(code, [])
        record = {'corp_code': code, 'corp_name': name, 'stock_code': None,
                  'industry_code': '30392', 'industry_classification_system': 'KSIC',
                  'industry_classification_version': 'DART_tree_revision_unstated',
                  'industry_evidence_source': 'https://dart.fss.or.kr/dsae001/main.do',
                  'industry_evidence_date': '2026-10-25', 'fiscal_end_month': None,
                  'candidate_status': 'identity_review_required', 'exclusion_reason': None,
                  'cohort_id': 'ksic30392_dart_tree_candidates',
                  'catalogue_matches': matches, 'previously_audited': code in old_codes}
        if len(matches) != 1 or matches[0]['corp_name'] != name or matches[0]['stock_code'].strip():
            record['exclusion_reason'] = 'corpcode_catalogue_identity_or_stock_conflict'
            records.append(record)
            continue
        record['catalogue_modify_date'] = matches[0]['modify_date']
        path = RAW / f'{code}.json'
        try:
            if path.exists():
                content = path.read_bytes()
                response = DartHTTP._decode(content, 'company')
            elif http:
                response = await http.request('company', {'corp_code': code}, before_request=lambda: asyncio.sleep(0))
                content = response.content
                DartService._atomic_write(path, content)
            else:
                record['exclusion_reason'] = 'company_api_key_unavailable'
                records.append(record)
                continue
        except DartFailure as error:
            record['exclusion_reason'] = 'company_api_' + error.kind
            records.append(record)
            if error.kind in {'rate_limited', 'authentication_error', 'access_denied'}:
                break
            continue
        company = response.data
        record['company_source_sha256'] = hashlib.sha256(content).hexdigest()
        record['company_induty_code'] = company.get('induty_code')
        record['corp_cls'] = company.get('corp_cls')
        record['stock_code'] = company.get('stock_code') or None
        record['api_corp_name'] = company.get('corp_name')
        if (company.get('corp_code') != code or legal_name(company.get('corp_name')) != legal_name(name)
                or record['stock_code']):
            record['exclusion_reason'] = 'company_api_identity_or_stock_conflict'
        else:
            month = str(company.get('acc_mt') or '')
            record['fiscal_end_month'] = int(month) if month.isdigit() and 1 <= int(month) <= 12 else None
            if str(company.get('induty_code')) != '30392':
                record['candidate_status'] = 'ksic5_unverified'
                record['exclusion_reason'] = 'company_induty_code_not_30392'
            else:
                record['candidate_status'] = 'ksic5_revision_review_required'
                record['exclusion_reason'] = 'ksic_revision_and_financial_validation_pending'
        records.append(record)
    result = {'status': 'candidate_discovery_only', 'as_of': '2026-10-25',
              'dart_tree_code': '30392', 'dart_tree_count': len(DART_LIST),
              'previous_candidate_overlap': sum(x['previously_audited'] for x in records),
              'catalogue_sha256': hashlib.sha256(CATALOGUE.read_bytes()).hexdigest(),
              'prior_audit_sha256': hashlib.sha256(OLD.read_bytes()).hexdigest(),
              'records': records,
              'limitation': 'DART 업종 트리의 KSIC 개정 차수가 화면에 없어 기존 코호트에 자동 편입하지 않는다.'}
    DartService._atomic_write(OUTPUT, safe_payload(result).encode())
    print(safe_payload({'records': len(records), 'identity_confirmed': sum(
        r['candidate_status'] == 'ksic5_revision_review_required' for r in records),
        'previously_audited': result['previous_candidate_overlap']}))
    return 0 if len(records) == len(DART_LIST) else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
