"""Freeze structural candidates from the nine already archived audit reports."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.finance.audit_report_candidates import statement_period_candidates
from agent.finance.dart_protocol import zip_members
from agent.research_store import safe_payload

SOURCE = ROOT / "doc/finance/KSIC30392_AUDIT_REPORT_INSPECTION_20261026.json"
RAW = ROOT / "data/research/cohort_official_30392_20261026/audit_reports_latest"
OUTPUT = ROOT / "doc/finance/KSIC30392_AUDIT_REPORT_CANDIDATES_20261026.json"


def main():
    source_bytes = SOURCE.read_bytes()
    rows = []
    for item in sorted(json.loads(source_bytes)["records"], key=lambda row: row["receipt_number"]):
        receipt = item["receipt_number"]
        archive = (RAW / f"{receipt}.zip").read_bytes()
        if hashlib.sha256(archive).hexdigest() != item["raw_sha256"]:
            raise ValueError("archive_hash_mismatch")
        members = zip_members(archive, max_expanded_bytes=256 * 1024 * 1024, max_members=100)
        if len(members) != 1:
            raise ValueError("unexpected_member_count")
        name, content = members[0]
        expected = item["marker_members"][0]
        if name != expected["member_name"] or hashlib.sha256(content).hexdigest() != expected["member_sha256"]:
            raise ValueError("member_hash_mismatch")
        candidate_result = statement_period_candidates(content)
        rows.append({"corp_code": item["corp_code"], "receipt_number": receipt,
                     "source_hash": candidate_result["source_hash"],
                     "candidate_version": candidate_result["version"],
                     "candidates": candidate_result["candidates"],
                     "numeric_validation_status": "not_started", "cohort_eligible": False})
    output = {"as_of": "2026-10-26", "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
              "external_requests": 0, "records": rows,
              "limitation": "표제와 날짜 문자열은 구조 후보이다. 기간 열·계정·금액·API·XBRL 검증 전에는 재무 사실이나 분석 입력이 아니다."}
    payload = (safe_payload(output) + "\n").encode()
    reused = OUTPUT.exists()
    if reused:
        if OUTPUT.read_bytes() != payload:
            raise ValueError("immutable_audit_artifact_conflict")
    else:
        OUTPUT.write_bytes(payload)
    print(safe_payload({"status": "ok", "external_requests": 0,
                        "company_count": len(rows),
                        "statement_candidate_count": sum(len(row["candidates"]) for row in rows),
                        "revalidated": reused}))


if __name__ == "__main__":
    main()
