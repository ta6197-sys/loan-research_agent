"""ML 학습 전에 데이터셋의 최소 운영 요건을 결정론적으로 판정한다."""
from decimal import Decimal
from datetime import date

from .peer_dataset import FEATURES

VERSION = "model-readiness-1.1"
MIN_ROWS, MIN_COMPANIES, MIN_ANNUAL_PERIODS, MIN_FEATURES = 150, 30, 3, 5
MIN_CORE_AVAILABILITY = Decimal("0.85")


def _annual(row):
    try:
        days = (date.fromisoformat(row['period_end']) - date.fromisoformat(row['period_start'])).days + 1
        return row.get('period_kind') == 'duration' and 350 <= days <= 380
    except (KeyError, TypeError, ValueError): return False


def evaluate(snapshot, *, core_features=None):
    all_rows = snapshot.get("rows", [])
    rows = [row for row in all_rows if _annual(row)]
    # 호출자가 넘긴 집계값 대신 실제 연간 행에서 특성 가용성을 다시 계산한다.
    missingness = {name: {'availability_rate': str(Decimal(sum(name in r.get('features', {}) for r in rows)) / len(rows)) if rows else None} for name in FEATURES}
    core_features = list(core_features or ("operating_margin", "net_margin", "liabilities_to_assets",
                                            "operating_cashflow_to_revenue", "revenue_growth"))
    invalid_features = [feature for feature in core_features if feature not in FEATURES]
    availability = {feature: Decimal(str(missingness.get(feature, {}).get("availability_rate") or 0)) for feature in core_features}
    annual_periods = sorted({row["period_end"] for row in rows if _annual(row)})
    companies = {row.get("corp_code") for row in rows}
    industries = {row.get("industry_code") for row in rows}
    scopes = {row.get("scope") for row in rows}
    accounts = {row.get("account_definition_version") for row in rows}
    peer_groups = {row.get("peer_group") for row in rows}
    origins = {row.get('data_origin') for row in rows}
    industry_versions = {row.get('industry_version') for row in rows if row.get('industry_version') is not None}
    checks = {
        "minimum_company_period_rows": {"actual": len(rows), "minimum": MIN_ROWS, "pass": len(rows) >= MIN_ROWS},
        "minimum_distinct_companies": {"actual": len(companies), "minimum": MIN_COMPANIES, "pass": len(companies) >= MIN_COMPANIES},
        "minimum_annual_periods": {"actual": len(annual_periods), "minimum": MIN_ANNUAL_PERIODS, "pass": len(annual_periods) >= MIN_ANNUAL_PERIODS},
        "minimum_effective_features": {"actual": sum(rate > 0 for rate in availability.values()), "minimum": MIN_FEATURES, "pass": sum(rate > 0 for rate in availability.values()) >= MIN_FEATURES and not invalid_features},
        "core_feature_availability": {"minimum": str(MIN_CORE_AVAILABILITY), "features": {key: {"availability_rate": str(value), "pass": value >= MIN_CORE_AVAILABILITY} for key, value in availability.items()}, "pass": all(value >= MIN_CORE_AVAILABILITY for value in availability.values()) and not invalid_features},
        "comparability": {"industry_codes": sorted(industries), "scopes": sorted(scopes), "account_definition_versions": sorted(accounts, key=str), "peer_groups": sorted(peer_groups, key=str),
                          "pass": len(industries) == len(scopes) == len(accounts) == len(peer_groups) == 1 and None not in accounts and None not in peer_groups},
        "data_origin": {"origins": sorted(origins, key=str),
                        "pass": len(origins) == 1 and next(iter(origins), None) in {'verified_public_disclosure', 'synthetic_test_only'}},
        "industry_revision": {"versions": sorted(industry_versions, key=str),
                              "pass": 'unconfirmed' not in industry_versions,
                              "reason": "official_classification_revision_required_for_training"},
        "public_cutoff": {"pass": not any(row["source_receipt"][:8] > row["public_cutoff"].replace("-", "") for row in rows), "reason": "source_receipt_date_must_not_exceed_public_cutoff"},
    }
    passed = all(check["pass"] for check in checks.values())
    return {"schema_version": VERSION, "dataset_snapshot_id": snapshot.get("snapshot_id"),
            "training_status": "ready_for_exploratory_training" if passed else "not_trained_insufficient_data",
            "checks": checks, "input_row_count": len(all_rows), "annual_row_count": len(rows), "annual_periods": annual_periods, "selected_core_features": core_features,
            "label_quality": {"status": "not_applicable_unsupervised", "operational_accuracy": None},
            "data_origin": next(iter(origins)) if len(origins) == 1 else 'mixed_or_unavailable',
            "limitations": ["모든 게이트는 최초 실험을 위한 운영상 최소 요건이며 모델 성능·통계적 신뢰성·감독 적합성을 보증하지 않습니다.",
                            "본 결과는 신용등급 예측이나 대출 승인·거절 자동화를 수행하지 않습니다."]}
