# 데이터 품질 분석 설계

`quality_analytics.py`는 `quality_registry.py`, `quality_metrics.py`, `quality_exceptions.py`의 읽기 모델을 재사용한다. 규칙 분포의 분모는 실제 실행된 해당 규칙 결과 수이며, 계정·기간 공백은 `numeric_fact` 하나당 한 번만 센다. 같은 값의 V05·V07을 여러 오류 건으로 합산하지 않는다.

`not_checked`는 성공·오류·0으로 전환하지 않는다. 출력은 규칙·계정·기간·범위·원문 매핑·정정 이력·배치 추세·예외 사유를 포함한다. 품질 분석은 검증 상태를 바꾸지 않는다.

검증: `tests/test_quality_analytics.py`.
