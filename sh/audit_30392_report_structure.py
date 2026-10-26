"""Inspect archived audit reports without promoting extracted values to facts."""
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.finance.dart_protocol import zip_members
from agent.finance.document_extractor import DocumentExtractor, tag, text
from agent.finance.extraction_service import candidates
from agent.research_store import safe_payload

SOURCE = ROOT / "doc/finance/KSIC30392_AUDIT_REPORT_INSPECTION_20261026.json"
RAW = ROOT / "data/research/cohort_official_30392_20261026/audit_reports_latest"
OUTPUT = ROOT / "doc/finance/KSIC30392_AUDIT_REPORT_STRUCTURE_PILOT_20261026.json"
OUTPUT_ALL = ROOT / "doc/finance/KSIC30392_AUDIT_REPORT_STRUCTURE_ALL_20261026.json"
RECEIPTS = ("20250408002111", "20260331001135")


def inspect(receipt, source_row):
    archive = (RAW / f"{receipt}.zip").read_bytes()
    if hashlib.sha256(archive).hexdigest() != source_row["raw_sha256"]:
        raise ValueError("archive_hash_mismatch")
    members = zip_members(archive, max_expanded_bytes=256 * 1024 * 1024, max_members=100)
    if len(members) != 1:
        raise ValueError("unexpected_member_count")
    member_name, data = members[0]
    if hashlib.sha256(data).hexdigest() != source_row["marker_members"][0]["member_sha256"]:
        raise ValueError("member_hash_mismatch")
    # The archived member is UTF-8 in both selected cases. A future encoding
    # conversion would require an explicit source-to-parsing-copy record.
    data.decode("utf-8", errors="strict")
    extractor = DocumentExtractor(data)
    parsed = extractor.extract()
    libraries = []
    for node in extractor.root.iter():
        if tag(node) != "library":
            continue
        first_group = next((child for child in node if tag(child) == "table-group"), None)
        if first_group is None:
            continue
        title_node = next((child for child in first_group if tag(child) == "title"), None)
        title = text(title_node) if title_node is not None else ""
        compact = "".join(title.split())
        statement = next((kind for word, kind in (("재무상태표", "BS"), ("손익계산서", "IS"),
                                                   ("현금흐름표", "CF")) if word in compact), None)
        if statement:
            libraries.append({"statement": statement,
                              "table_count": sum(tag(child) == "table" for child in node.iter())})
    tables = parsed["tables"]
    return {
        "corp_code": source_row["corp_code"], "receipt_number": receipt,
        "archive_sha256": source_row["raw_sha256"], "member_name": member_name,
        "member_sha256": hashlib.sha256(data).hexdigest(),
        "parser_version": parsed["parser_version"], "parser_kind": parsed["parser_kind"],
        "recovered": parsed["recovered"], "literal_repair_status": parsed["literal_repair"]["status"],
        "coverage": parsed["coverage"], "raw_table_count": parsed["raw_table_count"],
        "indexed_table_count": len(tables),
        "content_mismatch_table_count": parsed["content_mismatch_table_count"],
        "unmapped_raw_table_count": len(parsed["unmapped_raw_tables"]),
        "statement_libraries": libraries,
        "tables_with_statement_hint": sum(bool(t["statement_hint"]) for t in tables),
        "tables_with_period_declarations": sum(bool(t["period_declarations"]) for t in tables),
        "existing_account_candidate_count": sum(len(candidates(t)) for t in tables),
        "numeric_validation_status": "not_started", "cohort_eligible": False,
        "reason_codes": ["audit_library_layout_not_mapped", "period_and_scope_unconfirmed",
                         "api_and_independent_xbrl_match_not_performed"],
    }


def main(all_reports=False):
    source_bytes = SOURCE.read_bytes()
    rows = {row["receipt_number"]: row for row in json.loads(source_bytes)["records"]}
    receipts = tuple(sorted(rows)) if all_reports else RECEIPTS
    output = OUTPUT_ALL if all_reports else OUTPUT
    result = {
        "as_of": "2026-10-26", "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "external_requests": 0, "pilot_receipts": list(RECEIPTS) if not all_reports else None,
        "records": [inspect(receipt, rows[receipt]) for receipt in receipts],
        "limitation": "원문 구조 확인만 수행했다. 계정·기간·결산월·API 값·독립 XBRL 검증 전에는 수치를 분석 입력이나 코호트에 넣지 않는다.",
    }
    payload = (safe_payload(result) + "\n").encode()
    reused = output.exists()
    if reused:
        if output.read_bytes() != payload:
            raise ValueError("immutable_audit_artifact_conflict")
    else:
        output.write_bytes(payload)
    print(safe_payload({"status": "ok", "external_requests": 0, "inspected_count": len(receipts),
                        "revalidated": reused}))


if __name__ == "__main__":
    main(all_reports="--all" in sys.argv[1:])
