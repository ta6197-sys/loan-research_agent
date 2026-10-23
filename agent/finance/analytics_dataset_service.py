"""명시적 생성과 불변 스냅샷 조회를 분리한 로컬 분석 서비스."""
import json
from agent.research_store import Conflict, IntegrityFailure, digest, encode
from .cohort_registry import CohortRegistry, initialize
from .verified_peer_builder import build_report
from .peer_dataset import build_snapshot
from .statistical_analysis import analyze
from .model_readiness import evaluate
from .anomaly_detection import detect


class AnalyticsDatasetService:
    def __init__(self, store): self.store = store

    def build(self, cohort_id, report_ids):
        cohort = CohortRegistry(self.store).get(cohort_id)
        records, excluded, linked = [], [], []
        members = {m['corp_code']: m for m in cohort['members']}
        for report_id in sorted(set(report_ids)):
            try:
                report = self.store.get_report(report_id)
                research = self.store.get_research(report['research_id'])
                company = research['company']['corp_code']
                if company not in members: raise ValueError('company_outside_registered_cohort')
                rows, issues = build_report(self.store, report_id, members[company], cohort)
                records.extend(rows); excluded.extend(issues); linked.append(report_id)
            except IntegrityFailure: raise
            except (ValueError, KeyError) as error:
                excluded.append({'report_id': report_id, 'reason': str(error)})
        snapshot = build_snapshot(records)
        snapshot.update(cohort=cohort, build_exclusions=excluded, report_ids=linked)
        # 모든 metadata까지 해시로 봉인한다. 입력 행 순서와 무관하다.
        identifier = digest(encode(snapshot).encode())
        snapshot['artifact_id'] = identifier
        statistical = {}
        observations = self._observations(snapshot)
        for company in sorted(members):
            result = analyze(company, observations, analysis_as_of=cohort['public_cutoff'])
            result['status'] = 'measured' if any(p['status'] == 'measured' for p in result['peer_comparisons']) else 'insufficient_peer_data'
            result['reason_code'] = None if result['status'] == 'measured' else 'comparable_verified_sample_below_gate'
            result['comparison_definition'] = cohort
            statistical[company] = result
        payload = {'snapshot': snapshot, 'ml_readiness': evaluate(snapshot), 'anomaly_detection': detect(snapshot), 'statistics': statistical}
        payload_hash = digest(encode(payload).encode())
        initialize(self.store)
        with self.store.connection(write=True) as conn:
            conn.execute('INSERT OR IGNORE INTO analytics_snapshots VALUES (?,?)', (payload_hash, encode(payload)))
        return {'artifact_id': payload_hash, **payload}

    @staticmethod
    def _observations(snapshot):
        return [{'company_id': row['corp_code'], 'industry_group': row['peer_group'], 'metric': name,
                 'value': value, 'scope': row['scope'], 'period_kind': row['period_kind'], 'period_end': row['period_end'],
                 'currency': row['currency'], 'accounting_standard': row['accounting_standard'],
                 'public_at': row['source_public_at'], 'verification_status': 'supported',
                 'evidence_refs': row['feature_evidence'][name]} for row in snapshot['rows'] for name, value in row['features'].items()]

    def bind(self, report_id, artifact_id):
        payload = self.get(artifact_id)
        report = self.store.get_report(report_id)
        company = self.store.get_research(report['research_id'])['company']['corp_code']
        cutoff = report['content'].get('context', {}).get('public_cutoff')
        if not any(row.get('report_id') == report_id and row.get('corp_code') == company
                   for row in payload['snapshot']['rows']):
            raise Conflict('snapshot_report_has_no_verified_observation')
        if (report_id not in payload['snapshot']['report_ids'] or company not in payload['statistics']
                or cutoff != payload['snapshot']['cohort']['public_cutoff']): raise Conflict('snapshot_report_or_cutoff_mismatch')
        with self.store.connection(write=True) as conn:
            old = conn.execute('SELECT snapshot_id FROM analytics_report_bindings WHERE report_id=?', (report_id,)).fetchone()
            if old and old[0] != artifact_id: raise Conflict('report_analysis_already_frozen')
            conn.execute('INSERT OR IGNORE INTO analytics_report_bindings VALUES (?,?)', (report_id, artifact_id))

    def get(self, artifact_id):
        with self.store.connection() as conn:
            row = conn.execute('SELECT payload FROM analytics_snapshots WHERE id=?', (artifact_id,)).fetchone()
        if row is None: raise Conflict('analytics_snapshot_missing')
        payload = json.loads(row[0])
        if digest(encode(payload).encode()) != artifact_id: raise IntegrityFailure('analytics_snapshot_hash_mismatch')
        for record in payload['snapshot']['rows']:
            for source in record.get('source_manifest', []):
                if digest(self.store.source_content(source['source_id'])) != source['source_hash']:
                    raise IntegrityFailure('analytics_original_hash_mismatch')
        return {'artifact_id': artifact_id, **payload}

    def for_report(self, report_id):
        self.store.get_report(report_id)
        with self.store.connection() as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='analytics_report_bindings'").fetchone()
            row = conn.execute('SELECT snapshot_id FROM analytics_report_bindings WHERE report_id=?', (report_id,)).fetchone() if exists else None
        return self.get(row[0]) if row else None
