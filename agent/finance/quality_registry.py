"""기존 금융 검증 결과를 설명하기 위한 데이터 품질 규칙 레지스트리.

이 모듈은 검증을 실행하거나 상태를 변경하지 않는다. 검증기의 V 규칙과 계산
결과를 금융 데이터 품질 차원, 우선순위, 후속 처리 정책에 연결한다.
"""
from .validation_acceptance import MANDATORY_CHECKS

REGISTRY_VERSION = "quality-registry-1.1"
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
    "V14": _rule("V14", "사실 주장 근거 연결", "사실 주장마다 지원 근거를 연결하고 미지원 주장은 보류", "traceability", "high", "publication", state="partially_implemented"),
    "V15": _rule("V15", "원인·위험 판단 확정 금지", "원문에 없는 원인·위험 판단을 확정하지 않고 해석 후보와 한계를 표시", "accuracy", "high", "publication", state="partially_implemented"),
    "V16": _rule("V16", "질문 전 공개자료 조회", "질문 전에 공개 자료를 합리적으로 조회하고 실제 제한을 기록", "completeness", "normal", "research", state="partially_implemented"),
    "V17": _rule("V17", "기본 항목 확인 범위", "모든 기본 항목의 확인 범위와 남은 부분을 기록", "completeness", "normal", "publication", state="partially_implemented"),
    "V18": _rule("V18", "결측값 구분", "빈 값과 0을 구분하고 가짜 0 표시를 금지", "completeness", "critical"),
    "V19": _rule("V19", "본문·표·차트·출력 버전 일치", "본문·표·차트·출력의 데이터와 버전을 동일 manifest로 연결", "consistency", "high", "output", state="partially_implemented"),
    "V20": _rule("V20", "후속 영향·이전 버전 보존", "후속 자료의 영향을 계산하고 관련 결과를 재검증하며 이전 게시본을 보존", "traceability", "high", "storage", state="partially_implemented"),
    "V21": _rule("V21", "키·이용권한·외부 전송 경계", "키·권리 제한 자료·외부 전송 경계를 준수하고 허용되지 않은 전송·출력을 차단", "integrity", "high", "runtime", state="partially_implemented"),
    "V22": _rule("V22", "한도 소진의 검증 통과 금지", "한도 도달을 검증 통과로 쓰지 않고 검증된 부분과 남은 내용을 분리 게시", "integrity", "critical", "runtime", state="partially_implemented"),
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
