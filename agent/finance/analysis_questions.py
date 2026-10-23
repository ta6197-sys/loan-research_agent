"""통계·ML 이상 관측을 근거가 연결된 조사 질문 후보로 투영한다."""
VERSION = "analysis-questions-1.1"


def _references(refs):
    return {key: sorted({identifier for ref in refs if isinstance(ref, dict) for identifier in ref.get(field, [])})
            for key, field in (("input_fact_ids", "fact_ids"), ("input_validation_ids", "validation_ids"), ("source_ids", "source_ids"))}


def build(anomaly_result, statistics=None, target_company_id=None):
    questions = []
    for split in anomaly_result.get("splits", []):
        if split.get("status") != "measured": continue
        for item in split.get("observations", []):
            if not item.get("exploratory_anomaly") or target_company_id and item.get("corp_code") != target_company_id: continue
            refs = [ref for values in item.get("feature_evidence", {}).values() for ref in values]
            questions.append({"signal_id": "anomaly:" + split["name"] + ":" + item["row_id"],
                "observed_metric": item["anomaly_score"], "period": {"end":item["period_end"]}, "scope":item['scope'],
                "comparison": "학습 비교집단 점수의 95분위수로 고정한 탐색 임계값", **_references(refs)})
    for item in (statistics or {}).get('peer_comparisons', []):
        robust = item.get('robust_outlier', {})
        if robust.get('status') != 'measured' or not robust.get('outside_threshold'): continue
        definition = item['comparison_definition']
        questions.append({'signal_id': 'robust:' + definition['metric'] + ':' + definition['period_end'],
            'observed_metric': definition['metric'] + ': ' + item['target_value'],
            'period': {'end':definition['period_end']}, 'scope':definition['scope'],
            'comparison': '비교집단 중앙값·MAD 기준 Robust Z ' + robust['score'], **_references(item.get('evidence_refs', []))})
    for item in questions:
        item.update(status='candidate_for_followup',
            question='관찰된 지표 변화의 일시성, 구성 항목 및 원문상 정정 여부를 확인할 수 있나요?',
            requested_information='해당 기간의 계정 구성, 원인 설명, 후속 공시·정정 자료',
            limitation='통계적 특이성은 회계오류·부실·연체·신용위험을 확정하지 않습니다. 현재 자료만으로 원인을 확정할 수 없습니다.')
    return {'schema_version':VERSION,'questions':questions,
            'limitations':['질문 후보는 기존 checked_draft·검증 절차를 우회해 새로운 사실로 게시되지 않습니다.']}
