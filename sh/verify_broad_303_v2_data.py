"""Read-only source, period, and validation regression for new v2 companies."""
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.finance.numeric_validation import NumericValidationService
from agent.finance.report_context import fiscal_periods, numeric_rows
from agent.finance.validation_acceptance import MANDATORY_CHECKS
from agent.research_api import _run_verified_analysis_payload
from agent.research_store import ResearchStore, encode

DOC = ROOT / 'doc/finance'
DATA = ROOT / 'data/research/broad_303_increment_20261027'
AUDIT = json.loads((DOC / 'BROAD_303_EXPANDED_V2_AUDIT_20261027.json').read_text())
SNAPSHOTS = {scope: json.loads((DOC / f'BROAD_303_EXPANDED_V2_{scope}_20261027.json').read_text())
             for scope in ('OFS', 'CFS')}
STORE = ResearchStore(DATA / 'session.db', DATA / 'sources')


def verify():
    reviews = [item for item in AUDIT['company_reviews']
               if item['source_store'] == 'increment' and item['eligible_observations'] > 0]
    if len(reviews) != AUDIT['new_eligible_companies']:
        raise ValueError('increment_eligible_company_count_mismatch')
    codes = {item['corp_code'] for item in reviews}
    if len(codes) != len(reviews):
        raise ValueError('duplicate_increment_company')
    source_cache = {}
    fact_cache = {}
    batch_cache = {}
    reviewed = []
    total_rows = 0
    feature_refs = 0
    v12_eligible_periods = []
    v12_supported_preview = []
    for review in reviews:
        code, run_id = review['corp_code'], review['run_id']
        run = STORE.get_run(run_id)
        collection = run['trigger']['collection_result']
        fiscal = collection['fiscal_period']
        periods = fiscal_periods(collection['target_reports'], fiscal['month'])
        by_end = {item['end']: item for item in periods}
        receipts = {(int(item['year']), item['report_code']): item['rcept_no']
                    for item in collection['target_reports']}
        company_rows = [row for scope in ('OFS', 'CFS') for row in SNAPSHOTS[scope]['rows']
                        if row['corp_code'] == code]
        if len(company_rows) != review['eligible_observations'] or not company_rows:
            raise ValueError('company_observation_count_mismatch:' + code)
        for row in company_rows:
            total_rows += 1
            end = row['period_end']
            if (end not in by_end or row['period_start'] != by_end[end]['start']
                    or row['report_period_kind'] != by_end[end]['period_kind']
                    or row['source_receipt'] != receipts[(by_end[end]['year'], by_end[end]['report_code'])]
                    or row['run_id'] != run_id or row['scope'] not in ('OFS', 'CFS')
                    or row['verification_gates'] != {gate: 'pass' for gate in
                        ('original_api', 'actual_period', 'fiscal_month', 'independent_xbrl')}
                    or row['correction_status'] != 'current_verified'):
                raise ValueError('period_or_gate_mismatch:' + code + ':' + end)
            manifest = row['source_manifest']
            if sha256(encode(manifest).encode()).hexdigest() != row['source_hash']:
                raise ValueError('manifest_hash_mismatch:' + code + ':' + end)
            manifest_ids = {item['source_id'] for item in manifest}
            for item in manifest:
                source_id = item['source_id']
                if source_id not in source_cache:
                    source = STORE.get_source(source_id)
                    source_cache[source_id] = (source, sha256(STORE.source_content(source_id)).hexdigest())
                source, content_hash = source_cache[source_id]
                if (source['run_id'] != run_id or item['source_hash'] != content_hash
                        or item['public_at'] > row['public_cutoff']
                        or item['receipt'] != (source['metadata'].get('receipt_number') or '')):
                    raise ValueError('source_provenance_mismatch:' + source_id)
            batch_id = row['validation_batch_id']
            if batch_id not in batch_cache:
                batch = NumericValidationService(STORE).batch(batch_id)
                if batch['run_id'] != run_id:
                    raise ValueError('validation_batch_run_mismatch:' + batch_id)
                batch_cache[batch_id] = {item['id']: item for item in batch['validations']}
            validations = batch_cache[batch_id]
            for refs in row['feature_evidence'].values():
                for ref in refs:
                    feature_refs += 1
                    if not ref['fact_ids'] or not ref['validation_ids'] or not ref['source_ids']:
                        raise ValueError('feature_trace_missing:' + code + ':' + end)
                    if not set(ref['source_ids']).issubset(manifest_ids):
                        raise ValueError('feature_source_not_in_manifest:' + code + ':' + end)
                    if (ref['period'].get('end') or ref['period'].get('as_of')) not in by_end:
                        raise ValueError('feature_period_outside_plan:' + code + ':' + end)
                    for validation_id in ref['validation_ids']:
                        validation = validations.get(validation_id)
                        if (not validation or validation['status'] != 'pass'
                                or validation['rule_id'] != 'numeric_fact'
                                or any(validation['data']['checks'].get(rule, {}).get('status') != 'pass'
                                       for rule in MANDATORY_CHECKS)
                                or validation['data']['source_id'] not in ref['source_ids']
                                or validation['fact_id'] not in ref['fact_ids']):
                            raise ValueError('feature_validation_not_passed:' + validation_id)
                    for fact_id in ref['fact_ids']:
                        if fact_id not in fact_cache:
                            fact_cache[fact_id] = STORE.get_evidence(fact_id)
                        fact = fact_cache[fact_id]
                        if (fact['run_id'] != run_id or fact['source']['id'] not in ref['source_ids']
                                or fact['source']['id'] not in manifest_ids):
                            raise ValueError('feature_fact_mismatch:' + fact_id)
            if 'borrowing_to_assets' in row['features']:
                raise ValueError('unexpected_v12_feature_in_immutable_v2:' + code + ':' + end)
        # Check the broader stored numeric batch. If no three-part V12 group exists
        # there, correction filtering cannot create one in the accepted subset.
        batch_id = company_rows[0]['validation_batch_id']
        batch = NumericValidationService(STORE).batch(batch_id)
        numeric = numeric_rows(batch, tuple(by_end), periods)
        for scope in ('OFS', 'CFS'):
            for end in by_end:
                group = {(item['account']): item for item in numeric
                         if item['scope'] == scope and item['end'] == end}
                required = ('assets', 'short_term_borrowings',
                            'current_long_term_borrowings', 'long_term_borrowings')
                if all(group.get(name, {}).get('status') == 'verified' for name in required):
                    v12_eligible_periods.append({'corp_code': code, 'scope': scope, 'period_end': end})
        reviewed.append({'corp_code': code, 'run_id': run_id,
                         'observation_count': len(company_rows),
                         'period_ends': sorted({item['period_end'] for item in company_rows})})
        preview = _run_verified_analysis_payload(STORE, run_id)
        if preview['xbrl_gate']['status'] != 'pass':
            raise ValueError('replayed_xbrl_gate_failed:' + code)
        for metric in preview['financial_metrics']:
            if metric['name'] != 'borrowing_to_assets' or metric['status'] != 'supported':
                continue
            finding = {'corp_code': code, 'scope': metric['scope'],
                       'period_end': metric['period']['as_of'], 'value_percent': metric['value'],
                       'input_fact_ids': metric['input_fact_ids'],
                       'input_validation_ids': metric['input_validation_ids'],
                       'source_ids': metric['source_ids']}
            if not finding['input_fact_ids'] or not finding['input_validation_ids'] or not finding['source_ids']:
                raise ValueError('v12_supported_without_provenance:' + code)
            v12_supported_preview.append(finding)
    expected = {(item['corp_code'], item['scope'], item['period_end']) for item in v12_eligible_periods}
    actual = {(item['corp_code'], item['scope'], item['period_end']) for item in v12_supported_preview}
    if expected != actual:
        raise ValueError('v12_numeric_and_accepted_preview_disagree')
    return {'schema_version': 'broad-303-v2-data-regression-1.0',
            'snapshot_ids': {scope: snapshot['snapshot_id'] for scope, snapshot in SNAPSHOTS.items()},
            'new_company_count': len(reviews), 'new_company_period_rows': total_rows,
            'checked_unique_sources': len(source_cache), 'checked_unique_facts': len(fact_cache),
            'checked_feature_references': feature_refs,
            'v12_eligible_periods_in_stored_batches': v12_eligible_periods,
            'v12_supported_in_current_read_model': v12_supported_preview,
            'companies': reviewed, 'external_requests': 0, 'model_calls': 0,
            'limitations': ['기존 v2 자료의 읽기 전용 회귀이며 신규 공시 조회는 수행하지 않습니다.',
                            'V12 새 정책의 확인 수치는 v2 고정 스냅샷에 소급 반영하지 않습니다.',
                            '303 계열 XBRL 코드의 KSIC 개정 차수는 미확인입니다.']}


if __name__ == '__main__':
    output = verify()
    path = DOC / 'BROAD_303_V2_DATA_REGRESSION_20261027.json'
    encoded = json.dumps(output, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
    if path.exists() and path.read_text() != encoded:
        raise ValueError('immutable_data_regression_conflict')
    path.write_text(encoded)
    print(json.dumps({key: output[key] for key in ('new_company_count', 'new_company_period_rows',
          'checked_unique_sources', 'checked_unique_facts', 'checked_feature_references')}, sort_keys=True))
