"""Archived OpenDART audit evidence for an exploratory 303 family candidate list.

This never promotes XBRL industry text or an API receipt to a verified peer row.
"""
from collections import Counter
from hashlib import sha256
import json


VERSION = 'broad-303-candidates-1.0'


def classify(record):
    annual = record['annual_2025']
    half = record['half_2026']
    annual_codes = annual.get('xbrl_codes') or []
    half_codes = half.get('xbrl_codes') or []
    if not annual_codes or not half_codes:
        status = 'xbrl_industry_unconfirmed'
    elif len(annual_codes) != 1 or len(half_codes) != 1:
        status = 'industry_conflict_within_report'
    elif not annual_codes[0].startswith('303') or not half_codes[0].startswith('303'):
        status = 'outside_observed_303_family'
    elif annual_codes[0] != half_codes[0]:
        status = 'industry_code_changed_review_required'
    else:
        status = 'candidate_only'
    return {
        'corp_code': record['corp_code'], 'corp_name': record['corp_name'],
        'stock_code': record.get('stock_code'),
        'company_induty_code': record.get('company_induty_code'),
        'annual_2025': {'receipt': annual.get('receipt'), 'status': annual.get('status'),
                        'observed_codes': annual_codes},
        'half_2026': {'receipt': half.get('receipt'), 'status': half.get('status'),
                      'observed_codes': half_codes},
        'candidate_status': status,
        'cohort_status': 'not_included',
        'classification_revision_status': 'unconfirmed',
        'financial_validation_status': 'not_inferred_from_candidate_audit',
    }


def build(archived_bytes):
    audit = json.loads(archived_bytes)
    records = [classify(item) for item in audit['records']]
    records.sort(key=lambda item: item['corp_code'])
    counts = Counter(item['candidate_status'] for item in records)
    return {
        'schema_version': VERSION,
        'candidate_group_id': 'opendart-observed-303-family-separate-from-ksic30392',
        'source_audit_sha256': sha256(archived_bytes).hexdigest(),
        'source_audit_date': '2026-10-25',
        'source_periods': ['2025_annual', '2026_half'],
        'public_cutoff': '2026-10-25',
        'selection_rule': 'single identical XBRL StandardIndustryCode beginning 303 in both archived reports',
        'limitations': [
            'XBRL 자체 분류의 KSIC 개정 차수는 이 기록만으로 확인하지 못했습니다.',
            '공시 접수번호와 업종 후보는 원문/API 수치 검증 또는 비교집단 편입 근거가 아닙니다.',
            '법인 전체 재무제표가 303 세부 사업만을 나타낸다고 추정하지 않습니다.',
        ],
        'candidate_count': counts.get('candidate_only', 0),
        'status_counts': dict(sorted(counts.items())),
        'records': records,
    }
