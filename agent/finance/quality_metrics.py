"""검증 배치와 게시 보고서에서 결정론적으로 품질지표를 계산한다."""
from collections import Counter
from .quality_registry import REGISTRY_VERSION

METRICS_VERSION = "quality-metrics-1.0"


def _measure(passed, failed, not_checked=0, not_applicable=0):
    denominator = passed + failed
    return {
        "passed_count": passed, "failed_count": failed,
        "not_checked_count": not_checked, "not_applicable_count": not_applicable,
        "denominator": denominator, "numerator": passed,
        "rate": (passed / denominator) if denominator else None,
        "status": "measured" if denominator else "unmeasured",
    }


def _statuses(items):
    counts = Counter(item.get("status") for item in items)
    return _measure(counts["pass"], counts["fail"], counts["not_checked"], counts["not_applicable"])


def _checks(batch, rule_id):
    result = []
    for validation in batch.get("validations", []):
        for key, value in validation.get("data", {}).get("checks", {}).items():
            if key == rule_id:
                result.append({"status": value.get("status"), "validation_id": validation.get("id")})
        if validation.get("rule_id") == rule_id:
            result.append({"status": validation.get("status"), "validation_id": validation.get("id")})
    return result


def _numeric_facts(batch):
    return [v for v in batch.get("validations", []) if v.get("rule_id") == "numeric_fact"]


def _report_coverage(report):
    if report is None:
        return {"verified_count": 0, "withheld_count": 0, "total_count": 0,
                "numerator": 0, "denominator": 0, "rate": None, "status": "unmeasured"}
    rows = report.get("content", {}).get("context", {}).get("financial_rows", [])
    verified = sum(row.get("status") == "verified" for row in rows)
    withheld = sum(row.get("status") != "verified" for row in rows)
    return {"verified_count": verified, "withheld_count": withheld, "total_count": len(rows),
            "numerator": verified, "denominator": len(rows),
            "rate": verified / len(rows) if rows else None, "status": "measured" if rows else "unmeasured"}


def calculate(batch, report=None):
    """동일 배치·동일 보고서 입력이면 동일한 순수 JSON 결과를 반환한다."""
    numeric = _numeric_facts(batch)
    amount_checks = _checks(batch, "V05")
    location_checks = _checks(batch, "V07")
    exact = rounding = 0
    for item in numeric:
        comparison = item.get("data", {}).get("amount_comparison", {})
        if comparison.get("match") == "exact" and item.get("data", {}).get("checks", {}).get("V05", {}).get("status") == "pass":
            exact += 1
        if comparison.get("match") == "rounding" and item.get("data", {}).get("checks", {}).get("V05", {}).get("status") == "pass":
            rounding += 1
    temporal = {key: _statuses(_checks(batch, key)) for key in ("V02", "V13")}
    audit = batch.get("summary", {}).get("disclosure_history_audit", {})
    coverage = batch.get("summary", {}).get("account_coverage", [])
    return {
        "metrics_version": METRICS_VERSION, "registry_version": REGISTRY_VERSION,
        "validation_batch_id": batch.get("id"), "validation_policy_version": batch.get("policy_version"),
        "numeric_fact": {**_statuses(numeric), "total_count": len(numeric)},
        "amount_comparison": {**_statuses(amount_checks), "exact_match_count": exact, "rounding_match_count": rounding},
        "source_location": _statuses(location_checks),
        "report_coverage": _report_coverage(report),
        "temporal_checks": {**temporal,
            "disclosure_history_status": audit.get("status", "not_checked"),
            "latest_data_checked": False,
            "limitation": "공개 기준일 적합성과 현재 최신 공시 여부는 별도 개념입니다."},
        "coverage": {"account_coverage": coverage, "overall_disclosure_coverage": None,
                     "limitation": "수집하지 않은 공시 전체의 완전성을 계산할 근거가 없습니다."},
        "limitations": ["품질지표는 정해진 자동 검증 범위의 부분 지표이며, 기업 재무정보 전체의 정확성 또는 신용심사 결론을 보증하지 않습니다."]
    }
