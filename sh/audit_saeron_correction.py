"""Fetch and preserve the original Saeron filing for correction relationship review."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent.finance.dart_protocol import DartHTTP, zip_members
from agent.research_store import ResearchStore, safe_payload

ORIGINAL = "20260813000914"
CORRECTED = "20260814002314"
LIST_SOURCE = "a8b61545e4a945ed806db92d50e1390d"
CORRECTED_SOURCE = "8b1fe2670fd64558826e1bca1d1ff800"
OUT = ROOT / "doc/finance/SAERON_CORRECTION_RELATION_20261027.json"
ARCHIVE = ROOT / "data/research/audits/saeron_20260813_original.zip"
ACTIVE = ROOT / "data/research/audits/disclosure_relations" / (CORRECTED + ".json")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def text_lines(raw):
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("unsafe_document")
    root = etree.fromstring(raw, etree.XMLParser(resolve_entities=False, no_network=True, recover=True))
    return [" ".join(line.split()) for line in root.itertext() if line and line.strip()]


async def main():
    store = ResearchStore(ROOT / "session.db", ROOT / "data/research")
    listing = json.loads(store.source_content(LIST_SOURCE))
    pair = [row for row in listing["list"] if row.get("rcept_no") in {ORIGINAL, CORRECTED}]
    if len(pair) != 2 or {row.get("corp_code") for row in pair} != {"00305668"}:
        raise ValueError("official_list_pair_mismatch")
    matching = [row for row in listing["list"] if row.get("corp_code") == "00305668"
                and row.get("rcept_dt") == "20260813" and "반기보고서 (2026.06)" in row.get("report_nm", "")]
    if len(matching) != 1 or matching[0]["rcept_no"] != ORIGINAL:
        raise ValueError("original_not_unique_in_official_list")
    load_dotenv(ROOT / ".env")
    key = os.environ.get("OPENDART_API_KEY")
    if not key:
        raise ValueError("opendart_key_missing")
    if ARCHIVE.exists():
        raw = ARCHIVE.read_bytes()
    else:
        async def reserve():
            pass
        result = await DartHTTP(key, max_response_bytes=100 * 1024 * 1024).request(
            "document", {"rcept_no": ORIGINAL}, before_request=reserve)
        raw = result.content
        ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
        ARCHIVE.write_bytes(raw)
    members = zip_members(raw, max_expanded_bytes=256 * 1024 * 1024, max_members=100)
    originals = [(name, content) for name, content in members if name == ORIGINAL + ".xml"]
    if len(originals) != 1:
        raise ValueError("original_member_missing_or_ambiguous")
    original_lines = text_lines(originals[0][1])
    corrected_raw = store.source_content(CORRECTED_SOURCE)
    corrected_lines = text_lines(corrected_raw)
    corrected_intro = " ".join(corrected_lines[:30])
    original_intro = " ".join(original_lines[:30])
    checks = {
        "corrected_names_original_submission_date": "정정대상 공시서류의 최초제출일 : 2026년 8월 13일" in corrected_intro,
        "original_is_same_company": "새론오토모티브" in original_intro,
        "original_is_2026_half_year": "2026년 06월 30일" in original_intro or "2026년 6월 30일" in original_intro,
        "original_receipt_unique_in_official_list": True,
    }
    outcome = {
        "original_receipt": ORIGINAL, "corrected_receipt": CORRECTED,
        "company_code": "00305668", "public_cutoff": "2026-10-27",
        "official_list_source_id": LIST_SOURCE,
        "official_list_hash": store.get_source(LIST_SOURCE)["content_hash"],
        "corrected_source_id": CORRECTED_SOURCE,
        "corrected_source_hash": store.get_source(CORRECTED_SOURCE)["content_hash"],
        "original_archive_path": str(ARCHIVE.relative_to(ROOT)),
        "original_archive_hash": digest(raw),
        "original_member_name": originals[0][0],
        "original_member_hash": digest(originals[0][1]),
        "official_list_pair": sorted(pair, key=lambda x: x["rcept_no"]),
        "checks": checks,
        "status": "relation_supported" if all(checks.values()) else "relation_unconfirmed",
        "financial_verification": "not_performed",
        "limitation": "원본·정정 관계 조사만 수행. 기존 검증 배치와 보고서 게시 상태를 변경하지 않음",
    }
    encoded = (safe_payload(outcome) + "\n").encode()
    if OUT.exists() and OUT.read_bytes() != encoded:
        raise ValueError("immutable_result_conflict")
    if not OUT.exists():
        OUT.write_bytes(encoded)
    ACTIVE.parent.mkdir(parents=True, exist_ok=True)
    if ACTIVE.exists() and ACTIVE.read_bytes() != encoded:
        raise ValueError("active_relation_conflict")
    if not ACTIVE.exists():
        ACTIVE.write_bytes(encoded)
    print(safe_payload({"status": outcome["status"], "checks": checks,
                        "archive_hash": outcome["original_archive_hash"],
                        "member_hash": outcome["original_member_hash"]}))


if __name__ == "__main__":
    asyncio.run(main())
