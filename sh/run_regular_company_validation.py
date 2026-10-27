"""Bounded, immutable collection and validation for one regular-report company.

Usage: plan --name ... --corp-code ... --stock-code ... --cutoff ... --prefix doc/finance/NAME
       collect --prefix doc/finance/NAME
       validate --prefix doc/finance/NAME
"""
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
from agent.finance.numeric_validation import NumericValidationService
from agent.finance.xbrl_mapping import XbrlMappingService
from agent.finance.report_context import fiscal_periods, numeric_rows, xbrl_report_gate
from agent.research_models import Budget
from agent.research_store import ResearchStore, safe_payload


def freeze(path, value):
    content = (safe_payload(value) + "\n").encode()
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError("immutable_result_conflict")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("plan", "retry-plan", "collect", "validate"))
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--name")
    parser.add_argument("--corp-code")
    parser.add_argument("--stock-code")
    parser.add_argument("--cutoff")
    parser.add_argument("--retry", action="store_true")
    args = parser.parse_args()
    prefix = (args.prefix if args.prefix.is_absolute() else ROOT / args.prefix).resolve()
    if not prefix.is_relative_to(ROOT / "doc/finance"):
        raise ValueError("manifest_prefix_must_be_under_doc_finance")
    suffix = "_RETRY" if args.retry or args.action == "retry-plan" else ""
    paths = {kind: Path(str(prefix) + suffix + "_" + kind.upper() + ".json") for kind in ("plan", "collection", "validation")}
    store = ResearchStore(ROOT / "session.db", ROOT / "data/research")
    if args.action == "plan":
        if not all((args.name, args.corp_code, args.cutoff)):
            raise ValueError("company_name_code_and_cutoff_required")
        if paths["plan"].exists():
            print(safe_payload(json.loads(paths["plan"].read_text())))
            return
        value = GeneralStartInput(company_name=args.name, corp_code=args.corp_code,
                                  stock_code=args.stock_code, public_cutoff=args.cutoff,
                                  request_key="regular-validation:" + prefix.name,
                                  budget=Budget(external_requests=60, model_calls=0,
                                                writing_reserve_calls=0, execution_seconds=1800))
        receipt = receive(store, value)
        freeze(paths["plan"], receipt)
        print(safe_payload({"status": "planned", "run_id": receipt["run_id"], "external_request_limit": 60}))
        return
    if args.action == "retry-plan":
        original = json.loads(Path(str(prefix) + "_PLAN.json").read_text())
        if paths["plan"].exists():
            retry = json.loads(paths["plan"].read_text())
        else:
            retry = create_collection_retry(store, original["research_id"])
            freeze(paths["plan"], retry)
        print(safe_payload({"status": "retry_planned", "run_id": retry["run_id"],
                            "previous_run_id": retry["previous_run_id"]}))
        return
    plan = json.loads(paths["plan"].read_text())
    run_id = plan["run_id"]
    run = store.get_run(run_id)
    if args.action == "collect":
        if paths["collection"].exists():
            print(safe_payload({"status": "already_collected", "run_id": run_id}))
            return
        if run["status"] != "queued":
            raise ValueError("unexpected_collection_run_status")
        load_dotenv(ROOT / ".env")
        if not os.environ.get("OPENDART_API_KEY"):
            raise ValueError("opendart_key_missing")
        outcome = asyncio.run(GeneralCollectionService(store).run(run_id))
        result = {"run_id": run_id, "research_id": plan["research_id"],
                  "collection_result": outcome, "usage": store.get_run(run_id)["usage"],
                  "financial_verification": "not_performed"}
        freeze(paths["collection"], result)
        print(safe_payload({"status": "collected", "run_id": run_id,
                            "external_requests": result["usage"]["external_requests"],
                            "next_phase": outcome.get("next_phase")}))
        return
    if paths["validation"].exists():
        print(safe_payload({"status": "already_validated", "run_id": run_id}))
        return
    collection = run['trigger'].get('collection_result', {})
    if run['status'] != 'finished' or collection.get('next_phase') != 'numeric_and_xbrl_validation':
        raise ValueError('collection_not_ready_for_validation')
    before = run['usage']['external_requests']
    numeric = NumericValidationService(store).validate_run(run_id)
    mapping = XbrlMappingService(store).validate_run(run_id)
    if store.get_run(run_id)['usage']['external_requests'] != before:
        raise ValueError('validation_made_external_request')
    fiscal = collection.get('fiscal_period', {})
    if fiscal.get('status') not in {'supported', 'identity_only'}:
        raise ValueError('fiscal_month_review_required')
    periods = fiscal_periods(collection['target_reports'], fiscal['month'])
    rows = numeric_rows(numeric, tuple(p['end'] for p in periods), periods)
    gate = xbrl_report_gate(rows, numeric, mapping)
    result = {"run_id": run_id, "research_id": plan["research_id"],
              "numeric_batch_id": numeric['id'], "numeric_policy_version": numeric['policy_version'],
              "numeric_summary": numeric['summary'], "mapping_batch_id": mapping['id'],
              "mapping_policy_version": mapping['policy_version'], "mapping_summary": mapping['summary'],
              "report_gate": gate, "row_counts": {"verified": sum(r['status'] == 'verified' for r in rows),
                                                   "withheld": sum(r['status'] != 'verified' for r in rows)},
              "external_requests_added": 0, "model_calls_added": 0, "report_publication": "not_performed"}
    freeze(paths["validation"], result)
    print(safe_payload({"status": "validated", "run_id": run_id,
                        "verified_rows": result['row_counts']['verified'], "xbrl_gate": gate['status']}))


if __name__ == "__main__":
    main()
