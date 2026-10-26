"""보관된 13곳 공시목록에서 문서 수집 가능성을 재분류한다. 외부 호출 없음."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.finance.disclosure_availability import VERSION, classify_candidate
from agent.research_store import safe_payload

AUDIT = ROOT / "doc/finance/KSIC30392_OFFICIAL_DISCLOSURES_20261026.json"
RAW = ROOT / "data/research/cohort_official_30392_20261026/disclosures"
OUTPUT = ROOT / "doc/finance/KSIC30392_DISCLOSURE_AVAILABILITY_20261026.json"


def main():
    audit_bytes = AUDIT.read_bytes()
    audit = json.loads(audit_bytes)
    if audit.get("checked_count") != 13 or len(audit.get("records", [])) != 13:
        raise ValueError("expected_13_candidate_audit")
    rows = []
    for row in audit["records"]:
        path = RAW / (row["corp_code"] + ".json")
        raw = path.read_bytes() if path.exists() else None
        rows.append(classify_candidate(row, raw, begin_date="2023-01-01",
                                       public_cutoff=audit["as_of"]))
    rows.sort(key=lambda row: row["corp_code"])
    payload = {"version": VERSION, "public_cutoff": audit["as_of"],
               "begin_date": "2023-01-01", "request_count": 0,
               "input_audit_sha256": hashlib.sha256(audit_bytes).hexdigest(),
               "candidate_count": len(rows), "records": rows,
               "limitation": "공시목록은 문서 후보 선별만 한다. 감사보고서의 재무표, 원문/API 값, 기간, 결산월, XBRL은 미검증이다."}
    if OUTPUT.exists() and OUTPUT.read_text() != safe_payload(payload) + "\n":
        raise ValueError("existing_availability_snapshot_differs")
    OUTPUT.write_text(safe_payload(payload) + "\n", encoding="utf-8")
    print(safe_payload({"candidate_count": len(rows), "status_counts": {
        name: sum(row["status"] == name for row in rows)
        for name in sorted({row["status"] for row in rows})}}))


if __name__ == "__main__":
    main()
