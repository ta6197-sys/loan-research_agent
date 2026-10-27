"""새 기준일에서 기존 13개 DART 법인만 1회씩 조회하고 새 읽기 감사를 기록한다."""
import argparse
import asyncio
from datetime import datetime, date
import hashlib
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from agent.finance.candidate_disclosure_refresh import refresh_plan, new_regular_reports
from agent.finance.dart_protocol import DartFailure, DartHTTP
from agent.finance.dart_service import DartService
from agent.research_store import safe_payload

DEFAULT_PREVIOUS = ROOT / 'doc/finance/KSIC30392_OFFICIAL_DISCLOSURES_20261026.json'


async def run(cutoff, request_limit, *, previous_path=DEFAULT_PREVIOUS):
    if date.fromisoformat(cutoff) > datetime.now(ZoneInfo('Asia/Seoul')).date():
        raise ValueError('cutoff_cannot_be_in_future')
    previous_bytes = previous_path.read_bytes()
    previous = json.loads(previous_bytes)
    plan = refresh_plan(previous, cutoff=cutoff, request_limit=request_limit)
    output = ROOT / f"doc/finance/KSIC30392_OFFICIAL_DISCLOSURES_{cutoff.replace('-', '')}.json"
    if output.exists():
        raise ValueError('refresh_snapshot_already_exists')
    load_dotenv(ROOT / '.env')
    key = os.environ.get('OPENDART_API_KEY')
    if not key:
        raise ValueError('opendart_api_key_missing')
    plan_path = ROOT / f"doc/finance/KSIC30392_DISCLOSURE_PLAN_{cutoff.replace('-', '')}.json"
    plan_bytes = (safe_payload({**plan, 'previous_audit_sha256':
                                hashlib.sha256(previous_bytes).hexdigest()}) + '\n').encode()
    if plan_path.exists():
        if plan_path.read_bytes() != plan_bytes:
            raise ValueError('refresh_plan_changed_after_freeze')
    else:
        DartService._atomic_write(plan_path, plan_bytes)
    http = DartHTTP(key, max_response_bytes=4 * 1024 * 1024)
    raw = ROOT / 'data/research/cohort_official_30392_20261026' / f"disclosures_{cutoff.replace('-', '')}"
    raw.mkdir(parents=True, exist_ok=True)
    records = []
    for old in previous['records']:
        code = old['corp_code']
        params = {'corp_code': code, 'bgn_de': plan['begin_date'],
                  'end_de': cutoff, 'last_reprt_at': 'N',
                  'page_no': 1, 'page_count': 100}
        try:
            response = await http.request('disclosures', params, before_request=lambda: asyncio.sleep(0))
        except DartFailure as error:
            if error.kind != 'no_data':
                raise RuntimeError(f'dart_refresh_failed:{code}:{error.kind}') from None
            found, source_hash = [], None
        else:
            path = raw / f'{code}.json'
            DartService._atomic_write(path, response.content)
            found = new_regular_reports(response.data, begin_date=plan['begin_date'], cutoff=cutoff)
            source_hash = hashlib.sha256(response.content).hexdigest()
        records.append({'corp_code': code, 'corp_name': old['corp_name'],
                        'previous_status': old['status'],
                        'status': 'new_reports_found' if found else 'no_new_annual_or_half_report',
                        'new_reports': found, 'source_sha256': source_hash})
    result = {'status': 'candidate_report_refresh_only', 'as_of': cutoff,
              'previous_audit_sha256': hashlib.sha256(previous_bytes).hexdigest(),
              'collection_plan_sha256': hashlib.sha256(plan_bytes).hexdigest(),
              'collection_plan': plan, 'checked_count': len(records), 'records': records,
              'limitation': '새 공시목록은 법인 업종 차수·원문·XBRL·재무 수치 검증 근거가 아니다.'}
    DartService._atomic_write(output, safe_payload(result).encode())
    return {'output': str(output), 'checked': len(records),
            'with_new_reports': sum(bool(row['new_reports']) for row in records)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cutoff', required=True, help='이전 감사일보다 늦은 공개 기준일 YYYY-MM-DD')
    parser.add_argument('--request-limit', required=True, type=int)
    parser.add_argument('--previous', type=Path, default=DEFAULT_PREVIOUS,
                        help='직전 불변 공시 감사 JSON')
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.cutoff, args.request_limit,
                                     previous_path=args.previous)), ensure_ascii=False))
