# ML 데이터셋 정책

`peer_dataset.py`의 행은 법인·산업 코드, 기간, 범위, 공개 기준일, 접수번호·원문 해시, 검증 배치, 특성 정책 버전을 보관한다. 특성별 원문·검증 근거도 유지한다.

`supported`와 근거가 모두 있는 값만 특성이다. `withheld`·미검증 값은 0으로 대체하지 않고 결측 사유로 기록한다. 정정 관계 미검증, 기준일 뒤 공시, 서로 다른 해시의 동일 기업·기간·범위는 제외한다.

`model_readiness.py`는 150행, 30개 기업, 3개 연간 기간, 5개 특성, 핵심 특성 85% 가용성, 비교 가능성, 공개 기준일을 평가한다. 미달 상태는 `not_trained_insufficient_data`다. OpenDART 응답은 그 자체로 `verified`가 아니다.

검증: `tests/test_peer_dataset.py`, `tests/test_model_readiness.py`.
