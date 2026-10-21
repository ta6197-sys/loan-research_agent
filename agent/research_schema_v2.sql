-- 기존 후보·대화·보고 이력을 보존하는 추가 마이그레이션.
CREATE TABLE research_validation_batches (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES research_runs(id),
    fingerprint TEXT NOT NULL, policy_version TEXT NOT NULL,
    manifest_json TEXT NOT NULL, summary_json TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(run_id, fingerprint)
);
CREATE TABLE research_validations (
    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES research_validation_batches(id),
    fact_id TEXT REFERENCES research_facts(id), rule_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pass','fail','not_applicable','not_checked')),
    data_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE research_calculations (
    id TEXT PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES research_validation_batches(id),
    formula_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pass','fail','not_applicable','not_checked')),
    data_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE research_calculation_inputs (
    calculation_id TEXT NOT NULL REFERENCES research_calculations(id),
    role TEXT NOT NULL, validation_id TEXT NOT NULL REFERENCES research_validations(id),
    fact_id TEXT NOT NULL REFERENCES research_facts(id), PRIMARY KEY(calculation_id, role)
);
CREATE INDEX research_validation_run ON research_validation_batches(run_id);
CREATE INDEX research_validation_fact ON research_validations(fact_id);
CREATE TRIGGER research_validation_batch_immutable BEFORE UPDATE ON research_validation_batches
BEGIN SELECT RAISE(ABORT, 'immutable validation batch'); END;
CREATE TRIGGER research_validation_immutable BEFORE UPDATE ON research_validations
BEGIN SELECT RAISE(ABORT, 'immutable validation'); END;
CREATE TRIGGER research_calculation_immutable BEFORE UPDATE ON research_calculations
BEGIN SELECT RAISE(ABORT, 'immutable calculation'); END;
CREATE TRIGGER research_calculation_input_immutable BEFORE UPDATE ON research_calculation_inputs
BEGIN SELECT RAISE(ABORT, 'immutable calculation input'); END;
