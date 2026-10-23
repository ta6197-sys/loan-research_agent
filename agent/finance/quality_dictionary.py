"""지원 재무 계정의 설명용 표준사전. 기존 매핑 동작을 변경하지 않는다."""
from .extraction_service import ACCOUNT_RULES, SUPPORT_RULES
from .numeric_validation import ACCOUNT_IDS

DICTIONARY_VERSION = "quality-dictionary-1.0"


def entries():
    result = []
    for account, (statement, aliases) in {**ACCOUNT_RULES, **SUPPORT_RULES}.items():
        result.append({
            "internal_account": account,
            "korean_name": sorted(aliases)[0],
            "statement_type": statement,
            "official_account_ids": sorted(ACCOUNT_IDS.get(account, set())),
            "aliases": sorted(aliases),
            "expected_period_kind": "duration" if statement in {"CIS", "CF"} else "instant",
            "allowed_scopes": ["OFS", "CFS"],
            "mapping_policy_version": DICTIONARY_VERSION,
            "limitations": ["이 사전은 기존 후보 규칙과 공식 계정 ID를 설명합니다.",
                            "회사 고유 계정명은 이름만으로 동일 경제적 개념으로 확정하지 않습니다."],
        })
    return sorted(result, key=lambda item: item["internal_account"])


def get(internal_account):
    return next((item for item in entries() if item["internal_account"] == internal_account), None)
