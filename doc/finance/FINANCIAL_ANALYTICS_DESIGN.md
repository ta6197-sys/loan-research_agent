# 결정론적 재무분석 설계

`financial_analytics.py`는 게시 당시의 검증 배치와 원문 해시를 읽는다. `verified` 행, 필수 V 규칙, 원문 위치, 기간 비교 V11을 모두 통과한 값만 수익성·안정성·현금흐름·운전자금·차입 변화 계산에 쓴다.

각 결과는 `supported`, `withheld`, `unmeasured` 중 하나이며 값, 기간, 범위, 계산식, 사실·검증·원문 ID를 포함한다. 분모 0·부적절 음수는 보류하고, 차입 합계는 V12와 개별 V11 모두 필요하다. `analysis_signals.py`의 결과는 추가 조사 후보일 뿐 신용위험·회계오류 판정이 아니다.

검증: `tests/test_financial_analytics.py`, `tests/test_analysis_signals.py`.
