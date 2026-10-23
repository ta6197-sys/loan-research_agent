"""검증된 동질 데이터셋의 탐색적 이상 관측치 탐지.

IsolationForest 점수는 부도·신용위험 확률이 아니며 검증 상태를 변경하지 않는다.
"""
from decimal import Decimal
import hashlib

import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .model_readiness import evaluate as readiness, _annual

VERSION = "anomaly-detection-1.1"
RANDOM_STATE = 20261023
N_ESTIMATORS = 200
MIN_TRAIN_ROWS, MIN_EVALUATION_ROWS = 30, 10


def _id(row):
    return "|".join(str(row[key]) for key in ("corp_code", "period_end", "scope", "validation_batch_id"))


def _value(row, feature):
    try: return float(Decimal(row["features"][feature]))
    except (KeyError, ValueError, TypeError): return None


def _complete(rows, features):
    accepted, excluded = [], []
    for row in rows:
        values = [_value(row, feature) for feature in features]
        if any(value is None or not np.isfinite(value) for value in values):
            excluded.append({"row_id": _id(row), "reason": "missing_or_non_numeric_model_feature"})
        else: accepted.append((row, values))
    return accepted, excluded


def _quantile(values, q):
    return float(np.quantile(np.asarray(values, dtype=float), q, method="linear"))


def _baseline(train, test, features):
    train_matrix = np.asarray([values for _, values in train], dtype=float)
    output = {}
    for row, values in test:
        metrics = {}
        for index, feature in enumerate(features):
            samples = train_matrix[:, index]; median = float(np.median(samples)); mad = float(np.median(np.abs(samples - median)))
            metrics[feature] = {"value": str(values[index]), "median": str(median),
                                "percentile_rank": str(100 * (np.sum(samples < values[index]) + .5 * np.sum(samples == values[index])) / len(samples)),
                                "robust_z": None if mad == 0 else str(.6745 * (values[index] - median) / mad),
                                "robust_z_status": "unmeasured_zero_mad" if mad == 0 else "measured"}
        output[_id(row)] = metrics
    return output


def _fit_score(train, test, features):
    pipeline = Pipeline([("scale", StandardScaler()), ("isolation_forest", IsolationForest(
        n_estimators=N_ESTIMATORS, contamination="auto", random_state=RANDOM_STATE, n_jobs=1))])
    x_train = np.asarray([values for _, values in train], dtype=float)
    x_test = np.asarray([values for _, values in test], dtype=float)
    pipeline.fit(x_train)
    scores = -pipeline.score_samples(x_test)  # 큰 값일수록 훈련 분포에서 멀다.
    train_scores = -pipeline.score_samples(x_train)
    threshold = _quantile(train_scores, .95)
    return pipeline, scores, threshold, train_scores


def _split_time(rows):
    ends = sorted({row["period_end"] for row in rows})
    if len(ends) < 2: return [], rows, {"status": "withheld", "reason": "at_least_two_periods_required"}
    cutoff = ends[-1]
    return [row for row in rows if row["period_end"] < cutoff], [row for row in rows if row["period_end"] == cutoff], {"status": "measured", "holdout_period": cutoff}


def _split_company(rows):
    companies = sorted({row["corp_code"] for row in rows})
    holdout = {company for company in companies if int(hashlib.sha256(company.encode()).hexdigest(), 16) % 5 == 0}
    if not holdout and companies: holdout = {companies[-1]}
    return [row for row in rows if row["corp_code"] not in holdout], [row for row in rows if row["corp_code"] in holdout], {"status": "measured", "holdout_companies": sorted(holdout)}


def _run_split(name, rows, features, splitter):
    train_rows, test_rows, detail = splitter(rows)
    # 과거 행이라도 나중에 공개된 정정본이면 학습 시점에 사용할 수 없다.
    training_cutoff = (min((row.get("source_public_at", row["source_receipt"][:8]).replace("-", "") for row in test_rows), default="")
                       if name == "time_ordered" else min((row["public_cutoff"].replace("-", "") for row in rows), default=""))
    future_train = [row for row in train_rows if row.get("source_public_at", row["source_receipt"][:8]).replace("-", "") > training_cutoff]
    train_rows = [row for row in train_rows if row not in future_train]
    train, excluded_train = _complete(train_rows, features); test, excluded_test = _complete(test_rows, features)
    excluded_train.extend({"row_id": _id(row), "reason": "published_after_training_cutoff"} for row in future_train)
    result = {"name": name, "split": detail, "training_row_count": len(train), "evaluation_row_count": len(test),
              "training_company_count": len({row["corp_code"] for row, _ in train}), "evaluation_company_count": len({row["corp_code"] for row, _ in test}),
              "excluded_rows": excluded_train + excluded_test}
    if detail["status"] != "measured" or len(train) < MIN_TRAIN_ROWS or len(test) < MIN_EVALUATION_ROWS:
        result.update(status="not_trained_insufficient_split_data", reason="minimum_30_training_and_10_evaluation_complete_rows_required")
        return result
    _, scores, threshold, train_scores = _fit_score(train, test, features)
    baseline = _baseline(train, test, features)
    observations = []
    for (row, _), score in zip(test, scores):
        observations.append({"row_id": _id(row), "corp_code": row["corp_code"], "period_end": row["period_end"],
                             "scope": row["scope"], "anomaly_score": str(float(score)),
                             "exploratory_anomaly": bool(score >= threshold), "baseline": baseline[_id(row)],
                             "feature_evidence": {feature: row["feature_evidence"].get(feature, []) for feature in features},
                             "limitation": "탐색적 특이성은 회계오류·부실·연체·신용위험을 뜻하지 않습니다."})
    sensitivity = []
    for quantile in (.90, .95, .975):
        boundary = _quantile(train_scores, quantile)
        candidates = sorted(_id(row) for (row, _), score in zip(test, scores) if score >= boundary)
        sensitivity.append({"quantile": str(quantile), "threshold": str(boundary), "threshold_source": "training_scores",
                            "candidate_count": len(candidates), "candidate_row_ids": candidates})
    return {**result, "status": "measured", "score_threshold_quantile": "0.95", "score_threshold": str(float(threshold)),
            "threshold_source": "training_scores", "training_public_cutoff": training_cutoff,
            "training_row_ids": [_id(row) for row, _ in train], "evaluation_row_ids": [_id(row) for row, _ in test],
            "training_periods": sorted({row["period_end"] for row, _ in train}),
            "training_scores": [str(float(score)) for score in train_scores], "sensitivity": sensitivity,
            "observations": sorted(observations, key=lambda item: float(item["anomaly_score"]), reverse=True)}


def detect(snapshot, *, features=None):
    """적격성·누수 방지 분할을 통과한 경우에만 탐색 모델을 실행한다."""
    gate = readiness(snapshot, core_features=features)
    selected = list(features or gate["selected_core_features"])
    base = {"schema_version": VERSION, "dataset_snapshot_id": snapshot.get("snapshot_id"), "model": {"name": "IsolationForest", "sklearn_version": sklearn.__version__, "random_state": RANDOM_STATE, "n_estimators": N_ESTIMATORS, "feature_policy_version": snapshot.get("feature_policy_version")},
            "data_origin": snapshot.get("cohort", {}).get("data_origin", "unspecified"),
            "features": selected, "feature_missingness": {feature: snapshot.get("feature_missingness", {}).get(feature) for feature in selected}, "readiness": gate}
    if gate["training_status"] != "ready_for_exploratory_training":
        return {**base, "status": "not_trained_insufficient_data", "reason": "model_readiness_gate_not_passed", "splits": []}
    rows = [row for row in snapshot.get("rows", []) if _annual(row)]
    splits = [_run_split("time_ordered", rows, selected, _split_time), _run_split("company_holdout", rows, selected, _split_company)]
    return {**base, "status": "measured" if any(split["status"] == "measured" for split in splits) else "not_trained_insufficient_split_data", "industry_group": rows[0].get("peer_group") if rows else None,
            "splits": splits,
            "limitations": ["IsolationForest 점수는 부도확률·신용위험 확률이 아닙니다.", "모델 결과는 원문의 검증 통과·보류 상태를 변경하지 않습니다.", "결측 특성은 0 또는 추정치로 대체하지 않고 해당 분할에서 제외합니다."]}
