"""기존 금융 검증 결과를 설명하기 위한 데이터 품질 규칙 레지스트리.

이 모듈은 검증을 실행하거나 상태를 변경하지 않는다. 검증기의 V 규칙과 계산
결과를 금융 데이터 품질 차원, 우선순위, 후속 처리 정책에 연결한다.
"""
from .validation_acceptance import MANDATORY_CHECKS

REGISTRY_VERSION = "quality-registry-1.0"
DIMENSIONS = {"accuracy", "completeness", "consistency", "integrity", "timeliness", "traceability"}


def _rule(rule_id, name, description, dimension, severity="normal", layer="numeric_validation",
          scope="financial_disclosure", state="implemented", on_fail="withhold_and_review",
          on_not_checked="record_gap", reference="BCBS239 P3/P7"):
    return {
        "rule_id": rule_id, "rule_name": name, "description": description,
        "quality_dimension": dimension, "severity": severity, "validation_layer": layer,
        "applicable_scope": scope, "implementation_state": state,
        "on_fail": on_fail, "on_not_checked": on_not_checked,
        "reference_standard": reference, "registry_version": REGISTRY_VERSION,
    }


# V01~V22는 문서의 번호를 유지한다. 실제 배치에 없는 규칙은 design_only로
# 표시하며, 실행됐다고 가정하지 않는다.
_RULES = {
    "V01": _rule("V01", "법인 식별 대조", "원문·API의 법인코드가 조사 법인과 일치하는지 확인", "integrity", "critical"),
    "V02": _rule("V02", "공개 기준일 대조", "접수일과 공개일이 기준일 이내인지 확인", "timeliness", "critical"),
    "V03": _rule("V03", "별도·연결 범위 대조", "원문 표와 API 자료의 재무제표 범위를 확인", "consistency", "critical"),
    "V04": _rule("V04", "보고기간·API 열 대조", "보고기간과 DART API 금액 열을 대조", "consistency", "high"),
    "V05": _rule("V05", "수치 대조", "원문 셀의 단위·부호·금액을 API 값과 대조", "accuracy", "critical"),
    "V06": _rule("V06", "계정 매핑 대조", "표시 계정명·공식 계정 ID·계층을 확인", "integrity", "high"),
    "V07": _rule("V07", "원문 위치 대조", "원문 셀·헤더·기간·단위의 바이트 위치와 해시를 확인", "traceability", "high"),
    "V08": _rule("V08", "재무상태표 등식", "자산과 부채·자본의 산술 관계를 확인", "consistency", "high", "calculation"),
    "V09": _rule("V09", "현금 기말 대조", "현금흐름표 기말 현금과 재무상태표 현금을 대조", "consistency", "high", "calculation"),
    "V10": _rule("V10", "현금흐름 롤포워드", "기초 현금과 활동별 흐름의 산술 관계를 확인", "consistency", "high", "calculation"),
    "V11": _rule("V11", "기간 비교", "동일 범위·통화·기간의 증감 계산 조건을 확인", "consistency", "normal", "calculation"),
    "V12": _rule("V12", "차입금 구성", "차입금 본표·주석 구성과 리스·보증 제한을 확인", "completeness", "high", "composition_review"),
    "V13": _rule("V13", "정정·중복 이력", "같은 공시 쌍과 정정 이력을 확인", "timeliness", "high"),
    "V14": _rule("V14", "보고서 표시 검토", "게시 단계 검토 규칙", "traceability", "normal", state="design_only"),
    "V15": _rule("V15", "해석 근거 검토", "해석과 근거 연결 검토 규칙", "traceability", "normal", state="design_only"),
    "V16": _rule("V16", "사용자 입력 분리", "사용자 입력과 공시 근거 분리 규칙", "integrity", "normal", state="partially_implemented"),
    "V17": _rule("V17", "보고서 완결성", "게시 단계의 완결성 검토 규칙", "completeness", "normal", state="design_only"),
    "V18": _rule("V18", "결측값 구분", "결측값을 0으로 취급하지 않는지 확인", "completeness", "critical"),
    "V19": _rule("V19", "외부 입력 검토", "외부 자료의 검증 범위 표시 규칙", "integrity", "normal", state="partially_implemented"),
    "V20": _rule("V20", "불변 이력", "원문·검증·보고 버전의 불변 저장 규칙", "traceability", "high", "storage"),
    "V21": _rule("V21", "모델 경계", "모델이 검증 상태를 승격하지 못하도록 하는 규칙", "integrity", "high", "runtime"),
    "V22": _rule("V22", "강제 통과 금지", "입력이나 모델이 검증 통과를 강제하지 못하도록 하는 규칙", "integrity", "critical", "runtime"),
}


def get(rule_id):
    """알 수 없는 실행 규칙도 미정의로 투명하게 반환한다."""
    base = _RULES.get(rule_id)
    if base:
        return dict(base)
    prefix = rule_id.split("_", 1)[0]
    if prefix in _RULES:
        item = dict(_RULES[prefix])
        item.update(rule_id=rule_id, rule_name=_RULES[prefix]["rule_name"] + " 세부 검사")
        return item
    return _rule(rule_id, "미등록 검증 규칙", "레지스트리에 설명이 아직 등록되지 않은 실행 결과", "traceability",
                 "normal", state="partially_implemented", on_fail="review", on_not_checked="record_gap",
                 reference="local_policy")


def all_rules():
    return [dict(_RULES[key]) for key in sorted(_RULES)]


def is_mandatory(rule_id):
    return rule_id in MANDATORY_CHECKS
