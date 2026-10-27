"""Validate the archived SK collection without external or model calls."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.finance.numeric_validation import NumericValidationService
from agent.finance.xbrl_mapping import XbrlMappingService
from agent.research_store import ResearchStore, safe_payload

PLAN = ROOT / "doc/finance/SK_COLLECTION_PLAN_20261026.json"
RESULT = ROOT / "doc/finance/SK_VALIDATION_RESULT_20261026.json"
RETRY_PLAN = ROOT / "doc/finance/SK_COLLECTION_RETRY_PLAN_20261026.json"
RETRY_RESULT = ROOT / "doc/finance/SK_VALIDATION_RETRY_RESULT_20261026.json"


def main():
    retry = "--retry" in sys.argv[1:]
    plan = json.loads((RETRY_PLAN if retry else PLAN).read_text())
    result_path = RETRY_RESULT if retry else RESULT
    store = ResearchStore(ROOT / "session.db", ROOT / "data/research")
    run_id = plan["run_id"]
    run = store.get_run(run_id)
    if run["status"] != "finished" or run["trigger"].get("collection_result", {}).get("next_phase") != "numeric_and_xbrl_validation":
        raise ValueError("collection_not_ready_for_validation")
    before = run["usage"]["external_requests"]
    numeric = NumericValidationService(store).validate_run(run_id)
    mapping = XbrlMappingService(store).validate_run(run_id)
    after = store.get_run(run_id)["usage"]["external_requests"]
    if before != after:
        raise ValueError("validation_made_external_request")
    output = {"as_of": "2026-10-26", "research_id": plan["research_id"], "run_id": run_id,
              "numeric_batch_id": numeric["id"], "numeric_policy_version": numeric["policy_version"],
              "numeric_summary": numeric["summary"], "mapping_batch_id": mapping["id"],
              "mapping_policy_version": mapping["policy_version"], "mapping_summary": mapping["summary"],
              "external_requests_added": 0, "model_calls_added": 0,
              "report_publication": "not_performed"}
    payload = (safe_payload(output) + "\n").encode()
    if result_path.exists():
        if result_path.read_bytes() != payload:
            raise ValueError("immutable_validation_result_conflict")
    else:
        result_path.write_bytes(payload)
    print(safe_payload({"status": "validated", "numeric_batch_id": numeric["id"],
                        "mapping_batch_id": mapping["id"], "fact_count": numeric["summary"].get("fact_count"),
                        "external_requests_added": 0}))


if __name__ == "__main__":
    main()
