"""검증 결과를 바꾸지 않는 데이터 품질 예외 읽기 모델."""
import hashlib
from .quality_registry import get, is_mandatory

EXCEPTIONS_VERSION = "quality-exceptions-1.0"


def _id(batch_id, record_id, rule_id, reason):
    value = "|".join(str(v or "") for v in (batch_id, record_id, rule_id, reason))
    return hashlib.sha256(value.encode()).hexdigest()


def _period_scope(data):
    return data.get("period"), data.get("scope"), data.get("account")


def _exception(research_id, batch_id, record, rule_id, status, reason, *, calculation=False, check=None):
    meta = get(rule_id)
    period, scope, account = _period_scope(record.get("data", {}))
    mandatory = is_mandatory(rule_id)
    severity = "critical" if mandatory and status == "fail" else meta["severity"]
    if status == "not_checked" and severity == "critical": severity = "high"
    return {
        "exception_id": _id(batch_id, record.get("id"), rule_id, reason),
        "research_id": research_id, "batch_id": batch_id,
        "validation_id": None if calculation else record.get("id"),
        "calculation_id": record.get("id") if calculation else None,
        "rule_id": rule_id, "quality_dimension": meta["quality_dimension"], "severity": severity,
        "validation_status": status, "reason_code": reason, "affected_account": account,
        "scope": scope, "period": period, "evidence_refs": [x for x in (record.get("fact_id"),) if x],
        "suggested_action": meta["on_fail"] if status == "fail" else meta["on_not_checked"],
        "created_at": record.get("created_at"), "registry_version": meta["registry_version"],
        "check": check,
    }


def build(research_id, batch, report=None):
    """실패·미검증 결과를 예외로 투영한다. 중복 키는 안정적으로 제거한다."""
    items = []
    for record in batch.get("validations", []):
        rule = record.get("rule_id", "unknown")
        if record.get("status") in {"fail", "not_checked"}:
            items.append(_exception(research_id, batch["id"], record, rule, record["status"],
                                    record.get("data", {}).get("reason", "validation_" + record["status"])))
        for check_rule, check in record.get("data", {}).get("checks", {}).items():
            if check.get("status") in {"fail", "not_checked"}:
                items.append(_exception(research_id, batch["id"], record, check_rule, check["status"],
                                        check.get("reason", "check_" + check["status"]), check=check_rule))
    for record in batch.get("calculations", []):
        if record.get("status") in {"fail", "not_checked"}:
            rule = record.get("formula_version", "unknown").split(":")[-1]
            items.append(_exception(research_id, batch["id"], record, rule, record["status"],
                                    record.get("data", {}).get("reason", "calculation_" + record["status"]), calculation=True))
    if report:
        for index, row in enumerate(report.get("content", {}).get("context", {}).get("financial_rows", [])):
            if row.get("status") != "verified":
                record = {"id": "report-row-" + str(index), "data": {"account": row.get("account"), "scope": row.get("scope"), "period": row.get("period", {"end": row.get("end")})}}
                items.append(_exception(research_id, batch["id"], record, "V17", "not_checked", "report_financial_row_withheld"))
    deduped = {item["exception_id"]: item for item in items}
    return sorted(deduped.values(), key=lambda x: (x["severity"] != "critical", x["severity"] != "high", x["exception_id"]))
