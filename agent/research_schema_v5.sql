-- 보고서/원문 이력은 보존하고 실행의 재개 위치만 변경한다.
CREATE TABLE research_executions (
    run_id TEXT NOT NULL REFERENCES research_runs(id), request_key TEXT NOT NULL,
    owner_id TEXT NOT NULL, phase TEXT NOT NULL,
    state_json TEXT NOT NULL, model_attempts INTEGER NOT NULL DEFAULT 0,
    model_pending INTEGER NOT NULL DEFAULT 0,
    recovery_failures INTEGER NOT NULL DEFAULT 0,
    recovering INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id,request_key)
);
