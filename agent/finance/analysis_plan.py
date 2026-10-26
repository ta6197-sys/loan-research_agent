"""검증된 신호와 계산 공백으로 다음 조사 단계를 제안하는 읽기 모델."""

VERSION = "analysis-plan-1.0"

REQUESTS = {
    "required_period_or_account_missing": "같은 범위·기간의 공시 원문과 해당 재무 계정",
    "verified_input_withheld": "보류된 입력 수치의 원문 위치와 검증 실패 사유",
    "v11_period_account_mapping_not_supported": "전후 기간의 동일 계정 정의와 V11 기간 대응 근거",
    "borrowing_composition_not_verified": "차입금 구성 항목·잔액과 원문 주석의 독립 대조 근거",
    "individual_borrowing_v11_mapping_not_supported": "개별 차입 계정의 전후 기간 대응 근거",
    "all_verified_borrowing_parts_required": "누락된 개별 차입 계정과 각 원문·검증 근거",
    "same_period_verified_revenue_and_operating_profit_required": "같은 기간·범위의 매출액과 영업이익 검증 수치",
    "same_period_verified_revenue_and_net_profit_required": "같은 기간·범위의 매출액과 순이익 검증 수치",
    "same_period_verified_revenue_and_operating_cashflow_required": "같은 기간·범위의 매출액과 영업현금흐름 검증 수치",
    "same_period_verified_operating_cashflow_and_operating_profit_required": "같은 기간·범위의 영업이익과 영업현금흐름 검증 수치",
    "zero_or_nonpositive_comparison_base": "비교 기준값과 증감률 대신 사용할 금액 변화 근거",
    "zero_or_inappropriate_denominator": "분모의 회계 의미와 원문 수치",
    "required_verified_input_missing": "해당 계정의 공시 원문과 독립 수치 검증 근거",
    "input_missing": "해당 계정·기간·범위의 공시 재무제표와 수치 대조 자료",
    "composition_or_numeric_evidence_unconfirmed": "차입금 구성 주석과 각 항목의 원문·API·XBRL 대응 근거",
    "correction_history_unconfirmed": "원본·정정본 접수 관계와 기준일까지의 공식 전체 공시목록",
}


def _refs(items):
    return {key: sorted({identifier for item in items for identifier in item.get(key, [])})
            for key in ("input_fact_ids", "input_validation_ids", "source_ids")}


def build(report_id, validation_batch_id, metrics, signals, financial_rows=()):
    """비교집단과 무관하게 단일기업의 검증 상태만으로 재현 가능한 계획을 만든다."""
    steps = []
    for signal in sorted(signals, key=lambda item: item["signal_id"]):
        if signal.get("status") != "candidate_for_followup":
            continue
        steps.append({"id": "signal:" + signal["signal_id"], "kind": "verified_signal_followup",
                      "status": "candidate_for_followup", "reason_code": None,
                      "question": signal["question"],
                      "requested_information": "해당 기간 계정 구성과 변화 원인에 대한 원문·후속 공시 또는 담당자 자료",
                      "scope": signal.get("scope"), "periods": [signal.get("period")],
                      "related_metric_ids": [], **_refs([signal]),
                      "limitation": "검증된 수치의 동시 변화는 원인·신용위험을 확정하지 않습니다."})
    grouped = {}
    for metric in metrics:
        if metric.get("status") not in {"withheld", "unmeasured"}:
            continue
        reason = metric.get("reason_code") or "analysis_input_unavailable"
        key = (metric.get("scope"), reason)
        grouped.setdefault(key, []).append(metric)
    for (scope, reason), items in sorted(grouped.items(), key=lambda pair: (str(pair[0][0]), pair[0][1])):
        periods = {str(item.get("period")): item.get("period") for item in items}
        steps.append({"id": "gap:" + str(scope) + ":" + reason,
                      "kind": "verification_gap", "status": "evidence_required",
                      "reason_code": reason,
                      "question": "이 분석 항목의 수치·기간·계정 대응을 확인할 자료가 있나요?",
                      "requested_information": REQUESTS.get(reason, "해당 계정의 공시 원문과 검증 근거"),
                      "scope": scope, "periods": [periods[key] for key in sorted(periods)],
                      "related_metric_ids": sorted(item["metric_id"] for item in items),
                      **_refs(items),
                      "limitation": "자료 요청은 미검증 값을 수치나 사실로 승격하지 않습니다."})
    row_gaps={}
    for row in financial_rows:
        if row.get('status')!='withheld':continue
        reason=row.get('reason') or 'analysis_input_unavailable'
        key=(row.get('scope'),row.get('account'),reason)
        row_gaps.setdefault(key,[]).append(row)
    for (scope,account,reason),items in sorted(row_gaps.items(),key=lambda pair:tuple(str(x) for x in pair[0])):
        periods=sorted({row.get('end') for row in items if row.get('end')})
        label=items[0].get('label') or account
        refs=[{'input_fact_ids':row.get('fact_ids',[]),
               'input_validation_ids':row.get('validation_ids',[]),
               'source_ids':row.get('source_ids',[])} for row in items]
        steps.append({'id':'account-gap:'+str(scope)+':'+str(account)+':'+reason,
                      'kind':'account_verification_gap','status':'evidence_required',
                      'reason_code':reason,
                      'question':f'{label}의 원문 수치와 계정·기간 대응을 확인할 수 있나요?',
                      'requested_information':REQUESTS.get(reason,'해당 계정의 공시 원문과 검증 근거'),
                      'scope':scope,'periods':[{'end':end} for end in periods],
                      'related_metric_ids':[],**_refs(refs),
                      'limitation':'해당 계정은 미검증 상태이며 금액을 0으로 대체하지 않습니다.'})
    return {"version": VERSION, "report_id": report_id, "validation_batch_id": validation_batch_id,
            "status": "review_items_available" if steps else "no_review_items",
            "steps": steps, "limitations": ["계획과 질문은 공개 자료 사전조사 후보이며 대출·신용위험 판단이 아닙니다."]}
