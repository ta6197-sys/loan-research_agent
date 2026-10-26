"""공장 단위 30392 후보를 법인 식별과 업종 근거가 확인될 때까지 보류한다."""

from datetime import date
import re

CHECKS = ('corp_code', 'legal_address', 'factory_location', 'official_registration')
STATES = {'pass', 'conflict', 'unverified'}


def assess_candidate(checks, *, industry_status='unverified', evidence=None):
    """서로 독립적인 공식 근거 네 가지가 모두 통과해야 확인한다."""
    if set(checks) != set(CHECKS) or any(value not in STATES for value in checks.values()):
        raise ValueError('identity_checks_incomplete_or_invalid')
    if industry_status not in STATES:
        raise ValueError('industry_status_invalid')
    evidence = evidence or {}
    evidence_ready = all(isinstance(evidence.get(key), dict)
        and evidence[key].get('source_url', '').startswith('https://')
        and re.fullmatch(r'[0-9a-f]{64}', evidence[key].get('source_sha256', ''))
        and evidence[key].get('matched_value') for key in (*CHECKS, 'ksic_revision_and_legal_industry'))
    if 'conflict' in checks.values() or industry_status == 'conflict':
        status = '충돌'
    elif all(value == 'pass' for value in checks.values()) and industry_status == 'pass' and evidence_ready:
        status = '확인'
    else:
        status = '미확인'
    unresolved = [key for key in CHECKS if checks[key] != 'pass']
    if industry_status != 'pass':
        unresolved.append('ksic_revision_and_legal_industry')
    if not evidence_ready and not unresolved:
        unresolved.append('official_evidence_reference_missing')
    return {'identity_status': status, 'checks': dict(checks),
            'industry_status': industry_status, 'unresolved': unresolved,
            'evidence': evidence,
            'next_stage_allowed': status == '확인'}


def collection_plan(candidates, *, public_cutoff, request_limit, maximum_reports_per_company=2):
    """외부 조회 전에 대상·기준일·최대 요청 수를 봉인하는 계획 자료."""
    date.fromisoformat(public_cutoff)
    if type(request_limit) is not int or request_limit < 0:
        raise ValueError('invalid_request_limit')
    if type(maximum_reports_per_company) is not int or maximum_reports_per_company < 1:
        raise ValueError('invalid_report_limit')
    selected = sorted({r['corp_code'] for r in candidates if r.get('identity_status') == '확인'
                       and r.get('next_stage_allowed') is True
                       and r.get('eligible_report_count', 0) > 0 and r.get('corp_code')})
    if len(selected) * maximum_reports_per_company > request_limit:
        raise ValueError('request_budget_below_target_maximum')
    return {'public_cutoff': public_cutoff, 'target_corp_codes': selected,
            'request_limit': request_limit,
            'maximum_reports_per_company': maximum_reports_per_company,
            'unselected_count': len(candidates) - len(selected),
            'no_report_status': '비상장 또는 공시 미확인',
            'missing_numeric_value': None}
