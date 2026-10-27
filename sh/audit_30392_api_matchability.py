"""Check whether archived audit receipts have contract-compatible OpenDART comparators."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.research_store import safe_payload

DISCLOSURES = ROOT / "doc/finance/KSIC30392_DISCLOSURE_AVAILABILITY_20261026.json"
CANDIDATES = ROOT / "doc/finance/KSIC30392_AUDIT_REPORT_CANDIDATES_20261026.json"
OUTPUT = ROOT / "doc/finance/KSIC30392_API_MATCHABILITY_20261026.json"


def evaluate(disclosures, candidates):
    by_corp = {row["corp_code"]: row for row in disclosures["records"]}
    rows = []
    for candidate in candidates["records"]:
        company = by_corp[candidate["corp_code"]]
        regular = [row for row in company["reports"] if row["kind"] == "regular"]
        receipt = candidate["receipt_number"]
        audit = [row for row in company["reports"]
                 if row["receipt_number"] == receipt and row["kind"] == "audit_report"]
        if len(audit) != 1:
            raise ValueError("candidate_receipt_not_audit_report")
        rows.append({
            "corp_code": company["corp_code"], "audit_receipt": receipt,
            "statement_candidate_count": len(candidate["candidates"]),
            "regular_report_count_in_window": len(regular),
            "financials_same_receipt": "not_addressable_by_documented_contract",
            "xbrl_same_receipt": "not_addressable_by_documented_contract",
            "reason_code": "audit_report_has_no_supported_reprt_code",
            "numeric_validation_status": "not_started", "cohort_eligible": False,
        })
    return rows


def main():
    disclosure_bytes = DISCLOSURES.read_bytes()
    candidate_bytes = CANDIDATES.read_bytes()
    disclosures = json.loads(disclosure_bytes)
    candidates = json.loads(candidate_bytes)
    if disclosures["public_cutoff"] != candidates["as_of"]:
        raise ValueError("public_cutoff_mismatch")
    rows = evaluate(disclosures, candidates)
    output = {
        "as_of": disclosures["public_cutoff"], "external_requests": 0,
        "disclosure_source_sha256": hashlib.sha256(disclosure_bytes).hexdigest(),
        "candidate_source_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
        "official_contracts": {
            "financials": "https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020",
            "xbrl": "https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019019",
            "supported_reprt_codes": ["11011", "11012", "11013", "11014"],
        },
        "records": rows,
        "limitation": "공식 요청 계약과 보관 공시목록의 대응 가능성을 검사했다. 라이브 API의 개별 no_data 응답을 시험한 것은 아니며 감사보고서 재무제표의 부재를 뜻하지 않는다.",
    }
    payload = (safe_payload(output) + "\n").encode()
    reused = OUTPUT.exists()
    if reused:
        if OUTPUT.read_bytes() != payload:
            raise ValueError("immutable_audit_artifact_conflict")
    else:
        OUTPUT.write_bytes(payload)
    print(safe_payload({"status": "ok", "company_count": len(rows),
                        "compatible_regular_reports": sum(row["regular_report_count_in_window"] for row in rows),
                        "external_requests": 0, "revalidated": reused}))


if __name__ == "__main__":
    main()
