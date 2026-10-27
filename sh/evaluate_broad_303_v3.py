"""Create an immutable V12 successor snapshot from archived verified runs."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.finance.analytics_dataset_service import AnalyticsDatasetService
from agent.finance.model_readiness import evaluate as model_readiness
from agent.finance.peer_dataset import FEATURE_POLICY_VERSION, build_snapshot
from agent.finance.statistical_analysis import analyze as statistical_analyze
from agent.finance.verified_run_peer import build_run
from agent.research_store import ResearchStore

DOC = ROOT / 'doc/finance'
V2_AUDIT = json.loads((DOC / 'BROAD_303_EXPANDED_V2_AUDIT_20261027.json').read_text())
CANDIDATES = {item['corp_code']: item for item in
              json.loads((DOC / 'BROAD_303_CANDIDATES_20261027.json').read_text())['records']}
STORES = {
    'main': ResearchStore(ROOT / 'session.db', ROOT / 'data/research'),
    'isolated': ResearchStore(ROOT / 'data/research/broad_303_20261027/session.db',
                              ROOT / 'data/research/broad_303_20261027/sources'),
    'increment': ResearchStore(ROOT / 'data/research/broad_303_increment_20261027/session.db',
                               ROOT / 'data/research/broad_303_increment_20261027/sources'),
}


def immutable(path, payload):
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
    if path.exists() and path.read_text() != content:
        raise ValueError('immutable_v3_conflict:' + path.name)
    path.write_text(content)


def key(row):
    return row['corp_code'], row['scope'], row['period_start'], row['period_end']


def verify_successor(previous, current):
    before, after = {key(row): row for row in previous['rows']}, {key(row): row for row in current['rows']}
    if set(before) != set(after):
        raise ValueError('v3_company_period_membership_changed')
    additions = []
    for identity in sorted(before):
        old, new = before[identity], after[identity]
        for field in old:
            if field in {'features', 'missing_features', 'feature_evidence', 'feature_policy_version',
                         'source_manifest', 'source_hash', 'source_public_at'}:
                continue
            if old[field] != new[field]:
                raise ValueError('v3_existing_row_changed:' + str(identity) + ':' + field)
        if not set(old['source_manifest'][0]) <= set(new['source_manifest'][0]):
            raise ValueError('v3_manifest_schema_changed')
        old_manifest = {item['source_id']: item for item in old['source_manifest']}
        new_manifest = {item['source_id']: item for item in new['source_manifest']}
        if any(new_manifest.get(source_id) != source for source_id, source in old_manifest.items()):
            raise ValueError('v3_prior_source_evidence_changed:' + str(identity))
        if old['source_public_at'] > new['source_public_at']:
            raise ValueError('v3_source_public_time_regressed')
        for field in ('features', 'feature_evidence'):
            if {name: value for name, value in new[field].items() if name != 'borrowing_to_assets'} != old[field]:
                raise ValueError('v3_prior_feature_changed:' + str(identity) + ':' + field)
        if {name: value for name, value in new['missing_features'].items()
                if name != 'borrowing_to_assets'} != {
                name: value for name, value in old['missing_features'].items()
                if name != 'borrowing_to_assets'}:
            raise ValueError('v3_prior_missing_reason_changed:' + str(identity))
        if new['feature_policy_version'] != FEATURE_POLICY_VERSION:
            raise ValueError('v3_feature_policy_version_mismatch')
        if 'borrowing_to_assets' in new['features']:
            additions.append({'corp_code': identity[0], 'scope': identity[1],
                              'period_end': identity[3],
                              'value_percent': new['features']['borrowing_to_assets'],
                              'evidence_refs': new['feature_evidence']['borrowing_to_assets']})
    return additions


def main():
    records = []
    for review in V2_AUDIT['company_reviews']:
        if not review['eligible_observations']:
            continue
        result = build_run(STORES[review['source_store']], review['run_id'],
                           CANDIDATES[review['corp_code']])
        if len(result['records']) != review['eligible_observations'] or result['excluded'] != review['exclusions']:
            raise ValueError('v3_replay_company_review_changed:' + review['corp_code'])
        records.extend(result['records'])
    scopes, additions = {}, []
    outputs = {}
    for scope in ('OFS', 'CFS'):
        previous = json.loads((DOC / f'BROAD_303_EXPANDED_V2_{scope}_20261027.json').read_text())
        snapshot = build_snapshot([record for record in records if record['scope'] == scope])
        if snapshot['excluded_records']:
            raise ValueError('v3_unexpected_snapshot_exclusion')
        additions.extend(verify_successor(previous, snapshot))
        readiness = model_readiness(snapshot)
        observations = AnalyticsDatasetService._observations(snapshot)
        statistics = {code: statistical_analyze(code, observations,
                      analysis_as_of=V2_AUDIT['public_cutoff'])
                      for code in sorted({row['corp_code'] for row in snapshot['rows']})}
        measured = sum(item['status'] == 'measured' for result in statistics.values()
                       for item in result['peer_comparisons'])
        path = DOC / f'BROAD_303_EXPANDED_V3_{scope}_20261027.json'
        outputs[scope] = (path, snapshot)
        scopes[scope] = {'snapshot_file': path.name, 'snapshot_id': snapshot['snapshot_id'],
                         'row_count': snapshot['row_count'],
                         'distinct_companies': len({row['corp_code'] for row in snapshot['rows']}),
                         'annual_row_count': readiness['annual_row_count'],
                         'training_status': readiness['training_status'],
                         'industry_revision_gate': readiness['checks']['industry_revision'],
                         'measured_peer_comparisons': measured}
    actual = {(item['corp_code'], item['scope'], item['period_end']) for item in additions}
    expected = {('00102353', 'OFS', '2023-12-31'), ('00102353', 'OFS', '2025-06-30')}
    if actual != expected:
        raise ValueError('v3_v12_successor_findings_changed')
    for path, snapshot in outputs.values():
        immutable(path, snapshot)
    audit = {'schema_version': 'broad-303-expanded-v3-audit-1.0',
             'public_cutoff': V2_AUDIT['public_cutoff'],
             'feature_policy_version': FEATURE_POLICY_VERSION,
             'prior_audit': 'BROAD_303_EXPANDED_V2_AUDIT_20261027.json',
             'prior_snapshot_ids': {scope: V2_AUDIT['scopes'][scope]['snapshot_id']
                                    for scope in ('OFS', 'CFS')},
             'classification_revision': 'unconfirmed',
             'not_ksic30392_snapshot': True,
             'company_reviews': V2_AUDIT['company_reviews'], 'scopes': scopes,
             'v12_additions': additions, 'new_external_requests': 0, 'new_model_calls': 0,
             'limitations': ['v2와 같은 공개 기준일·기업기간을 보관 검증 실행에서 재계산했습니다.',
                             'XBRL 업종코드의 KSIC 개정 차수는 미확인입니다.',
                             '차입 비율과 탐색 비교는 신용위험 또는 대출 판단이 아닙니다.']}
    immutable(DOC / 'BROAD_303_EXPANDED_V3_AUDIT_20261027.json', audit)
    print(json.dumps({'scopes': scopes, 'v12_additions': len(additions)},
                     ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
