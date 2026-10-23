"""라벨 없는 탐색 모델의 분할·안정성·기준선 비교와 합성 오류 평가."""
VERSION = "model-evaluation-1.0"


def _candidates(split):
    return {item["row_id"] for item in split.get("observations", []) if item.get("exploratory_anomaly")}


def evaluate(result):
    splits = [item for item in result.get("splits", []) if item.get("status") == "measured"]
    sets = [_candidates(item) for item in splits]
    overlap = len(sets[0] & sets[1]) / len(sets[0] | sets[1]) if len(sets) == 2 and sets[0] | sets[1] else None
    baseline_ml = []
    for split in splits:
        for item in split.get("observations", []):
            baseline_candidate = any(abs(float(metric["robust_z"])) >= 3.5 for metric in item["baseline"].values() if metric["robust_z"] is not None)
            baseline_ml.append({"row_id": item["row_id"], "ml_candidate": item["exploratory_anomaly"], "baseline_candidate": baseline_candidate})
    return {"schema_version": VERSION, "dataset_snapshot_id": result.get("dataset_snapshot_id"),
            "status": "measured" if splits else "not_evaluated_no_trained_split", "label_status": "unlabeled_no_operational_accuracy",
            "splits": [{key: split.get(key) for key in ("name", "training_row_count", "evaluation_row_count", "training_company_count", "evaluation_company_count", "split")} for split in result.get("splits", [])],
            "candidate_stability": {"comparison": "time_ordered_vs_company_holdout", "jaccard": overlap, "status": "unmeasured" if overlap is None else "measured"},
            "baseline_ml_comparison": baseline_ml,
            "sensitivity": {"score_percentile_thresholds": ["0.90", "0.95", "0.975"], "model_settings": result.get("model"), "period_change": "time_ordered split reported separately"},
            "top_case_evidence_review": [{"row_id": item["row_id"], "feature_evidence": item["feature_evidence"]} for split in splits for item in split.get("observations", [])[:5]],
            "limitations": ["정답 라벨이 없어 실제 운영 정확도·부도 예측 성능을 산출하지 않습니다.", "합성 오류 평가는 실제 금융 부실·오류 탐지 성능으로 표시하지 않습니다."]}


def evaluate_synthetic(injected_row_ids, detected_row_ids, universe_row_ids):
    """명시적으로 합성 오류라고 표시하는 별도 평가 보조 함수."""
    injected, detected, universe = set(injected_row_ids), set(detected_row_ids), set(universe_row_ids)
    tp, fp = len(injected & detected), len(detected - injected)
    fn, tn = len(injected - detected), len(universe - injected - detected)
    return {"status": "synthetic_evaluation_only", "true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn,
            "detection_rate": tp / len(injected) if injected else None, "precision": tp / len(detected) if detected else None,
            "recall": tp / len(injected) if injected else None, "false_positive_rate": fp / (fp + tn) if fp + tn else None,
            "limitation": "합성 오류 성능은 실제 금융 부실·회계오류·신용위험 탐지 성능이 아닙니다."}
