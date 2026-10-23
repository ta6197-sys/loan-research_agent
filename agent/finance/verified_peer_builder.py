"""게시 보고서의 검증 결과와 원문을 재확인해 기업·기간 관측치를 생성한다."""
from collections import defaultdict
from decimal import Decimal, DecimalException
import json
import re
from agent.research_store import digest, encode, IntegrityFailure
from .financial_analytics import FinancialAnalyticsService
from .numeric_validation import NumericValidationService
from .validation_acceptance import MANDATORY_CHECKS
from .peer_dataset import FEATURES

ACCOUNT_POLICY = 'verified-peer-account-definitions-1.0'


def _identity(store, member, cohort, report):
    source = store.get_source(member['identity_source_id'])
    identity = json.loads(store.source_content(source['id']))
    if (identity.get('corp_code') != member['corp_code']
            or source['metadata'].get('reporting_period', {}).get('operation') != 'company'
            or source['metadata'].get('kind') != 'dart'):
        raise ValueError('industry_identity_source_mismatch')
    basis = store.get_research(report['research_id'])
    if basis['company']['corp_code'] != member['corp_code']: raise ValueError('report_company_mismatch')
    basis_ids = {b['id'] for b in basis['bases']}
    if store.get_run(source['run_id'])['basis_id'] not in basis_ids:
        raise ValueError('identity_evidence_company_mismatch')
    industry_sources = []
    if cohort['industry_system'] == 'KSIC':
        # DART 기업개황의 3자리 303은 5자리 30392의 충분한 근거가 아니다.
        dart_code = str(identity.get('induty_code') or '')
        if dart_code not in (cohort['industry_code'], cohort['industry_code'][:3]):
            raise ValueError('industry_parent_code_mismatch')
        proof = member['industry_evidence']
        industry_source = store.get_source(proof['source_id'])
        industry_meta = industry_source['metadata']
        xbrl_period = industry_meta.get('reporting_period', {})
        if (industry_meta.get('kind') != 'dart' or xbrl_period.get('operation') != 'xbrl_member'
                or not str(xbrl_period.get('member_name', '')).startswith('entity' + member['corp_code'] + '_')
                or not str(xbrl_period.get('member_name', '')).endswith('.xbrl')
                or store.get_run(industry_source['run_id'])['basis_id'] not in basis_ids):
            raise ValueError('industry_evidence_company_mismatch')
        raw_industry = store.source_content(proof['source_id'])
        if not NumericValidationService._raw_check(raw_industry, proof['location']):
            raise ValueError('industry_original_location_unverified')
        location = proof['location']
        span = raw_industry[location['byte_start']:location['byte_end']]
        pattern = rb'<dart-gcd:StandardIndustryCode\b[^>]*>(\d{3,5})</dart-gcd:StandardIndustryCode>'
        match = re.fullmatch(pattern, span.strip())
        if not match or match.group(1).decode() != cohort['industry_code']:
            raise ValueError('industry_self_classification_not_supported')
        published = industry_meta.get('published_at')
        if not published or published[:10] > cohort['public_cutoff']:
            raise ValueError('industry_evidence_after_cutoff')
        # 과거 자체 분류가 현재 XBRL과 충돌하면 해당 법인을 제외한다.
        with store.connection() as conn:
            own_sources = conn.execute(
                'SELECT s.id FROM research_sources s JOIN research_runs r ON s.run_id=r.id '
                'WHERE r.basis_id IN (' + ','.join('?' for _ in basis_ids) + ')', tuple(sorted(basis_ids))).fetchall()
        latest = []
        for record in own_sources:
            candidate = store.get_source(record['id'])
            meta = candidate['metadata']; period = meta.get('reporting_period', {})
            if (period.get('operation') != 'xbrl_member'
                    or not str(period.get('member_name', '')).startswith('entity' + member['corp_code'] + '_')
                    or not str(period.get('member_name', '')).endswith('.xbrl')
                    or not meta.get('published_at') or meta['published_at'][:10] > cohort['public_cutoff']):
                continue
            codes = {code.decode() for code in re.findall(pattern, store.source_content(candidate['id']))}
            if len(codes) > 1: raise ValueError('industry_code_conflict_within_xbrl')
            if codes:
                latest.append((meta.get('receipt_number') or '', candidate['id'], next(iter(codes))))
        if not latest:
            raise ValueError('industry_evidence_not_latest')
        if max(latest)[2] != cohort['industry_code']:
            raise ValueError('industry_latest_code_conflict')
        if max(latest)[:2] != (industry_meta.get('receipt_number') or '', industry_source['id']):
            raise ValueError('industry_evidence_not_latest')
        industry_sources.append(industry_source)
    elif identity.get('induty_code') != cohort['industry_code']:
        raise ValueError('industry_identity_source_mismatch')
    # 회계기준은 등록값을 신뢰하지 않고 기존 원문 위치에서 재확인한다.
    proof = member['accounting_standard_evidence']
    standard_source = store.get_source(proof['source_id'])
    fact_run = store.get_run(standard_source['run_id'])
    if fact_run['basis_id'] not in basis_ids: raise ValueError('accounting_evidence_company_mismatch')
    raw = store.source_content(proof['source_id'])
    if not NumericValidationService._raw_check(raw, proof['location']): raise ValueError('accounting_standard_location_unverified')
    loc = proof['location']; span = raw[loc['byte_start']:loc['byte_end']]
    labels = {'K-IFRS': '한국채택국제회계기준'}
    if cohort['accounting_standard'] not in labels or labels[cohort['accounting_standard']].encode() not in span:
        raise ValueError('accounting_standard_not_supported_by_original')
    meta = standard_source['metadata']; published = meta.get('published_at')
    if not published or published[:10] > cohort['public_cutoff']: raise ValueError('accounting_evidence_after_cutoff')
    return [source, *industry_sources, standard_source]


def build_report(store, report_id, member, cohort):
    report = store.get_report(report_id)
    if report['status'] != 'published': raise ValueError('published_report_required')
    if report['content'].get('context', {}).get('public_cutoff') != cohort['public_cutoff']:
        raise ValueError('report_correction_cutoff_not_matching_cohort')
    policy_sources = _identity(store, member, cohort, report)
    batch = NumericValidationService(store).batch(report['publication']['validation_batch_id'])
    analytics = FinancialAnalyticsService(store).report(report_id)
    validated = {v['id']: v for v in batch['validations']}
    rows = report['content'].get('context', {}).get('financial_rows', [])
    result, excluded = [], []
    for period in cohort['periods']:
        end = period['end']; all_eligible = {}
        source_manifest = {s['id']: {'source_id':s['id'], 'source_hash':s['content_hash'],
                           'receipt':s['metadata'].get('receipt_number') or '',
                           'public_at':(s['metadata'].get('published_at') or s['metadata']['retrieved_at'])[:10]} for s in policy_sources}
        if any(s['public_at'] > cohort['public_cutoff'] for s in source_manifest.values()):
            raise ValueError('identity_evidence_after_cutoff')
        for row in rows:
            if row.get('scope') != cohort['scope'] or row.get('status') != 'verified': continue
            row_period = next((p for p in cohort['periods'] if p['end'] == row.get('end')), None)
            if row_period is None: continue
            if not row.get('fact_ids') or not row.get('validation_ids'): continue
            # 출처·사실·검증 ID를 같은 회사와 기간으로 재결합한다.
            try:
                for vid in row['validation_ids']:
                    v = validated[vid]; d = v['data']
                    if v['status'] != 'pass' or any(d.get('checks', {}).get(rule, {}).get('status') != 'pass' for rule in MANDATORY_CHECKS): raise ValueError('mandatory_checks_not_passed')
                    if v.get('fact_id') not in row['fact_ids'] or d.get('account') != row['account'] or d.get('scope') != row['scope']: raise ValueError('fact_validation_mapping_mismatch')
                    if Decimal(d.get('amount_comparison', {}).get('original_normalized', 'NaN')) != Decimal(row['value']): raise ValueError('published_value_mismatch')
                if {validated[vid].get('fact_id') for vid in row['validation_ids']} != set(row['fact_ids']):
                    raise ValueError('unvalidated_fact_reference')
                for fid in row['fact_ids']:
                    fact = store.get_evidence(fid); data = fact['data']; source = fact['source']; meta = source['metadata']
                    if meta['kind'] not in ('dart', 'official_ir') and cohort.get('data_origin') != 'synthetic_test_only':
                        raise ValueError('non_public_source_excluded')
                    if fact['company']['corp_code'] != member['corp_code'] or data['scope'] != cohort['scope'] or data['account'] != row['account']: raise ValueError('fact_identity_mismatch')
                    if data['period'] != row.get('period'): raise ValueError('published_period_mismatch')
                    if data.get('currency') != 'KRW' or data.get('unit') != '원': raise ValueError('currency_or_unit_not_comparable')
                    expected = {'kind': 'duration', 'start': row_period['start'], 'end': row_period['end']}
                    actual_period = {key: data['period'].get(key) for key in ('kind', 'start', 'end', 'as_of') if key in data['period']}
                    if actual_period not in (expected, {'kind': 'instant', 'as_of': row_period['end']}):
                        raise ValueError('period_not_comparable')
                    if not meta.get('published_at') or meta['published_at'][:10] > cohort['public_cutoff']: raise ValueError('source_after_cutoff')
                    if not meta.get('receipt_number'): raise ValueError('source_receipt_missing')
                    raw = store.source_content(source['id'])
                    if not NumericValidationService._raw_check(raw, fact['fragment']['data']['location']): raise ValueError('raw_location_unverified')
                    source_manifest[source['id']] = {'source_id': source['id'], 'source_hash': source['content_hash'], 'receipt': meta['receipt_number'], 'public_at': meta['published_at'][:10]}
                all_eligible[(row['account'], row['end'])] = row
            except (ValueError, KeyError, DecimalException) as error:
                excluded.append({'report_id': report_id, 'account': row['account'], 'period_end': row['end'], 'reason': str(error)})
        eligible = {account: row for (account, row_end), row in all_eligible.items() if row_end == end}
        features = {}
        for item in analytics['results']:
            if item['name'] not in FEATURES or item['status'] != 'supported' or item['scope'] != cohort['scope'] or item['period'].get('end', item['period'].get('as_of')) != end: continue
            if not item.get('inputs') or any((ref['account'], ref.get('period', {}).get('end') or ref.get('period', {}).get('as_of')) not in all_eligible for ref in item['inputs']): continue
            # 비율은 당기, 증감은 기존 V11 증거가 필요하다.
            if item['period'].get('comparison') and not item.get('comparison_calculation_ids'): continue
            features[item['name']] = {'status': 'supported', 'value': item['value'], 'evidence_refs': item['inputs'], 'calculation_ids': item.get('comparison_calculation_ids', [])}
        for account, name in (('inventory','inventory_to_assets'), ('receivables','receivables_to_assets')):
            if account in eligible and 'assets' in eligible and Decimal(eligible['assets']['value']) > 0:
                inputs = [eligible[account], eligible['assets']]
                features[name] = {'status': 'supported', 'value': str(Decimal(inputs[0]['value']) / Decimal(inputs[1]['value']) * 100),
                                  'evidence_refs': [{k: row[k] for k in ('account','period','fact_ids','validation_ids','source_ids')} for row in inputs]}
        if not features or not source_manifest: continue
        manifest = sorted(source_manifest.values(), key=lambda s: s['source_id'])
        result.append({'corp_code': member['corp_code'], 'industry_code': cohort['industry_code'], 'period_start': period['start'], 'period_end': end,
                       'period_kind': 'duration', 'scope': cohort['scope'], 'public_cutoff': cohort['public_cutoff'],
                       'source_receipt': max(s['receipt'] for s in manifest), 'source_hash': digest(encode(manifest).encode()),
                       'source_public_at': max(s['public_at'] for s in manifest), 'validation_batch_id': batch['id'],
                       'account_definition_version': ACCOUNT_POLICY, 'peer_group': cohort['cohort_id'], 'correction_status': 'current_verified',
                       'features': features, 'source_manifest': manifest, 'report_id': report_id,
                       'data_origin': cohort.get('data_origin', 'verified_public_disclosure'),
                       'accounting_standard': cohort['accounting_standard'], 'currency': 'KRW'})
    return result, list({encode(issue): issue for issue in excluded}.values())
