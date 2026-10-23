"""탐색적 통계·ML 관측치를 추가 조사 질문 후보로만 투영한다."""
VERSION = "analysis-questions-1.0"


def build(anomaly_result):
    questions = []
    for split in anomaly_result.get("splits", []):
        if split.get("status") != "measured":
            continue
        for item in split.get("observations", []):
            if not item.get("exploratory_anomaly"):
                continue
            evidence = [ref for refs in item.get("feature_evidence", {}).values() for ref in refs]
            questions.append({"signal_id": "anomaly:" + split["name"] + ":" + item["row_id"],
                              "status": "candidate_for_followup", "observed_metric": item["anomaly_score"],
                              "comparison": "동일 비교집단의 IsolationForest 탐색 점수 상위 5%", "source_ids": evidence,
                              "question": "관찰된 지표 변화의 일시성, 구성 항목 및 원문상 정정 여부를 확인할 수 있나요?",
                              "requested_information": "해당 기간의 계정 구성, 원인 설명, 후속 공시·정정 자료",
                              "limitation": "통계적 특이성은 회계오류·부실·연체·신용위험을 확정하지 않습니다. 현재 자료만으로 원인을 확정할 수 없습니다."})
    return {"schema_version": VERSION, "questions": questions,
            "limitations": ["질문 후보는 검증되지 않은 생성 사실이 아니며 기존 checked_draft·검증 절차를 우회하지 않습니다."]}
