"""기존 검증 배치에서 품질 분포와 분석 입력 적격성을 재현한다."""
from collections import Counter, defaultdict

from .quality_exceptions import build as build_exceptions
from .quality_metrics import calculate
from .quality_registry import get
from .numeric_validation import NumericValidationService

VERSION = "quality-analytics-1.0"
STATES = ("pass", "fail", "not_checked", "not_applicable")


def _distribution(items, *, key_name, key_value, extra=None):
    counts = Counter(item.get("status") for item in items)
    denominator = counts["pass"] + counts["fail"]
    result = {key_name: key_value, **(extra or {}), **{state: counts[state] for state in STATES}}
    result.update(
        numerator=counts["pass"],
        denominator=denominator,
        rate=counts["pass"] / denominator if denominator else None,
        measurement_status="measured" if denominator else "unmeasured",
        observed_count=sum(counts.values()),
    )
    return result


def _rule_observations(batch):
    """한 validation의 한 rule 결과만 한 관측치로 보관한다.

    수치 한 개의 V05와 V07은 서로 다른 규칙 분포에는 각각 나타나지만, 이를
    합산해 전체 오류·수치 건수로 사용하지 않는다.
    """
    result = []
    for record in batch.get("validations", []):
        data = record.get("data", {})
        if record.get("rule_id") != "numeric_fact":
            result.append({"rule_id": record.get("rule_id", "unknown"), "status": record.get("status"),
                           "validation_id": record.get("id"), "data": data, "source": "validation"})
        for rule_id, check in data.get("checks", {}).items():
            result.append({"rule_id": rule_id, "status": check.get("status"), "validation_id": record.get("id"),
                           "data": data, "reason": check.get("reason"), "source": "numeric_check"})
    for record in batch.get("calculations", []):
        result.append({"rule_id": record.get("formula_version", "unknown").split(":")[-1], "status": record.get("status"),
                       "validation_id": record.get("id"), "data": record.get("data", {}), "source": "calculation"})
    return [item for item in result if item["status"] in STATES]


def _numeric_state(record):
    checks = record.get("data", {}).get("checks", {})
    statuses = [value.get("status") for value in checks.values()]
    if record.get("status") == "fail" or "fail" in statuses: return "fail"
    if record.get("status") == "not_checked" or "not_checked" in statuses: return "not_checked"
    if record.get("status") == "not_applicable": return "not_applicable"
    return "pass"


def _numeric_observations(batch):
    return [{"status": _numeric_state(item), "validation_id": item.get("id"), "data": item.get("data", {})}
            for item in batch.get("validations", []) if item.get("rule_id") == "numeric_fact"]


def _group_numeric(items, fields):
    groups = defaultdict(list)
    for item in items:
        data = item["data"]
        key = tuple(data.get(field) if field != "period" else (data.get("period", {}).get("as_of") or data.get("period", {}).get("end")) for field in fields)
        groups[key].append(item)
    output = []
    for key, values in sorted(groups.items(), key=lambda entry: tuple(str(v) for v in entry[0])):
        extra = dict(zip(fields, key))
        output.append(_distribution(values, key_name="group", key_value="|".join(str(value) for value in key), extra=extra))
    return output


def _issue_distribution(observations, *, title, predicate):
    matched = [item for item in observations if predicate(item)]
    groups = defaultdict(list)
    for item in matched:
        groups[item.get("reason") or item["rule_id"]].append(item)
    return [{"reason": reason, **_distribution(values, key_name="rule_group", key_value=title)}
            for reason, values in sorted(groups.items())]


def _trend(batches, reports_by_batch):
    output, previous = [], None
    for batch in batches:
        metrics = calculate(batch, reports_by_batch.get(batch.get("id")))
        current = metrics["numeric_fact"]
        row = {"batch_id": batch.get("id"), "created_at": batch.get("created_at"),
               "policy_version": batch.get("policy_version"), "numeric_fact": current,
               "amount_comparison": metrics["amount_comparison"], "source_location": metrics["source_location"],
               "comparison_to_previous": "unmeasured"}
        if previous and previous["numeric_fact"]["rate"] is not None and current["rate"] is not None:
            row["comparison_to_previous"] = "measured"
            row["numeric_fact_rate_delta"] = current["rate"] - previous["numeric_fact"]["rate"]
        if previous and previous["amount_comparison"]["rate"] is not None and row["amount_comparison"]["rate"] is not None:
            row["amount_comparison_rate_delta"] = row["amount_comparison"]["rate"] - previous["amount_comparison"]["rate"]
        if previous and previous["source_location"]["rate"] is not None and row["source_location"]["rate"] is not None:
            row["source_location_rate_delta"] = row["source_location"]["rate"] - previous["source_location"]["rate"]
        output.append(row); previous = row
    return output


def analyze(research_id, batches, *, reports_by_batch=None):
    """품질 분석은 검증 결과를 바꾸지 않는 결정론적 읽기 모델이다."""
    reports_by_batch = reports_by_batch or {}
    batches = sorted(batches, key=lambda item: (item.get("created_at", ""), item.get("id", "")))
    latest = batches[-1] if batches else None
    if not latest:
        return {"schema_version": VERSION, "research_id": research_id, "status": "unmeasured", "reason": "no_validation_batches",
                "rule_distribution": [], "account_gaps": [], "period_coverage": [], "scope_failure_types": [],
                "source_and_mapping_issues": [], "correction_history": [], "batch_trend": [], "exception_reasons": [],
                "model_dataset_eligibility": {"status": "not_eligible", "reason": "no_validation_batches"}}
    observations = _rule_observations(latest)
    by_rule = defaultdict(list)
    for item in observations: by_rule[item["rule_id"]].append(item)
    rules = [_distribution(values, key_name="rule_id", key_value=rule_id,
                           extra={"quality_dimension": get(rule_id)["quality_dimension"], "source": values[0]["source"]})
             for rule_id, values in sorted(by_rule.items())]
    numeric = _numeric_observations(latest)
    exceptions = build_exceptions(research_id, latest, reports_by_batch.get(latest.get("id")))
    exception_groups = defaultdict(list)
    for item in exceptions: exception_groups[item["reason_code"]].append(item)
    exception_reasons = [{"reason_code": reason, "review_required_count": len(values), "denominator": len(exceptions),
                          "numerator": len(values), "measurement_status": "measured" if exceptions else "unmeasured"}
                         for reason, values in sorted(exception_groups.items())]
    metric = calculate(latest, reports_by_batch.get(latest.get("id")))
    return {
        "schema_version": VERSION, "research_id": research_id, "status": "measured", "validation_batch_id": latest.get("id"),
        "quality_metrics": metric,
        "rule_distribution": rules,
        "account_gaps": _group_numeric(numeric, ("account",)),
        "period_coverage": _group_numeric(numeric, ("period",)),
        "scope_failure_types": _group_numeric([item for item in observations if item["status"] in {"fail", "not_checked"}], ("scope", "rule_id")),
        "source_and_mapping_issues": _issue_distribution(observations, title="source_and_mapping", predicate=lambda item: item["rule_id"] in {"V06", "V07", "V07_source_coverage", "V07_fact_coverage"}),
        "correction_history": {"v13": _distribution([item for item in observations if item["rule_id"] == "V13"], key_name="rule_id", key_value="V13"),
                               "disclosure_history_audit": latest.get("summary", {}).get("disclosure_history_audit", {"status": "not_checked"})},
        "batch_trend": _trend(batches, reports_by_batch),
        "exception_reasons": exception_reasons,
        "model_dataset_eligibility": {"status": "not_eligible", "quality_gate": metric["numeric_fact"]["status"],
            "reason": "단일 조사 품질 배치만으로 다기업 모델 표본·산업 동질성·기간 충분성을 입증할 수 없습니다.",
            "verified_numeric_fact_count": metric["numeric_fact"]["passed_count"],
            "withheld_or_unverified_numeric_fact_count": metric["numeric_fact"]["failed_count"] + metric["numeric_fact"]["not_checked_count"]},
        "limitations": ["규칙별 분포의 분모는 해당 규칙이 실제 실행된 고유 검증 결과 수다. 서로 다른 규칙의 관측치를 합산해 수치 오류 건수로 해석하지 않는다.",
                        "미검증은 오류나 성공으로 변환하지 않으며, 품질 분석은 금융 분석의 검증 상태를 변경하지 않는다."]}


class QualityAnalyticsService:
    def __init__(self, store): self.store = store

    def research(self, research_id):
        research = self.store.get_research(research_id)
        reports_by_batch = {}
        for report_meta in research["reports"]:
            report = self.store.get_report(report_meta["id"])
            if report.get("status") == "published": reports_by_batch[report["publication"]["validation_batch_id"]] = report
        batches = []
        with self.store.connection() as conn:
            rows = conn.execute("SELECT b.id FROM research_validation_batches b JOIN research_runs r ON r.id=b.run_id JOIN research_bases a ON a.id=r.basis_id WHERE a.research_id=? ORDER BY b.created_at,b.id", (research_id,)).fetchall()
        service = NumericValidationService(self.store)
        for row in rows: batches.append(service.batch(row["id"]))
        return analyze(research_id, batches, reports_by_batch=reports_by_batch)
