"""이미 식별된 DART 후보의 신규 정기공시만 제한된 요청으로 재확인한다."""

from datetime import date, timedelta
import re

from .general_collection import report_code, report_month


def refresh_plan(previous, *, cutoff, request_limit):
    previous_date = date.fromisoformat(previous['as_of'])
    cutoff_date = date.fromisoformat(cutoff)
    if cutoff_date <= previous_date:
        raise ValueError('cutoff_must_advance')
    rows = previous['records']
    if previous.get('checked_count') != len(rows) or not rows:
        raise ValueError('previous_audit_incomplete')
    if any(not re.fullmatch(r'\d{8}', str(row.get('corp_code', ''))) for row in rows):
        raise ValueError('invalid_corp_code')
    if len({row['corp_code'] for row in rows}) != len(rows):
        raise ValueError('duplicate_corp_code')
    if type(request_limit) is not int or request_limit < len(rows):
        raise ValueError('request_limit_below_candidate_count')
    return {'previous_as_of': previous['as_of'], 'public_cutoff': cutoff,
            'begin_date': (previous_date + timedelta(days=1)).isoformat(),
            'target_corp_codes': sorted(row['corp_code'] for row in rows),
            'request_limit': request_limit, 'request_count': len(rows),
            'operation': 'OpenDART list.json', 'page_count': 100,
            'new_reports_only': True}


def new_regular_reports(data, *, begin_date, cutoff):
    try:
        pages = int(data.get('total_page') or 1)
    except (TypeError, ValueError):
        raise ValueError('pagination_review_required') from None
    if pages != 1:
        raise ValueError('pagination_review_required')
    found = []
    for item in data.get('list', []):
        name = item.get('report_nm', '')
        receipt = str(item.get('rcept_no', ''))
        code = report_code(name)
        if (code in {'11011', '11012'} and re.fullmatch(r'\d{14}', receipt)
                and begin_date.replace('-', '') <= receipt[:8] <= cutoff.replace('-', '')):
            found.append({'report_nm': name, 'report_code': code,
                          'report_month': report_month(name), 'rcept_no': receipt})
    return sorted(found, key=lambda row: row['rcept_no'])
