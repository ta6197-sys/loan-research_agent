-- 데이터 품질 예외에 대한 로컬 검토 이력. 검증·보고·원문 행은 수정하지 않는다.
CREATE TABLE research_quality_review_events (
    id TEXT PRIMARY KEY,
    research_id TEXT NOT NULL REFERENCES research_researches(id),
    batch_id TEXT NOT NULL REFERENCES research_validation_batches(id),
    exception_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('acknowledged','investigating','evidence_requested','note_added','closed_unresolved')),
    note TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK(actor_type='local_demo_operator'),
    created_at TEXT NOT NULL
);
CREATE INDEX research_quality_review_lookup ON research_quality_review_events(research_id,batch_id,exception_id,created_at);
CREATE TRIGGER research_quality_review_immutable BEFORE UPDATE ON research_quality_review_events
BEGIN SELECT RAISE(ABORT, 'immutable quality review event'); END;
CREATE TRIGGER research_quality_review_no_delete BEFORE DELETE ON research_quality_review_events
BEGIN SELECT RAISE(ABORT, 'immutable quality review event'); END;
