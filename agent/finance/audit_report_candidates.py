"""Structural statement and period candidates from DART audit reports.

These are navigation hints only. They never create financial facts or validation.
"""
import re
import hashlib

from .document_extractor import DocumentExtractor, tag, text

VERSION = "audit-report-candidates-1.0"
STATEMENTS = (("재무상태표", "BS"), ("포괄손익계산서", "CIS"),
              ("손익계산서", "IS"), ("현금흐름표", "CF"))
DATE = re.compile(r"(20\d{2})\s*(?:년|[./-])\s*(\d{1,2})\s*(?:월|[./-])\s*(\d{1,2})\s*일?")


def _statement(value):
    compact = re.sub(r"\s+", "", value)
    return next((kind for word, kind in STATEMENTS if word in compact), None)


def _dates(value):
    from datetime import date

    result = []
    for parts in DATE.findall(value):
        try:
            result.append(date(*(int(part) for part in parts)).isoformat())
        except ValueError:
            continue
    return list(dict.fromkeys(result))


def _located(extractor, node):
    location = extractor.locate(node)
    return location if location.get("raw_span_verified") and location.get("parser_text_matches_raw") else None


def _financial_attachment(node):
    for ancestor in node.iterancestors():
        if tag(ancestor) != "section-1":
            continue
        title = next((child for child in ancestor if tag(child) == "title"), None)
        compact = re.sub(r"[\s()]", "", text(title)) if title is not None else ""
        if "첨부" in compact and "재무제표" in compact:
            return True
    return False


def statement_period_candidates(content):
    extractor = DocumentExtractor(content)
    candidates = []
    seen = set()
    for node in extractor.root.iter():
        if not _financial_attachment(node):
            continue
        if tag(node) == "table-group":
            title = next((child for child in node if tag(child) == "title"), None)
            statement = _statement(text(title)) if title is not None else None
            tables = [child for child in node if tag(child) == "table"]
            if not statement or len(tables) < 2:
                continue
            title_node, data_node = tables[0], tables[1]
            layout = "library_table_group" if any(tag(a) == "library" for a in node.iterancestors()) else "table_group"
        elif tag(node) == "table":
            # Some audit documents put the statement heading in a standalone
            # table and the data in its immediately following sibling.
            if any(tag(a) == "table-group" for a in node.iterancestors()):
                continue
            statement = _statement(text(node))
            next_node = node.getnext()
            if not statement or next_node is None or tag(next_node) != "table":
                continue
            title_node, data_node = node, next_node
            layout = "adjacent_tables"
        else:
            continue
        # Require a recognizable account-column heading and distinct raw spans.
        if "과목" not in re.sub(r"\s+", "", text(data_node)[:120]):
            continue
        title_location = _located(extractor, title_node)
        data_location = _located(extractor, data_node)
        if not title_location or not data_location:
            continue
        key = (statement, data_location.get("byte_start"), data_location.get("byte_end"))
        if key in seen:
            continue
        seen.add(key)
        heading = text(title) if tag(node) == "table-group" else text(title_node)
        scope = "CFS" if "연결" in re.sub(r"\s+", "", heading) else "unknown"
        candidates.append({
            "statement": statement, "layout": layout, "period_dates_candidate": _dates(text(title_node)),
            "title_location": title_location, "data_location": data_location,
            "scope": scope, "status": "candidate_only",
            "limitations": ["period_column_mapping_unconfirmed"] +
                           (["scope_unconfirmed"] if scope == "unknown" else []) +
                           ["account_and_amount_unverified", "api_and_xbrl_match_not_performed"],
        })
    return {"version": VERSION, "source_hash": hashlib.sha256(content).hexdigest(),
            "candidates": candidates, "financial_verification": "not_performed"}
