"""Read the fixed exploratory 303 snapshot without touching operational data."""
import hashlib
import json
from pathlib import Path

from .analytics_dataset_service import AnalyticsDatasetService
from .statistical_analysis import analyze

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / 'doc/finance/BROAD_303_EXPANDED_AUDIT_20261027.json'
SNAPSHOTS = {scope: ROOT / f'doc/finance/BROAD_303_EXPANDED_{scope}_20261027.json'
             for scope in ('OFS', 'CFS')}


def read_company(corp_code: str, scope: str = 'OFS'):
    if scope not in SNAPSHOTS:
        raise ValueError('unsupported_scope')
    audit = json.loads(AUDIT.read_text())
    snapshot = json.loads(SNAPSHOTS[scope].read_text())
    expected = audit['scopes'][scope]
    stable = json.dumps(snapshot['rows'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    actual_hash = hashlib.sha256(stable.encode()).hexdigest()
    if (actual_hash != snapshot['snapshot_id'] or actual_hash != expected['snapshot_id']
            or snapshot['row_count'] != len(snapshot['rows'])
            or snapshot['row_count'] != expected['row_count']):
        raise ValueError('exploratory_snapshot_integrity_failed')
    rows = [row for row in snapshot['rows'] if row['corp_code'] == corp_code]
    if not rows:
        return None
    if any(row.get('industry_version') != 'unconfirmed' or
           row.get('peer_group') != 'exploratory_observed_303_family' or
           row.get('verification_gates', {}).get('independent_xbrl') != 'pass'
           for row in snapshot['rows']):
        raise ValueError('exploratory_snapshot_policy_failed')
    observations = AnalyticsDatasetService._observations(snapshot)
    statistics = analyze(corp_code, observations, analysis_as_of=audit['public_cutoff'])
    comparisons = statistics['peer_comparisons']
    return {
        'schema_version': 'broad-303-read-model-1.0',
        'cohort_id': 'exploratory_observed_303_family',
        'corp_code': corp_code, 'scope': scope,
        'public_cutoff': audit['public_cutoff'],
        'snapshot_id': snapshot['snapshot_id'],
        'company_period_count': len(rows),
        'peer_company_count': expected['distinct_companies'],
        'peer_company_period_count': expected['row_count'],
        'classification_revision': audit['classification_revision'],
        'ml_training_status': expected['training_status'],
        'measured_comparison_count': sum(row['status'] == 'measured' for row in comparisons),
        'peer_comparisons': comparisons,
        'limitations': audit['limitations'] + [
            '원문과 검증 결과는 생성 당시 격리 DB에서 확인했습니다. 이 API는 불변 분석 스냅샷을 읽으며 최신 공시를 재조회하지 않습니다.'
        ],
    }
