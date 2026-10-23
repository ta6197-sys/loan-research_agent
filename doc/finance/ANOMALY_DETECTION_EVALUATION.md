# 이상 관측치 탐지와 평가

`anomaly_detection.py`는 적격성 통과·완결 행만 사용한다. 결측은 대체하지 않고 제외한다. `StandardScaler`와 `IsolationForest`를 `Pipeline`으로 구성하며 `random_state=20261023`, `n_estimators=200`을 고정한다. 최신 기간과 법인 단위 홀드아웃을 별도로 수행하고 전처리는 학습 분리 뒤 fit한다.

점수는 부도확률·신용위험 확률·회계오류 판정이 아니다. `model_evaluation.py`는 라벨 없는 경우 정확도를 만들지 않으며, 합성 오류 평가는 `synthetic_evaluation_only`로 표시한다.

환경: `scikit-learn 1.9.1`, `numpy 2.5.4`, `scipy 1.18.1`.

- [IsolationForest API](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html)
- [Pipeline API](https://scikit-learn.org/stable/modules/generated/sklearn.pipeline.Pipeline.html)
- [NumPy quantile API](https://numpy.org/doc/stable/reference/generated/numpy.quantile.html)

검증: `tests/test_anomaly_detection.py`, `tests/test_model_evaluation.py`.
