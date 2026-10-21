#!/usr/bin/env python3
"""승인된 보관 공개 공시만으로 OpenAI 5단계 게시 흐름을 한 번 검증한다.

원본 DB와 원문은 읽기만 하고, 임시 DB는 종료 즉시 삭제한다. 결과 파일에는 수치·원문·키를
쓰지 않고 실행 상태와 해시·개수만 남긴다. --execute 없이는 어떤 모델 요청도 하지 않는다.
"""
import argparse
import asyncio
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT))

from agent.finance.cached_case import CachedCaseLibrary, CachedStartInput, preferred_manifest
from agent.finance.openai_model import finance_model_from_environment
from agent.finance.research_stage05 import Stage05Service
from agent.research_store import ResearchStore


def write_record(data):
    directory = ROOT / "data/research/evaluation_openai"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"stage05-{stamp}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return path


async def execute():
    if os.environ.get("FINANCE_ALLOW_ARCHIVED_PUBLIC_EXTERNAL_MODEL") != "true":
        raise ValueError("archived_public_external_model_not_explicitly_allowed")
    model = finance_model_from_environment(ROOT)
    if not getattr(model, "allow_archived_public_override", False):
        raise ValueError("archived_public_external_model_not_enabled")
    with tempfile.TemporaryDirectory(prefix="finance-openai-stage05-") as temporary:
        store = ResearchStore(Path(temporary) / "evaluation.sqlite3", Path(temporary) / "sources")
        library = CachedCaseLibrary(ROOT, preferred_manifest(ROOT))
        receipt = library.receive(store, CachedStartInput(
            company="상신브레이크", purpose="OpenAI 공개 공시 초안 통합 검증",
            request_key="openai-stage05-e2e", budget={
                "external_requests": 0, "model_calls": 1, "execution_seconds": 1800,
                "writing_reserve_calls": 1, "writing_reserve_seconds": 300,
            }))
        report = await Stage05Service(store, model, case_library=library).run(
            receipt["run_id"], "cached-case-initial")
        run = store.get_run(receipt["run_id"])
        context = report["content"]["context"]
        if report["status"] != "published" or report["content"]["model_status"] != "validated":
            raise ValueError("openai_stage05_not_published")
        if run["usage"]["model_calls"] != 1 or run["usage"]["external_requests"] != 0:
            raise ValueError("unexpected_stage05_usage")
        return {
            "mode": "user_approved_archived_public_override", "model": model.model,
            "status": "passed", "report_status": report["status"], "model_status": report["content"]["model_status"],
            "model_calls": run["usage"]["model_calls"], "external_financial_requests": run["usage"]["external_requests"],
            "verified_evidence_count": sum(e["state"] == "verified" for e in context["model_evidence"]),
            "withheld_evidence_count": sum(e["state"] == "withheld" for e in context["model_evidence"]),
            "report_content_hash": hashlib.sha256(json.dumps(report["content"], ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="명시적으로 승인한 OpenAI API 1회 실행")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"status": "not_executed", "reason": "pass_--execute_to_allow_one_openai_call"}, ensure_ascii=False))
        return
    base = {"created_at": datetime.now(timezone.utc).isoformat(), "workflow": "stage05_openai_archived_public"}
    try:
        result = asyncio.run(execute())
        path = write_record({**base, **result})
        print(json.dumps({**result, "record": str(path.relative_to(ROOT))}, ensure_ascii=False))
    except Exception as error:
        path = write_record({**base, "status": "failed", "error_type": type(error).__name__, "reason": str(error)})
        raise SystemExit(json.dumps({"status": "failed", "error_type": type(error).__name__, "record": str(path.relative_to(ROOT))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
