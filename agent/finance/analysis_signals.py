"""결정론적 분석 결과에서 추가 확인 후보만 만든다. 위험 판단을 하지 않는다."""
from decimal import Decimal, InvalidOperation

VERSION = "analysis-signals-1.0"


def _value(item):
    try: return Decimal(item["value"])
    except (InvalidOperation, KeyError, TypeError): return None


def build(analytics):
    values = {(item["scope"], item["name"], item["period"].get("end")): item
              for item in analytics.get("results", []) if item.get("status") == "supported"}
    signals = []
    for (scope, name, end), item in values.items():
        def add(signal, evidence, question, limitation):
            signals.append({"id": f"{signal}:{scope}:{end}", "signal": signal, "scope": scope, "period": item["period"],
                            "status": "candidate_for_followup", "evidence": evidence, "question": question,
                            "limitation": limitation})
        revenue = values.get((scope, "revenue_growth", end))
        if name == "inventory_growth" and revenue and _value(item) > _value(revenue):
            add("inventory_growth_exceeds_revenue_growth", [item, revenue], "재고 증가분의 품목·회전·판매 예정 시점을 확인할 수 있나요?", "잔액과 매출 증감의 비교이며 재고평가·수요·부실을 판단하지 않습니다.")
        if name == "receivables_growth" and revenue and _value(item) > _value(revenue):
            add("receivables_growth_exceeds_revenue_growth", [item, revenue], "매출채권 및 기타유동채권 증가분의 구성과 회수 예정 시점을 확인할 수 있나요?", "기타채권이 포함돼 DSO·회수불능 판단에 사용하지 않습니다.")
        if name == "operating_cashflow_growth" and _value(item) < 0:
            add("operating_cashflow_deterioration", [item], "영업현금흐름 감소의 주요 조정 항목과 일시적 요인을 확인할 수 있나요?", "동기 대비 감소 관찰이며 원인·지속성·상환능력을 확정하지 않습니다.")
        if name == "operating_cashflow_minus_operating_profit":
            inputs = item.get("verified_input_values", {})
            profit, cashflow_value = _value({"value": inputs.get("operating_profit")}), _value({"value": inputs.get("operating_cashflow")})
            if profit is not None and cashflow_value is not None and profit > 0 and cashflow_value < 0:
                add("positive_profit_negative_operating_cashflow", [item], "영업이익은 양수이나 영업현금흐름이 음수인 원인을 만드는 운전자본·비현금 조정 항목을 확인할 수 있나요?", "검증된 같은 기간의 영업이익 양수·영업현금흐름 음수 관찰이며 회계오류나 신용위험을 뜻하지 않습니다.")
        cashflow = values.get((scope, "operating_cashflow_change", end))
        if name == "borrowing_total_change" and _value(item) > 0 and cashflow and _value(cashflow) < 0:
            add("borrowing_increase_with_cashflow_deterioration", [item, cashflow], "차입 증가의 실제 용도와 영업현금흐름 감소에 대한 대응 계획을 확인할 수 있나요?", "동시 변화는 인과·차입 용도·상환 가능성을 입증하지 않습니다.")
    return {"schema_version": VERSION, "analytics_version": analytics.get("schema_version"), "report_id": analytics.get("report_id"),
            "signals": signals, "limitations": ["확인 신호는 추가 조사 후보이며 금융 위험·회계 오류·부실·연체·상환불능 판단이 아닙니다."]}
