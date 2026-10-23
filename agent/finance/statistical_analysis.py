"""검증된 재무분석 결과를 위한 탐색적 시계열·비교집단 통계 모델.

이 모듈은 신용판단이나 회계오류 판정을 하지 않으며, 원문·검증 배치도 변경하지
않는다. 입력 관측치는 Phase 1에서 ``supported`` 상태인 결과만 허용한다.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

VERSION = "statistical-analysis-1.0"
MIN_PEER_COMPANIES = 10
MIN_PEER_OBSERVATIONS = 20
MIN_RELATIONSHIP_COMPANIES = 10
MIN_RELATIONSHIP_OBSERVATIONS = 20
DEFAULT_ROBUST_Z_THRESHOLD = Decimal("3.5")


def _decimal(value):
    try:
        value = Decimal(str(value))
        return value if value.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _text(value):
    return format(value, "f") if isinstance(value, Decimal) else value


def _period_end(item):
    period = item.get("period", {})
    return period.get("end") or period.get("as_of")


def observations_from_financial_analytics(company_id, industry_group, analytics, *, accounting_standard,
                                          currency, public_at):
    """Phase 1 출력에서 비교 가능한 표준 관측치만 만든다.

    통화·회계기준·산업은 분석 엔진이 추정하지 않는다. 수집 단계가 제공한 값만
    입력받아, 누락 시 이후 비교집단 구성에서 보류된다.
    """
    result = []
    for item in analytics.get("results", []):
        if item.get("status") != "supported" or _decimal(item.get("value")) is None:
            continue
        period = item.get("period", {})
        end = _period_end(item)
        if not end:
            continue
        result.append({
            "company_id": company_id, "industry_group": industry_group,
            "metric": item.get("name"), "value": item["value"], "unit": item.get("unit"),
            "scope": item.get("scope"), "period_kind": period.get("kind"), "period_end": end,
            "currency": currency, "accounting_standard": accounting_standard,
            "public_at": public_at, "source_result_id": item.get("id"),
            "verification_status": "supported",
        })
    return result


def _valid(observations, as_of):
    """검증된 수치와 비교 정의에 필요한 필드를 가진 관측치만 사용한다."""
    required = ("company_id", "industry_group", "metric", "scope", "period_kind", "period_end",
                "currency", "accounting_standard", "public_at")
    accepted, withheld = [], []
    for item in observations:
        value = _decimal(item.get("value"))
        if item.get("verification_status") != "supported" or value is None:
            withheld.append({"reason": "unsupported_or_non_numeric_observation", "observation": item})
        elif any(not item.get(field) for field in required):
            withheld.append({"reason": "incomplete_comparison_definition", "observation": item})
        elif as_of is not None and item["public_at"] > as_of:
            withheld.append({"reason": "not_publicly_available_as_of_reference_date", "observation": item})
        else:
            accepted.append({**item, "_value": value})
    return accepted, withheld


def _quantile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    position = Decimal(len(values) - 1) * fraction
    lower = int(position // 1)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _descriptive(values):
    values = sorted(values)
    count = len(values)
    if not count:
        return None
    mean = sum(values, Decimal(0)) / count
    variance = (sum((value - mean) ** 2 for value in values) / (count - 1)) if count > 1 else None
    q1, median, q3 = (_quantile(values, Decimal("0.25")), _quantile(values, Decimal("0.5")),
                      _quantile(values, Decimal("0.75")))
    return {"observation_count": count, "mean": _text(mean), "median": _text(median),
            "standard_deviation": _text(variance.sqrt()) if variance is not None else None,
            "q1": _text(q1), "q3": _text(q3), "iqr": _text(q3 - q1),
            "minimum": _text(values[0]), "maximum": _text(values[-1])}


def _percentile(value, values):
    # 동점의 중간순위를 이용해 0~100 범위로 나타낸다.
    less = sum(candidate < value for candidate in values)
    equal = sum(candidate == value for candidate in values)
    return Decimal(100) * (Decimal(less) + Decimal(equal) / 2) / len(values)


def _robust_score(value, values, threshold):
    median = _quantile(values, Decimal("0.5"))
    mad = _quantile([abs(candidate - median) for candidate in values], Decimal("0.5"))
    if len(values) < MIN_PEER_OBSERVATIONS:
        return {"status": "insufficient_peer_data", "reason": "minimum_peer_observations_not_met"}
    if mad == 0:
        return {"status": "unmeasured", "reason": "zero_mad"}
    score = Decimal("0.6745") * (value - median) / mad
    return {"status": "measured", "method": "robust_z_median_mad", "score": _text(score),
            "threshold": _text(threshold), "sensitivity_thresholds": [_text(Decimal("2.5")), _text(threshold), _text(Decimal("4.5"))],
            "outside_threshold": abs(score) >= threshold,
            "limitation": "통계적 특이성은 회계오류·신용위험·대출판단을 뜻하지 않습니다."}


def _peer_key(item):
    return tuple(item[field] for field in ("industry_group", "metric", "scope", "period_kind", "period_end",
                                            "currency", "accounting_standard"))


def _comparison(target_company_id, items, threshold):
    groups = defaultdict(list)
    for item in items:
        groups[_peer_key(item)].append(item)
    output = []
    for key, peers in sorted(groups.items()):
        targets = [item for item in peers if item["company_id"] == target_company_id]
        if not targets:
            continue
        values = [item["_value"] for item in peers]
        companies = {item["company_id"] for item in peers}
        definition = dict(zip(("industry_group", "metric", "scope", "period_kind", "period_end", "currency", "accounting_standard"), key))
        gate = len(companies) >= MIN_PEER_COMPANIES and len(peers) >= MIN_PEER_OBSERVATIONS
        for target in targets:
            row = {"comparison_definition": definition, "target_company_id": target_company_id,
                   "target_value": _text(target["_value"]), "evidence_refs": target.get("evidence_refs", []), "sample_size": len(peers),
                   "distinct_company_count": len(companies), "status": "measured" if gate else "insufficient_peer_data"}
            if gate:
                row.update(descriptive=_descriptive(values), percentile_rank=_text(_percentile(target["_value"], values)),
                           robust_outlier=_robust_score(target["_value"], values, threshold))
            else:
                row["reason"] = "minimum_10_companies_and_20_company_period_observations_required"
            output.append(row)
    return output


def _trend(target_company_id, items):
    groups = defaultdict(list)
    for item in items:
        if item["company_id"] == target_company_id:
            groups[(item["metric"], item["scope"], item["period_kind"], item["currency"], item["accounting_standard"])].append(item)
    output = []
    for key, values in sorted(groups.items()):
        values.sort(key=lambda item: item["period_end"])
        periods = [item["period_end"] for item in values]
        numbers = [item["_value"] for item in values]
        changes = []
        for before, after in zip(values, values[1:]):
            same_calendar = before["period_end"][5:] == after["period_end"][5:]
            changes.append({"from": before["period_end"], "to": after["period_end"],
                            "status": "observed_comparable" if same_calendar else "withheld",
                            "value": _text(after["_value"] - before["_value"]) if same_calendar else None,
                            "reason": None if same_calendar else "non_matching_period_end"})
        output.append({"metric": key[0], "scope": key[1], "period_kind": key[2], "currency": key[3],
                       "accounting_standard": key[4], "observation_count": len(values), "periods": periods,
                       "descriptive": _descriptive(numbers), "observed_values": [{"period_end": row["period_end"], "value": _text(row["_value"]), "evidence_refs": row.get("evidence_refs", [])} for row in values], "period_changes": changes,
                       "observed_direction": "up" if len(numbers) > 1 and numbers[-1] > numbers[0] else ("down" if len(numbers) > 1 and numbers[-1] < numbers[0] else "flat_or_single_observation"),
                       "missing_periods": {"status": "unmeasured", "reason": "expected_reporting_calendar_not_provided"},
                       "long_term_or_seasonal_inference": {"status": "withheld", "reason": "observed_values_only_no_population_or_seasonality_conclusion"}})
    return output


def _ranks(values):
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [Decimal(0)] * len(values)
    index = 0
    while index < len(ordered):
        end = index
        while end + 1 < len(ordered) and ordered[end + 1][1] == ordered[index][1]:
            end += 1
        rank = (Decimal(index + 1) + Decimal(end + 1)) / 2
        for position in range(index, end + 1):
            ranks[ordered[position][0]] = rank
        index = end + 1
    return ranks


def _spearman(left, right):
    rx, ry = _ranks(left), _ranks(right)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    denominator = (sum((x - mx) ** 2 for x in rx) * sum((y - my) ** 2 for y in ry)).sqrt()
    return None if denominator == 0 else sum((x - mx) * (y - my) for x, y in zip(rx, ry)) / denominator


def _relationships(items):
    pairs = (("revenue_growth", "inventory_growth"), ("revenue_growth", "receivables_growth"),
             ("operating_margin", "operating_cashflow_to_revenue"), ("borrowing_total_change", "operating_cashflow_change"))
    indexed = {(item["company_id"], item["scope"], item["period_kind"], item["period_end"], item["currency"], item["accounting_standard"], item["metric"]): item for item in items}
    output = []
    for left_metric, right_metric in pairs:
        matched = []
        for key, left in indexed.items():
            right = indexed.get((*key[:-1], right_metric))
            if left["metric"] == left_metric and right and left["industry_group"] == right["industry_group"]:
                matched.append((left, right))
        companies = {left["company_id"] for left, _ in matched}
        row = {"variables": [left_metric, right_metric], "observation_count": len(matched), "distinct_company_count": len(companies)}
        if len(matched) < MIN_RELATIONSHIP_OBSERVATIONS or len(companies) < MIN_RELATIONSHIP_COMPANIES:
            row.update(status="insufficient_peer_data", reason="minimum_10_companies_and_20_matched_company_period_observations_required")
        else:
            coefficient = _spearman([left["_value"] for left, _ in matched], [right["_value"] for _, right in matched])
            row.update(status="measured" if coefficient is not None else "unmeasured", method="spearman_rank_correlation",
                       coefficient=_text(coefficient) if coefficient is not None else None,
                       p_value=None, p_value_status="withheld", reason="same_company_multiple_periods_are_not_independent; causal_inference_not_available")
        output.append(row)
    return output


def analyze(target_company_id, observations, *, analysis_as_of=None, robust_z_threshold=DEFAULT_ROBUST_Z_THRESHOLD):
    """순수 읽기 함수. 비교·이상치는 적격한 관측치가 충분할 때만 반환한다."""
    threshold = _decimal(robust_z_threshold)
    if threshold is None or threshold <= 0:
        raise ValueError("positive_robust_z_threshold_required")
    items, withheld = _valid(observations, analysis_as_of)
    return {"schema_version": VERSION, "target_company_id": target_company_id, "analysis_as_of": analysis_as_of,
            "input_observation_count": len(observations), "eligible_observation_count": len(items),
            "excluded_observation_count": len(withheld), "excluded_observations": withheld,
            "trend_analysis": _trend(target_company_id, items),
            "peer_comparisons": _comparison(target_company_id, items, threshold),
            "relationship_analysis": _relationships(items),
            "limitations": ["비교집단 최소 요건 10개 기업·20개 기업-기간 관측치는 초기 탐색의 엔지니어링 게이트이며 법적·감독상 기준이나 통계적 신뢰성 보증이 아닙니다.",
                            "통계적 특이성·상관은 회계오류·신용위험·인과관계·대출판단을 뜻하지 않습니다."]}
