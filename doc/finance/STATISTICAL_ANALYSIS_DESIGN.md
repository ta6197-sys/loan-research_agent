# 통계 분석 설계

`statistical_analysis.py`는 `supported` 결과만 표준 관측치로 변환한다. 산업, 범위, 기간유형·끝일, 통화, 회계기준, 공개시점이 일치해야 비교집단에 포함된다.

개별 기업은 관측값·평균·중앙값·변화폭만 표시하며 3~5개 값으로 장기 경향·계절성을 확정하지 않는다. 비교·Robust Z-score·Spearman은 최소 10개 기업과 20개 기업-기간 관측치를 요구한다. MAD가 0이거나 표본이 작으면 보류한다. p-value와 인과 해석은 제공하지 않는다.

검증: `tests/test_statistical_analysis.py`.
