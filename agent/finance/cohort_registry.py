"""명시적으로 등록하는 비교집단 정책. 기존 검증·보고서 테이블과 독립한다."""
import json
import re
from datetime import date
from agent.research_store import Conflict, IntegrityFailure, digest, encode

SCHEMA = '''
CREATE TABLE IF NOT EXISTS analytics_cohorts(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analytics_snapshots(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analytics_report_bindings(report_id TEXT PRIMARY KEY, snapshot_id TEXT NOT NULL);
'''


def initialize(store):
    with store.connection(write=True) as conn:
        for statement in SCHEMA.split(';'):
            if statement.strip(): conn.execute(statement)
        for table in ('analytics_cohorts', 'analytics_snapshots', 'analytics_report_bindings'):
            for action in ('UPDATE', 'DELETE'):
                conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable analytics record'); END")


class CohortRegistry:
    def __init__(self, store): self.store = store

    def register(self, policy):
        required = ('industry_system', 'industry_version', 'industry_code', 'accounting_standard',
                    'scope', 'periods', 'public_cutoff', 'policy_version', 'members')
        if any(not policy.get(key) for key in required): raise ValueError('incomplete_cohort_policy')
        if policy['scope'] not in ('OFS', 'CFS'): raise ValueError('invalid_scope')
        if policy['industry_system'] == 'KSIC':
            if policy['industry_version'] != '11' or not re.fullmatch(r'\d{5}', str(policy['industry_code'])):
                raise ValueError('unsupported_ksic_classification')
            if not policy.get('industry_name'):
                raise ValueError('industry_name_required')
            if any(not item.get('industry_evidence') for item in policy['members']):
                raise ValueError('member_industry_evidence_required')
        date.fromisoformat(policy['public_cutoff'])
        companies = [item.get('corp_code', '') for item in policy['members']]
        if len(set(companies)) != len(companies) or any(not re.fullmatch(r'\d{8}', code) for code in companies):
            raise ValueError('invalid_or_duplicate_company')
        for period in policy['periods']:
            start, end = date.fromisoformat(period['start']), date.fromisoformat(period['end'])
            if start > end or period.get('kind') != 'duration': raise ValueError('invalid_cohort_period')
        payload = json.loads(encode(policy)); identifier = digest(encode(payload).encode())
        initialize(self.store)
        with self.store.connection(write=True) as conn:
            conn.execute('INSERT OR IGNORE INTO analytics_cohorts VALUES (?,?)', (identifier, encode(payload)))
        return {'cohort_id': identifier, **payload}

    def get(self, identifier):
        with self.store.connection() as conn:
            row = conn.execute('SELECT payload FROM analytics_cohorts WHERE id=?', (identifier,)).fetchone()
        if row is None: raise Conflict('cohort_not_registered')
        policy = json.loads(row[0])
        if digest(encode(policy).encode()) != identifier: raise IntegrityFailure('cohort_hash_mismatch')
        return {'cohort_id': identifier, **policy}
