"""검증된 재무 수치에서 변화와 조사 가설을 분리한다.

이 모듈은 원인을 확정하지 않는다. 가설은 확인된 변화 조합과 반대 관찰, 다음에 확인할
공시·내부 정보를 함께 반환한다.
"""
from decimal import Decimal, InvalidOperation
from datetime import date


BORROWING_ACCOUNTS = ("short_term_borrowings", "current_long_term_borrowings", "long_term_borrowings")
FLOW_ACCOUNTS = {"revenue", "operating_profit", "operating_cashflow"}

FOLLOWUP_QUERIES=(('borrowing_note','차입금'),('cashflow_note','영업활동'),('working_capital_note','재고자산'))


def _decimal(row):
    try:
        return Decimal(row["value"])
    except (KeyError, InvalidOperation):
        return None


def _verified(rows, account, end):
    candidates = [row for row in rows if row.get("scope") == "OFS" and row.get("account") == account
                  and row.get("end") == end and row.get("status") == "verified" and _decimal(row) is not None]
    return candidates[0] if len(candidates) == 1 else None


def _fact_refs(*rows):
    return sorted({fact_id for row in rows if row for fact_id in row.get("fact_ids", [])})


def _change(account, before, after, *, label=None):
    old, new = _decimal(before), _decimal(after)
    if old is None or new is None:
        return None
    return {"id": f"change:{account}:{before['end']}:{after['end']}", "account": account,
            "label": label or after.get("label", account), "scope": "OFS", "before_end": before["end"],
            "after_end": after["end"], "before_value": str(old), "after_value": str(new),
            "delta": str(new - old), "direction": "increase" if new > old else "decrease" if new < old else "flat",
            "fact_ids": _fact_refs(before, after)}


def _borrowing(rows, end):
    parts = [_verified(rows, account, end) for account in BORROWING_ACCOUNTS]
    if any(part is None for part in parts):
        return None
    return {"end": end, "value": str(sum(_decimal(part) for part in parts)), "fact_ids": _fact_refs(*parts),
            "label": "차입금 3계정 소계"}


def _period_pairs(rows, scope='OFS'):
    grouped = {"annual": set(), "interim": set()}
    for row in rows:
        if row.get('scope') != scope:continue
        period=row.get('period') or {}
        kind=period.get('kind')
        if kind=='duration':
            try:start,end=date.fromisoformat(period['start']),date.fromisoformat(period['end'])
            except (KeyError,ValueError):continue
            length=(end.year-start.year)*12+end.month-start.month+1
            if start.day!=1 or length not in (6,12):continue
            grouped['annual' if length==12 else 'interim'].add((end.isoformat(),start.isoformat()))
        elif not period:
            end=row.get('end','')
            if end.endswith('-12-31'):grouped['annual'].add((end,None))
            elif end.endswith('-06-30'):grouped['interim'].add((end,None))
    pairs = []
    for kind in ('annual','interim'):
        values=sorted(grouped[kind])
        if len(values)<2:continue
        before,after=values[-2],values[-1]
        if before[0][5:]!=after[0][5:] or int(after[0][:4])-int(before[0][:4])!=1:continue
        if before[1] and after[1] and (before[1][5:]!=after[1][5:] or
                int(after[1][:4])-int(before[1][:4])!=1):continue
        pairs.append((before[0],after[0],kind))
    return pairs


def analyze_financial_changes(rows):
    """변화 사실, 가설, 반대 관찰, 추가 행동을 각각 구조화한다."""
    changes, hypotheses, questions, withheld = [], [], [], []
    verified = [row for row in rows if row.get("scope") == "OFS" and row.get("status") == "verified"]
    for before_end, after_end, period_kind in _period_pairs(verified):
        pairs = {}
        for account in ("inventory", "receivables", "operating_cashflow", "cash", "revenue"):
            change = _change(account, _verified(verified, account, before_end), _verified(verified, account, after_end)) \
                if _verified(verified, account, before_end) and _verified(verified, account, after_end) else None
            if change:
                pairs[account] = change; changes.append(change)
        borrowing_before, borrowing_after = _borrowing(verified, before_end), _borrowing(verified, after_end)
        borrowing_change = _change("borrowing_total", borrowing_before, borrowing_after, label="차입금 3계정 소계") \
            if borrowing_before and borrowing_after else None
        if borrowing_change:
            pairs["borrowing_total"] = borrowing_change; changes.append(borrowing_change)
        if not borrowing_change or borrowing_change["direction"] != "increase":
            continue
        working_up = [pairs[key] for key in ("inventory", "receivables") if pairs.get(key, {}).get("direction") == "increase"]
        cfo_down = pairs.get("operating_cashflow", {}).get("direction") == "decrease"
        if working_up and cfo_down:
            support = [borrowing_change, *working_up, pairs["operating_cashflow"]]
            hypotheses.append({"id": f"hypothesis:working_capital:{after_end}", "period_kind": period_kind,
                "status": "possible_explanation", "title": "운전자본 부담과 차입 증가의 관련 가능성",
                "statement": "차입금 증가와 재고·채권 증가, 영업현금흐름 감소가 같은 비교 기간에 관찰됐다. 이 조합은 운전자본 부담과 차입 확대의 관련 가능성을 보여 주지만, 실제 차입 용도를 입증하지는 않는다.",
                "supporting_change_ids": [item["id"] for item in support], "fact_ids": _fact_refs(*[{
                    "fact_ids": item["fact_ids"]} for item in support]), "counter_evidence": [],
                "required_evidence": ["차입금 주석의 신규 차입·상환·용도", "재고 구성·회전 및 매출채권 회수 일정", "영업현금흐름 조정 내역"],
                "limitation": "매출채권 및 기타채권 합계는 순수 매출채권이 아니므로 DSO·CCC를 계산하지 않는다."})
            questions.append({"id": f"question:working_capital:{after_end}", "question": "재고와 매출채권 증가분의 품목·회수 예정 시점, 그리고 해당 기간 신규 차입의 실제 사용처를 확인할 수 있나요?",
                "reason": "공개 수치에서 운전자본 부담과 차입 증가가 함께 관찰됐지만 인과관계와 회수 계획은 확인되지 않았습니다.",
                "related_hypothesis_id": hypotheses[-1]["id"]})
        elif not working_up or not cfo_down:
            withheld.append({"id": f"withheld:borrowing_cause:{after_end}", "status": "insufficient_pattern",
                "text": "차입금 증감은 확인됐지만 운전자본·영업현금흐름의 같은 기간 변화 근거가 충분하지 않아 원인 가설을 만들지 않았습니다."})
    return {"version": "financial-change-analysis-1.0", "changes": changes, "hypotheses": hypotheses,
            "questions": questions, "withheld": withheld,
            "limitation": "증감의 동시 관찰은 원인 입증이 아니다. 차입 주석·현금흐름 주석·내부 용도 및 상환 자료가 있어야 설명을 강화할 수 있다."}


def investigate_hypotheses(hypotheses, search):
    """가설별로 저장 공시를 재탐색한다. 단어 발견은 원인 입증으로 승격하지 않는다."""
    actions=[]
    for hypothesis in hypotheses:
        attempts=[];matches=0
        for action_id,query in FOLLOWUP_QUERIES:
            result=search(query)
            count=sum(item.get('match_count',0) for item in result)
            attempts.append({'id':action_id,'query':query,'match_count':count})
            matches+=count
        actions.append({'id':'investigation:'+hypothesis['id'],'hypothesis_id':hypothesis['id'],
            'status':'stored_disclosure_matches_found' if matches else 'stored_disclosure_matches_not_found',
            'attempts':attempts,'match_count':matches,
            'limitation':'저장 공시에서의 검색 일치만 기록한다. 일치 문구는 차입 증가 원인·용도·상환 계획을 입증하지 않으며, 필요한 내부 자료는 계속 미확인이다.'})
    return actions
