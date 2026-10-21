-- 기존 보고서 내용을 수정하지 않고 검증·게시 완료 이력을 별도로 남긴다.
CREATE TABLE research_report_publications (
    report_id TEXT PRIMARY KEY REFERENCES research_reports(id),
    validation_batch_id TEXT NOT NULL REFERENCES research_validation_batches(id),
    content_hash TEXT NOT NULL, verification_json TEXT NOT NULL,
    published_at TEXT NOT NULL
);
CREATE TRIGGER research_publication_immutable BEFORE UPDATE ON research_report_publications
BEGIN SELECT RAISE(ABORT, 'immutable report publication'); END;
