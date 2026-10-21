-- messages 테이블과 SQLite user_version은 건드리지 않는다.
CREATE TABLE research_migrations (
    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
);
CREATE TABLE research_researches (
    id TEXT PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
    request_hash TEXT NOT NULL, company_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE research_bases (
    id TEXT PRIMARY KEY, research_id TEXT NOT NULL REFERENCES research_researches(id),
    version INTEGER NOT NULL, data_json TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(research_id, version)
);
CREATE TABLE research_runs (
    id TEXT PRIMARY KEY, basis_id TEXT NOT NULL REFERENCES research_bases(id),
    previous_run_id TEXT REFERENCES research_runs(id), status TEXT NOT NULL
        CHECK(status IN ('queued','running','stopping','stopped','finished',
                         'finished_with_gaps','interrupted','failed')),
    trigger_json TEXT NOT NULL, budget_json TEXT NOT NULL,
    usage_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE research_snapshots (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(id),
    revision INTEGER NOT NULL, stage TEXT NOT NULL, state_json TEXT NOT NULL,
    usage_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(run_id, revision)
);
CREATE TABLE research_sources (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(id),
    metadata_json TEXT NOT NULL, content_hash TEXT NOT NULL,
    byte_size INTEGER NOT NULL, storage_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE research_fragments (
    id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES research_sources(id),
    data_json TEXT NOT NULL, fragment_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE research_facts (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(id),
    fragment_id TEXT NOT NULL REFERENCES research_fragments(id),
    data_json TEXT NOT NULL, status TEXT NOT NULL CHECK(status='candidate'),
    created_at TEXT NOT NULL
);
CREATE TABLE research_reports (
    id TEXT PRIMARY KEY, research_id TEXT NOT NULL REFERENCES research_researches(id),
    run_id TEXT NOT NULL REFERENCES research_runs(id), version INTEGER NOT NULL,
    request_key TEXT NOT NULL, request_hash TEXT NOT NULL, content_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status='building'), created_at TEXT NOT NULL,
    UNIQUE(research_id, version), UNIQUE(research_id, request_key)
);
CREATE TABLE research_report_evidence (
    report_id TEXT NOT NULL REFERENCES research_reports(id),
    fact_id TEXT NOT NULL REFERENCES research_facts(id), PRIMARY KEY(report_id, fact_id)
);
CREATE TABLE research_events (
    research_id TEXT NOT NULL REFERENCES research_researches(id),
    sequence INTEGER NOT NULL, basis_id TEXT NOT NULL REFERENCES research_bases(id),
    run_id TEXT REFERENCES research_runs(id), kind TEXT NOT NULL,
    payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(research_id, sequence)
);
CREATE INDEX research_runs_basis ON research_runs(basis_id);
CREATE INDEX research_sources_run ON research_sources(run_id);
CREATE INDEX research_facts_run ON research_facts(run_id);
CREATE INDEX research_reports_research ON research_reports(research_id, version);
-- 기준·원문·값·스냅샷·보고 이력은 갱신 대신 새 행으로 남긴다.
CREATE TRIGGER research_basis_immutable BEFORE UPDATE ON research_bases
BEGIN SELECT RAISE(ABORT, 'immutable basis'); END;
CREATE TRIGGER research_source_immutable BEFORE UPDATE ON research_sources
BEGIN SELECT RAISE(ABORT, 'immutable source'); END;
CREATE TRIGGER research_fragment_immutable BEFORE UPDATE ON research_fragments
BEGIN SELECT RAISE(ABORT, 'immutable fragment'); END;
CREATE TRIGGER research_fact_immutable BEFORE UPDATE ON research_facts
BEGIN SELECT RAISE(ABORT, 'immutable fact'); END;
CREATE TRIGGER research_snapshot_immutable BEFORE UPDATE ON research_snapshots
BEGIN SELECT RAISE(ABORT, 'immutable snapshot'); END;
CREATE TRIGGER research_report_immutable BEFORE UPDATE ON research_reports
BEGIN SELECT RAISE(ABORT, 'immutable report'); END;
CREATE TRIGGER research_report_evidence_immutable BEFORE UPDATE ON research_report_evidence
BEGIN SELECT RAISE(ABORT, 'immutable report evidence'); END;
CREATE TRIGGER research_event_immutable BEFORE UPDATE ON research_events
BEGIN SELECT RAISE(ABORT, 'immutable event'); END;
