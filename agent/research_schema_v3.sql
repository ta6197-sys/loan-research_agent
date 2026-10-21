-- 원문 파일 쓰기를 DB에 먼저 예약한다. 기존 원문/보고 이력은 보존한다.
CREATE TABLE research_source_writes (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(id),
    metadata_json TEXT NOT NULL, content_hash TEXT NOT NULL, byte_size INTEGER NOT NULL,
    storage_key TEXT NOT NULL UNIQUE, temporary_key TEXT NOT NULL UNIQUE,
    root_digest TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('pending','committed','needs_review')),
    reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX research_source_writes_state ON research_source_writes(state);
CREATE TRIGGER research_source_write_descriptor_immutable BEFORE UPDATE OF
    id, run_id, metadata_json, content_hash, byte_size, storage_key, temporary_key, root_digest, created_at
ON research_source_writes
BEGIN SELECT RAISE(ABORT, 'immutable source write descriptor'); END;
