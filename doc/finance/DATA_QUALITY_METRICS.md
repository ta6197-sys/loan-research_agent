# 데이터 품질지표 계산식

모든 지표는 게시 보고서가 연결한 검증 배치의 저장 결과로 계산한다. LLM은 계산에 사용하지 않는다.

| 지표 | 분자 | 분모 | 제외·한계 |
|---|---:|---:|---|
| 수치 검증 상태 | `numeric_fact` 상태별 건수 | 해당 유형 전체 | 다른 검증 계층과 합산하지 않는다. |
| 수치 대조 통과율 | V05 `pass` | V05 `pass + fail` | `not_checked`·`not_applicable`은 별도 표시한다. `exact`, `rounding` 일치를 분리한다. |
| 원문 위치 검증 통과율 | V07 `pass` | V07 `pass + fail` | 원문 위치가 사실 전체를 보장하지 않는다. |
| 보고서 수치 항목 확인율 | `financial_rows`의 `verified` | 모든 `financial_rows` | 보류 수를 별도 표시하며 공시 전체 완전성은 계산하지 않는다. |
| 시점 정합성 | V02·V13 상태 | 각각 `pass + fail` | 공개 기준일 적합성과 현재 최신 여부를 구분한다. |

분모가 0이면 비율은 `null`, 상태는 `unmeasured`다. 100%로 대체하지 않는다. `overall_disclosure_coverage`는 수집하지 않은 전체 공시의 분모가 없으므로 `null`이다.
