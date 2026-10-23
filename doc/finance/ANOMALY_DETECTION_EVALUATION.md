# 이상 관측치 탐지와 평가

`anomaly_detection.py`는 적격성 통과·완결 행만 사용한다. 결측은 대체하지 않고 제외한다. `StandardScaler`와 `IsolationForest`를 `Pipeline`으로 구성하며 `random_state=20261023`, `n_estimators=200`을 고정한다. 최신 기간과 법인 단위 홀드아웃을 별도로 수행하고 전처리는 학습 분리 뒤 fit한다.

점수는 부도확률·신용위험 확률·회계오류 판정이 아니다. `model_evaluation.py`는 라벨 없는 경우 정확도를 만들지 않으며, 합성 오류 평가는 `synthetic_evaluation_only`로 표시한다.

환경: `scikit-learn 1.9.1`, `numpy 2.5.4`, `scipy 1.18.1`.

- [IsolationForest API](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html)
- [Pipeline API](https://scikit-learn.org/stable/modules/generated/sklearn.pipeline.Pipeline.html)
- [NumPy quantile API](https://numpy.org/doc/stable/reference/generated/numpy.quantile.html)

검증: `tests/test_anomaly_detection.py`, `tests/test_model_evaluation.py`.

## 코드 교정 1.1

탐색 임계값은 학습 점수의 95분위수다. 평가 점수의 분위수는 임계값 결정에 사용하지 않는다. 민감도는 동일 평가집단에 학습 기준 90·95·97.5분위수를 적용한 실제 후보 목록과 건수다. 시간/법인 분할 안정성은 공통 평가 관측치에 한정하며 공통 집합이 없으면 unmeasured다. 자세한 검증 결과는 `ANALYTICS_CORRECTION_AND_INTEGRATION_REPORT.md`를 참조한다.
