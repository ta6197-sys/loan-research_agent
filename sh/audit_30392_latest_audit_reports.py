"""13개 후보 중 감사보고서가 있는 9곳의 최신 원문 한 건씩만 확인한다."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from agent.finance.dart_protocol import DartFailure, DartHTTP, zip_members
from agent.finance.dart_service import DartService
from agent.research_store import safe_payload

SOURCE = ROOT / "doc/finance/KSIC30392_DISCLOSURE_AVAILABILITY_20261026.json"
PLAN = ROOT / "doc/finance/KSIC30392_AUDIT_REPORT_PLAN_20261026.json"
RESULT = ROOT / "doc/finance/KSIC30392_AUDIT_REPORT_INSPECTION_20261026.json"
RAW = ROOT / "data/research/cohort_official_30392_20261026/audit_reports_latest"
MAX_REQUESTS = 9


def plan():
    source_bytes = SOURCE.read_bytes()
    source = json.loads(source_bytes)
    selected = []
    for company in source["records"]:
        if company["status"] != "audit_report_document_candidate":
            continue
        reports = [row for row in company["reports"] if row["kind"] == "audit_report"]
        latest = max(reports, key=lambda row: row["receipt_number"])
        selected.append({"corp_code": company["corp_code"], "corp_name": company["corp_name"],
                         "receipt_number": latest["receipt_number"],
                         "report_name": latest["report_name"],
                         "list_source_sha256": company["source_sha256"]})
    selected.sort(key=lambda row: row["corp_code"])
    if len(selected) != MAX_REQUESTS or len({row["receipt_number"] for row in selected}) != MAX_REQUESTS:
        raise ValueError("expected_nine_distinct_latest_audit_reports")
    return {"as_of": source["public_cutoff"], "operation": "OpenDART document.xml",
            "request_limit": MAX_REQUESTS, "selected_count": len(selected),
            "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "selected": selected,
            "limitation": "원문 재무표 존재 확인만 수행한다. 숫자·기간·결산월·XBRL 검증이나 코호트 편입은 하지 않는다."}


def freeze(path, payload):
    content = (safe_payload(payload) + "\n").encode()
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError("immutable_audit_artifact_conflict")
    else:
        DartService._atomic_write(path, content)


async def main():
    load_dotenv(ROOT / ".env")
    key = os.environ.get("OPENDART_API_KEY")
    if not key:
        print('{"status":"missing_configuration"}')
        return 1
    selection = plan()
    freeze(PLAN, selection)
    RAW.mkdir(parents=True, exist_ok=True)
    http = DartHTTP(key, max_response_bytes=50 * 1024 * 1024)
    rows = []
    calls = 0
    for company in selection["selected"]:
        receipt = company["receipt_number"]
        path = RAW / (receipt + ".zip")
        try:
            if path.exists():
                content = path.read_bytes()
                DartHTTP._decode(content, "document")
            else:
                if calls >= MAX_REQUESTS:
                    raise ValueError("request_limit_reached")
                calls += 1
                response = await http.request("document", {"rcept_no": receipt},
                                              before_request=lambda: asyncio.sleep(0))
                content = response.content
                # 응답은 안전한 ZIP 검사 뒤에만 원문으로 보관한다.
                zip_members(content, max_expanded_bytes=256 * 1024 * 1024, max_members=100)
                DartService._atomic_write(path, content)
            members = zip_members(content, max_expanded_bytes=256 * 1024 * 1024, max_members=100)
            markers = []
            for name, raw in members:
                decoded = None
                for encoding in ("utf-8", "cp949", "euc-kr"):
                    try:
                        decoded = raw.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        continue
                if decoded and re.search("재무상태표|손익계산서|현금흐름표|재무제표", decoded):
                    markers.append({"member_name": name, "member_sha256": hashlib.sha256(raw).hexdigest()})
            rows.append({"corp_code": company["corp_code"], "receipt_number": receipt,
                         "status": "statement_marker_found" if markers else "statement_marker_not_found",
                         "raw_sha256": hashlib.sha256(content).hexdigest(),
                         "archive_member_count": len(members), "marker_members": markers,
                         "numeric_validation_status": "not_started", "cohort_eligible": False})
        except DartFailure as error:
            rows.append({"corp_code": company["corp_code"], "receipt_number": receipt,
                         "status": error.kind, "numeric_validation_status": "not_started",
                         "cohort_eligible": False})
            if error.kind in {"authentication_error", "rate_limited", "access_denied"}:
                break
        print(safe_payload({"corp_code": company["corp_code"], "status": rows[-1]["status"],
                            "requests": calls}), flush=True)
    output = {"as_of": selection["as_of"], "plan_sha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(),
              "external_requests": calls, "records": rows,
              "limitation": "문자열 표식은 재무표가 그 원문에 있다는 추가 검토 후보일 뿐 계정·기간·금액 검증이 아니다."}
    if len(rows) == MAX_REQUESTS:
        if RESULT.exists():
            previous = json.loads(RESULT.read_text())
            if (previous.get("records") != rows or previous.get("plan_sha256") != output["plan_sha256"]):
                raise ValueError("immutable_audit_artifact_conflict")
            print(safe_payload({"status": "previous_result_revalidated", "new_requests": calls}), flush=True)
            return 0
        freeze(RESULT, output)
        return 0
    print(safe_payload({"status": "incomplete", "completed": len(rows),
                        "request_count": calls}), flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
