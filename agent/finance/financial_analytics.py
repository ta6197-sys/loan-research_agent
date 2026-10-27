"""게시 당시 검증된 재무 행만 사용하는 결정론적 분석 읽기 모델."""
from decimal import Decimal, InvalidOperation

from .change_analysis import BORROWING_ACCOUNTS
from .numeric_validation import NumericValidationService
from .validation_acceptance import MANDATORY_CHECKS

VERSION = "financial-analytics-1.0"
FLOW = {"revenue", "operating_profit", "net_profit", "operating_cashflow"}


def _decimal(value):
    try:
        value = Decimal(str(value))
        return value if value.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _text(value):
    return format(value, "f")


def _refs(*rows):
    return [{"account": row["account"], "scope": row["scope"], "period": row.get("period"),
             "fact_ids": sorted(row.get("fact_ids", [])), "validation_ids": sorted(row.get("validation_ids", [])),
             "source_ids": sorted(row.get("source_ids", []))} for row in rows]


def _item(name, category, scope, period, formula, *, status, value=None, unit=None, inputs=(), reason=None):
    result = {"id": f"{category}:{name}:{scope}:{period.get('end') or period.get('as_of')}",
              "name": name, "category": category, "scope": scope, "period": period,
              "formula": formula, "status": status, "inputs": _refs(*inputs)}
    if status == "supported":
        result.update(value=_text(value), unit=unit)
    else:
        result["reason"] = reason
    return result


def _row_is_verified(row, validations, raw_ok):
    if row.get("status") != "verified" or _decimal(row.get("value")) is None:
        return False
    ids = row.get("validation_ids", [])
    if not ids or not raw_ok(row):
        return False
    for identifier in ids:
        item = validations.get(identifier, {})
        checks = item.get("data", {}).get("checks", {})
        if item.get("rule_id") != "numeric_fact" or item.get("status") != "pass":
            return False
        if any(checks.get(rule, {}).get("status") != "pass" for rule in MANDATORY_CHECKS):
            return False
    return True


def _index(rows, validations, raw_ok):
    result = {}
    held = set()
    for row in rows:
        key = (row.get("scope"), row.get("account"), row.get("end"))
        if _row_is_verified(row, validations, raw_ok):
            result[key] = row
        else:
            held.add(key)
    return result, held


def _same_flow(left, right):
    a, b = left.get("period", {}), right.get("period", {})
    return (a.get("kind") == b.get("kind") == "duration" and a.get("start") == b.get("start") and
            a.get("end") == b.get("end"))


def _metric_row(index, held, scope, account, end):
    key = (scope, account, end)
    return index.get(key), ("withheld" if key in held else "unmeasured")


def _v11(batch, current, previous):
    wanted = {tuple(sorted(current.get("validation_ids", []))), tuple(sorted(previous.get("validation_ids", [])))}
    for calculation in batch.get("calculations", []):
        if calculation.get("status") != "pass" or ":V11_change_" not in calculation.get("formula_version", ""):
            continue
        inputs = calculation.get("data", {}).get("inputs", {})
        current_ids = tuple(sorted([inputs.get("current", {}).get("validation_id")]))
        previous_ids = tuple(sorted([inputs.get("previous", {}).get("validation_id")]))
        if {current_ids, previous_ids} != wanted:
            continue
        result = _decimal(calculation.get("data", {}).get("result"))
        if result is None:
            continue
        expected = _decimal(current.get("value")) - _decimal(previous.get("value"))
        if result == expected:
            return calculation
    return None


def _change(name, category, index, held, batch, scope, account, before_end, after_end, *, percent=False):
    after, after_state = _metric_row(index, held, scope, account, after_end)
    before, before_state = _metric_row(index, held, scope, account, before_end)
    period = {"before_end": before_end, "end": after_end, "comparison": "validated_v11"}
    if not after or not before:
        return _item(name, category, scope, period, "V11 verified change", status="withheld" if "withheld" in (after_state, before_state) else "unmeasured",
                     reason="verified_input_withheld" if "withheld" in (after_state, before_state) else "required_period_or_account_missing")
    v11 = _v11(batch, after, before)
    if not v11:
        return _item(name, category, scope, period, "V11 verified change", status="withheld", inputs=(before, after), reason="v11_period_account_mapping_not_supported")
    delta = _decimal(v11["data"]["result"])
    if percent:
        base = _decimal(before["value"])
        if base is None or base <= 0:
            return _item(name, category, scope, period, "(current - previous) / previous * 100", status="withheld", inputs=(before, after), reason="zero_or_nonpositive_comparison_base")
        result = _item(name, category, scope, period, "(current - previous) / previous * 100", status="supported", value=delta / base * Decimal(100), unit="percent", inputs=(before, after))
    else:
        result = _item(name, category, scope, period, "current - previous", status="supported", value=delta, unit="KRW", inputs=(before, after))
    result["comparison_calculation_ids"] = [v11["id"]]
    return result


def _ratio(name, category, scope, period, numerator, denominator, formula, *, denominator_positive=True):
    if not numerator or not denominator:
        return _item(name, category, scope, period, formula, status="unmeasured", reason="required_verified_input_missing")
    base = _decimal(denominator["value"])
    if base is None or base == 0 or (denominator_positive and base < 0):
        return _item(name, category, scope, period, formula, status="withheld", inputs=(numerator, denominator), reason="zero_or_inappropriate_denominator")
    return _item(name, category, scope, period, formula, status="supported", value=_decimal(numerator["value"]) / base * Decimal(100), unit="percent", inputs=(numerator, denominator))


def _period_pairs(index, scope):
    from .change_analysis import _period_pairs as comparable_pairs
    return [(before,after) for before,after,_ in comparable_pairs(index.values(),scope)]


def analyze(report, batch, *, raw_ok=lambda row: True):
    """순수 읽기 모델. 입력·검증 배치·출력이 같으면 같은 결과를 반환한다."""
    rows = report.get("content", {}).get("context", {}).get("financial_rows", [])
    validations = {item.get("id"): item for item in batch.get("validations", [])}
    index, held = _index(rows, validations, raw_ok)
    results = []
    for scope in ("OFS", "CFS"):
        for end in sorted({key[2] for key in index if key[0] == scope}):
            revenue, _ = _metric_row(index, held, scope, "revenue", end)
            operating, _ = _metric_row(index, held, scope, "operating_profit", end)
            net, _ = _metric_row(index, held, scope, "net_profit", end)
            cashflow, _ = _metric_row(index, held, scope, "operating_cashflow", end)
            assets, _ = _metric_row(index, held, scope, "assets", end)
            liabilities, _ = _metric_row(index, held, scope, "liabilities", end)
            equity, _ = _metric_row(index, held, scope, "equity", end)
            period = revenue.get("period", {"end": end}) if revenue else {"end": end}
            if revenue and operating and _same_flow(revenue, operating):
                results.append(_ratio("operating_margin", "profitability", scope, period, operating, revenue, "operating_profit / revenue * 100"))
            else: results.append(_item("operating_margin", "profitability", scope, period, "operating_profit / revenue * 100", status="unmeasured", reason="same_period_verified_revenue_and_operating_profit_required"))
            if revenue and net and _same_flow(revenue, net):
                results.append(_ratio("net_margin", "profitability", scope, period, net, revenue, "net_profit / revenue * 100"))
            else: results.append(_item("net_margin", "profitability", scope, period, "net_profit / revenue * 100", status="unmeasured", reason="same_period_verified_revenue_and_net_profit_required"))
            results.append(_ratio("liabilities_to_equity", "stability", scope, {"as_of": end}, liabilities, equity, "liabilities / equity * 100"))
            results.append(_ratio("liabilities_to_assets", "stability", scope, {"as_of": end}, liabilities, assets, "liabilities / assets * 100"))
            results.append(_borrowing_to_assets(index, held, validations, scope, end))
            if revenue and cashflow and _same_flow(revenue, cashflow):
                results.append(_ratio("operating_cashflow_to_revenue", "cashflow", scope, period, cashflow, revenue, "operating_cashflow / revenue * 100"))
            else: results.append(_item("operating_cashflow_to_revenue", "cashflow", scope, period, "operating_cashflow / revenue * 100", status="unmeasured", reason="same_period_verified_revenue_and_operating_cashflow_required"))
            if cashflow and operating and _same_flow(cashflow, operating):
                item = _item("operating_cashflow_minus_operating_profit", "cashflow", scope, period, "operating_cashflow - operating_profit", status="supported", value=_decimal(cashflow["value"]) - _decimal(operating["value"]), unit="KRW", inputs=(cashflow, operating))
                # 차이의 부호와 별개로 후속 신호가 두 검증 입력값을 직접 판단하게 한다.
                item["verified_input_values"] = {"operating_profit": _text(_decimal(operating["value"])),
                                                  "operating_cashflow": _text(_decimal(cashflow["value"]))}
                results.append(item)
            else: results.append(_item("operating_cashflow_minus_operating_profit", "cashflow", scope, period, "operating_cashflow - operating_profit", status="unmeasured", reason="same_period_verified_operating_cashflow_and_operating_profit_required"))
        for before, after in _period_pairs(index, scope):
            for account, name, category, percent in (("revenue", "revenue_growth", "profitability", True), ("operating_profit", "operating_profit_growth", "profitability", True), ("operating_cashflow", "operating_cashflow_growth", "cashflow", True), ("operating_cashflow", "operating_cashflow_change", "cashflow", False), ("equity", "equity_change", "stability", False), ("inventory", "inventory_change", "working_capital", False), ("inventory", "inventory_growth", "working_capital", True), ("receivables", "receivables_change", "working_capital", False), ("receivables", "receivables_growth", "working_capital", True)):
                results.append(_change(name, category, index, held, batch, scope, account, before, after, percent=percent))
            for account in BORROWING_ACCOUNTS:
                results.append(_change(account + "_change", "borrowing", index, held, batch, scope, account, before, after))
            results.extend(_borrowing_total_changes(index, held, batch, scope, before, after))
    return {"schema_version": VERSION, "report_id": report.get("id"), "validation_batch_id": batch.get("id"),
            "results": results, "limitations": ["결과는 검증된 표시 수치의 산술 분석이며, 통계적 이상·회계 오류·신용위험·대출 판단을 뜻하지 않습니다."]}


def _borrowing_total_changes(index, held, batch, scope, before, after):
    before_rows = [_metric_row(index, held, scope, account, before)[0] for account in BORROWING_ACCOUNTS]
    after_rows = [_metric_row(index, held, scope, account, after)[0] for account in BORROWING_ACCOUNTS]
    period = {"before_end": before, "end": after, "comparison": "validated_v11_and_v12"}
    if not all(before_rows + after_rows):
        return [_item("borrowing_total_change", "borrowing", scope, period, "sum(current V12 parts) - sum(previous V12 parts)", status="unmeasured", reason="all_verified_borrowing_parts_required")]
    all_validations = {v.get("id"): v for v in batch.get("validations", [])}
    for row in before_rows + after_rows:
        for identifier in row.get("validation_ids", []):
            if all_validations.get(identifier, {}).get("data", {}).get("checks", {}).get("V12", {}).get("status") != "pass":
                return [_item("borrowing_total_change", "borrowing", scope, period, "sum(current V12 parts) - sum(previous V12 parts)", status="withheld", inputs=tuple(before_rows + after_rows), reason="borrowing_composition_not_verified")]
    individual = [_v11(batch, after_row, before_row) for before_row, after_row in zip(before_rows, after_rows)]
    if not all(individual):
        return [_item("borrowing_total_change", "borrowing", scope, period, "sum(validated individual V11 changes)", status="withheld", inputs=tuple(before_rows + after_rows), reason="individual_borrowing_v11_mapping_not_supported")]
    delta = sum((_decimal(item["data"]["result"]) for item in individual), Decimal(0))
    return [_item("borrowing_total_change", "borrowing", scope, period, "sum(validated individual V11 changes)", status="supported", value=delta, unit="KRW", inputs=tuple(before_rows + after_rows))]


def _borrowing_to_assets(index, held, validations, scope, end):
    period = {"as_of": end}
    formula = "sum(three V12 verified borrowing parts) / assets * 100"
    parts = [_metric_row(index, held, scope, account, end)[0] for account in BORROWING_ACCOUNTS]
    assets = _metric_row(index, held, scope, "assets", end)[0]
    if not assets or not all(parts):
        return _item("borrowing_to_assets", "borrowing", scope, period, formula,
                     status="unmeasured", reason="all_verified_borrowing_parts_and_assets_required")
    inputs = tuple(parts + [assets])
    for part in parts:
        if not part.get("validation_ids") or any(
                validations.get(identifier, {}).get("data", {}).get("checks", {}).get("V12", {}).get("status") != "pass"
                for identifier in part["validation_ids"]):
            return _item("borrowing_to_assets", "borrowing", scope, period, formula,
                         status="withheld", inputs=inputs, reason="borrowing_composition_not_verified")
    denominator = _decimal(assets["value"])
    if denominator is None or denominator <= 0:
        return _item("borrowing_to_assets", "borrowing", scope, period, formula,
                     status="withheld", inputs=inputs, reason="nonpositive_assets_denominator")
    numerator = sum((_decimal(part["value"]) for part in parts), Decimal(0))
    return _item("borrowing_to_assets", "borrowing", scope, period, formula,
                 status="supported", value=numerator / denominator * 100, unit="percent", inputs=inputs)


class FinancialAnalyticsService:
    def __init__(self, store): self.store = store

    def report(self, report_id):
        report = self.store.get_report(report_id)
        if report.get("status") != "published": raise ValueError("published_report_required")
        batch_id = report["publication"]["validation_batch_id"]
        batch = NumericValidationService(self.store).batch(batch_id)
        def raw_ok(row):
            try:
                for fact_id in row.get("fact_ids", []):
                    fact = self.store.get_evidence(fact_id)
                    if not NumericValidationService._raw_check(self.store.source_content(fact["source"]["id"]), fact["fragment"]["data"]["location"]): return False
                return True
            except (ValueError, KeyError): return False
        return analyze(report, batch, raw_ok=raw_ok)
