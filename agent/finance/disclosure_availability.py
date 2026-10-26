"""공시목록의 수집 가능성만 판정한다. 재무 수치 검증 상태는 변경하지 않는다."""
import hashlib
import json
import re

from .general_collection import report_code

VERSION = "disclosure-availability-1.0"
REGULAR_CODES = {"11011", "11012", "11013", "11014"}


def classify_candidate(audit_row, raw_content=None, *, begin_date, public_cutoff):
    """보관된 list.json 응답을 감사 기록과 대조하고 문서 조사 우선순위를 반환한다."""
    code = audit_row.get("corp_code")
    if not re.fullmatch(r"\d{8}", str(code or "")):
        raise ValueError("invalid_corp_code")
    if audit_row.get("status") == "no_data":
        if raw_content is not None:
            raise ValueError("unexpected_raw_for_no_data")
        status, reports, reason = "no_disclosure_in_window", [], "dart_no_data"
        source_hash = None
    elif raw_content is None:
        status, reports, reason = "review_required", [], "raw_disclosure_response_missing"
        source_hash = None
    else:
        source_hash = hashlib.sha256(raw_content).hexdigest()
        if source_hash != audit_row.get("source_sha256"):
            raise ValueError("disclosure_source_hash_mismatch")
        data = json.loads(raw_content)
        try:
            pages = int(data.get("total_page") or 1)
        except (ValueError, TypeError):
            pages = 0
        if data.get("status") != "000" or pages != 1 or not isinstance(data.get("list"), list):
            status, reports, reason = "review_required", [], "response_or_pagination_unverified"
        else:
            reports = []
            for item in data["list"]:
                receipt = str(item.get("rcept_no", ""))
                if not re.fullmatch(r"\d{14}", receipt):
                    raise ValueError("invalid_receipt_number")
                if not begin_date.replace("-", "") <= receipt[:8] <= public_cutoff.replace("-", ""):
                    raise ValueError("receipt_outside_audit_window")
                name = item.get("report_nm", "")
                normalized_name = re.sub(r"^(?:\[[^\]]+\])+", "", name).strip()
                kind = "regular" if report_code(normalized_name) in REGULAR_CODES else (
                    "audit_report" if re.search(r"(?:연결)?감사보고서", name) else "other")
                reports.append({"receipt_number": receipt, "report_name": name, "kind": kind})
            reports.sort(key=lambda row: (row["receipt_number"], row["report_name"]))
            kinds = {item["kind"] for item in reports}
            if "regular" in kinds:
                status, reason = "regular_report_found", None
            elif "audit_report" in kinds:
                status, reason = "audit_report_document_candidate", "document_financial_statements_unverified"
            else:
                status, reason = "other_disclosures_only", "no_regular_or_audit_report_in_window"
    return {"corp_code": code, "corp_name": audit_row.get("corp_name"),
            "status": status, "reason_code": reason, "source_sha256": source_hash,
            "reports": reports, "regular_report_count": sum(r["kind"] == "regular" for r in reports),
            "audit_report_count": sum(r["kind"] == "audit_report" for r in reports),
            "financial_statement_status": "unverified",
            "numeric_validation_status": "not_started",
            "cohort_eligible": False}
