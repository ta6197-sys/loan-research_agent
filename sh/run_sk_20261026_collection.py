"""Create and collect a bounded, immutable SK regular-disclosure research run."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from agent.finance.general_company import GeneralStartInput, receive, create_collection_retry
from agent.finance.general_collection import GeneralCollectionService
from agent.research_models import Budget
from agent.research_store import ResearchStore, safe_payload

PLAN = ROOT / "doc/finance/SK_COLLECTION_PLAN_20261026.json"
RESULT = ROOT / "doc/finance/SK_COLLECTION_RESULT_20261026.json"
RETRY_PLAN = ROOT / "doc/finance/SK_COLLECTION_RETRY_PLAN_20261026.json"
RETRY_RESULT = ROOT / "doc/finance/SK_COLLECTION_RETRY_RESULT_20261026.json"


def freeze(path, data):
    payload = (safe_payload(data) + "\n").encode()
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError("immutable_result_conflict")
    else:
        path.write_bytes(payload)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("plan", "collect", "retry-plan", "retry-collect"))
    args = parser.parse_args()
    store = ResearchStore(ROOT / "session.db", ROOT / "data/research")
    if args.action == "plan":
        value = GeneralStartInput(
            company_name="SK", corp_code="00181712", stock_code="034730",
            public_cutoff="2026-10-26", request_key="sk-regular-20261026-v1",
            budget=Budget(external_requests=60, model_calls=0, writing_reserve_calls=0,
                          execution_seconds=1800),
        )
        receipt = receive(store, value)
        freeze(PLAN, receipt)
        print(safe_payload({"status": "planned", "research_id": receipt["research_id"],
                            "run_id": receipt["run_id"], "external_request_limit": 60}))
        return
    if args.action == "retry-plan":
        if not RETRY_PLAN.exists():
            original = json.loads(PLAN.read_text())
            retry = create_collection_retry(store, original["research_id"])
            freeze(RETRY_PLAN, retry)
        retry = json.loads(RETRY_PLAN.read_text())
        print(safe_payload({"status": "retry_planned", "run_id": retry["run_id"],
                            "previous_run_id": retry["previous_run_id"],
                            "external_request_limit": 60}))
        return
    plan_path, result_path = (RETRY_PLAN, RETRY_RESULT) if args.action == "retry-collect" else (PLAN, RESULT)
    plan = json.loads(plan_path.read_text())
    run = store.get_run(plan["run_id"])
    if run["status"] == "finished":
        print(safe_payload({"status": "already_finished", "run_id": run["id"]}))
        return
    if run["status"] != "queued":
        raise ValueError("unexpected_run_status")
    load_dotenv(ROOT / ".env")
    if not os.environ.get("OPENDART_API_KEY"):
        raise ValueError("opendart_key_missing")
    outcome = asyncio.run(GeneralCollectionService(store).run(run["id"]))
    result = {"as_of": "2026-10-26", "run_id": run["id"], "research_id": plan["research_id"],
              "collection_result": outcome, "usage": store.get_run(run["id"])["usage"],
              "financial_verification": "not_performed"}
    freeze(result_path, result)
    print(safe_payload({"status": "collected", "run_id": run["id"],
                        "external_requests": result["usage"]["external_requests"],
                        "next_phase": outcome.get("next_phase")}))


if __name__ == "__main__":
    main()
