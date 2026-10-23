"""검증된 재무 특성을 기업·기간 단위의 재현 가능한 ML 후보 데이터셋으로 고정한다."""
import hashlib
import json
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

VERSION = "peer-dataset-1.0"
FEATURE_POLICY_VERSION = "verified-financial-features-1.0"
FEATURES = {
    "operating_margin": "영업이익 / 매출액",
    "net_margin": "순이익 / 매출액",
    "liabilities_to_assets": "부채 / 자산",
    "operating_cashflow_to_revenue": "영업현금흐름 / 매출액",
    "inventory_to_assets": "재고자산 / 자산",
    "receivables_to_assets": "매출채권 및 기타유동채권 / 자산",
    "inventory_growth": "재고자산 전년 동기 증가율",
    "revenue_growth": "매출액 전년 동기 증가율",
    "operating_cashflow_growth": "영업현금흐름 전년 동기 증가율",
    "borrowing_to_assets": "검증된 차입금 구성 합계 / 자산",
}
IDENTITY = ("corp_code", "industry_code", "period_start", "period_end", "period_kind", "scope",
            "public_cutoff", "source_receipt", "source_hash", "validation_batch_id")


def _number(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _text(value):
    return format(value, "f") if isinstance(value, Decimal) else value


def feature_dictionary():
    return {name: {"feature": name, "definition": formula, "value_type": "Decimal", "missing_value": None,
                   "acceptance": "supported_and_traceable_only"} for name, formula in FEATURES.items()}


def collection_expansion_plan():
    """다기업 공개자료 수집 확장을 위한 실행 계획. 수집 성공은 검증 통과가 아니다."""
    return {"version": VERSION, "target": "동일·유사 제조업 산업군의 공개 정기공시", "phases": [
        {"name": "cohort_registry", "action": "산업코드·법인코드·비교 기준일·회계기준을 고정해 수집 대상을 기록", "status": "required"},
        {"name": "collection", "action": "general_collection의 공시목록·원문·XBRL·재무 API 수집을 기업별로 실행", "status": "existing_component_reusable"},
        {"name": "verification", "action": "원문/API 대조와 V 규칙을 통과한 수치만 데이터셋 후보로 승격", "status": "required_no_auto_approval"},
        {"name": "correction_and_snapshot", "action": "정정 관계와 public_cutoff을 재검증하고 불변 스냅샷 생성", "status": "required"}],
        "known_constraints": ["현재 일반 수집기는 단일 조사 실행 단위이며 다기업 코호트 스케줄러·산업코드 보관이 별도 구현 대상입니다.",
                              "수집 결과의 financial_verification은 not_performed일 수 있으며 verified를 의미하지 않습니다.",
                              "특정 기업·결산월·보고기간 가정은 코호트 레지스트리와 기간 정규화 작업에서 분리해 제거해야 합니다."],
        "prohibitions": ["OpenDART 응답만으로 verified 처리 금지", "확인되지 않은 수치의 자동 승인 플래그 금지"]}


def _canonical(record, feature_policy_version):
    missing = [field for field in IDENTITY if not record.get(field)]
    if missing:
        return None, {"reason": "required_identity_missing", "fields": missing, "record": record}
    if record.get("correction_status") != "current_verified":
        return None, {"reason": "correction_relation_not_verified", "record": record}
    source_public_at = str(record.get("source_public_at", record["source_receipt"][:8])).replace("-", "")
    if source_public_at > record["public_cutoff"].replace("-", ""):
        return None, {"reason": "source_not_public_at_cutoff", "record": record}
    values, missing_features, evidence = {}, {}, {}
    supplied = record.get("features", {})
    for name in FEATURES:
        item = supplied.get(name, {})
        value = _number(item.get("value"))
        if item.get("status") == "supported" and value is not None and item.get("evidence_refs"):
            values[name] = _text(value); evidence[name] = item["evidence_refs"]
        else:
            missing_features[name] = item.get("reason", "not_supported_or_missing_traceable_value")
    return {**{field: record[field] for field in IDENTITY}, "feature_policy_version": feature_policy_version,
            "account_definition_version": record.get("account_definition_version"), "peer_group": record.get("peer_group"),
            "features": values, "missing_features": missing_features, "feature_evidence": evidence,
            "correction_status": record["correction_status"]}, None


def build_snapshot(records, *, feature_policy_version=FEATURE_POLICY_VERSION):
    """입력 순서와 무관한 스냅샷. 동일 기업·기간·범위가 모호하면 모두 제외한다."""
    accepted, excluded, grouped = [], [], defaultdict(list)
    for record in records:
        row, issue = _canonical(record, feature_policy_version)
        if issue: excluded.append(issue)
        else: grouped[(row["corp_code"], row["period_start"], row["period_end"], row["period_kind"], row["scope"])].append(row)
    for key, candidates in sorted(grouped.items()):
        hashes = {item["source_hash"] for item in candidates}
        if len(hashes) != 1:
            excluded.append({"reason": "ambiguous_duplicate_company_period_scope", "identity": key,
                             "source_hashes": sorted(hashes)})
        else:
            # 같은 검증 스냅샷의 중복 입력은 하나만 보관한다.
            accepted.append(sorted(candidates, key=lambda item: (item["source_receipt"], item["validation_batch_id"]))[-1])
    accepted.sort(key=lambda item: (item["corp_code"], item["period_end"], item["scope"]))
    total = len(accepted)
    missingness = {}
    for name in FEATURES:
        available = sum(name in item["features"] for item in accepted)
        reasons = Counter(item["missing_features"].get(name) for item in accepted if name not in item["features"])
        missingness[name] = {"available_count": available, "missing_count": total - available,
                             "availability_rate": _text(Decimal(available) / total) if total else None,
                             "missing_reasons": dict(sorted(reasons.items()))}
    stable = json.dumps(accepted, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {"schema_version": VERSION, "feature_policy_version": feature_policy_version,
            "snapshot_id": hashlib.sha256(stable.encode()).hexdigest(), "rows": accepted,
            "row_count": total, "feature_dictionary": feature_dictionary(), "feature_missingness": missingness,
            "excluded_records": excluded, "collection_expansion_plan": collection_expansion_plan()}
