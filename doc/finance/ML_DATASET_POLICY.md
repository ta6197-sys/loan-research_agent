# ML 데이터셋 정책

`peer_dataset.py`의 행은 법인·산업 코드, 기간, 범위, 공개 기준일, 접수번호·원문 해시, 검증 배치, 특성 정책 버전을 보관한다. 특성별 원문·검증 근거도 유지한다.

`supported`와 근거가 모두 있는 값만 특성이다. `withheld`·미검증 값은 0으로 대체하지 않고 결측 사유로 기록한다. 정정 관계 미검증, 기준일 뒤 공시, 서로 다른 해시의 동일 기업·기간·범위는 제외한다.

`model_readiness.py`는 150행, 30개 기업, 3개 연간 기간, 5개 특성, 핵심 특성 85% 가용성, 비교 가능성, 공개 기준일을 평가한다. 미달 상태는 `not_trained_insufficient_data`다. OpenDART 응답은 그 자체로 `verified`가 아니다.

검증: `tests/test_peer_dataset.py`, `tests/test_model_readiness.py`.

2026-10-26 후속: 새 실제 공개자료 행에는 원문/API 수치, 실제 기간, 기업개황 결산월, 독립 XBRL 계정·기간·금액 대조의 네 통과 상태를 모두 요구한다. 누락된 게이트는 `four_source_verification_gates_not_passed`로 제외한다. 합성 시험 행은 `synthetic_test_only`로 따로 표시하며 실제 자료와 섞이면 모델 적격성에서 보류한다. IsolationForest는 비지도 모델이므로 정답 라벨 품질은 해당 없음으로 표시하고 운영 정확도를 계산하지 않는다. 새 스냅샷 manifest와 격리 출력 검증은 [후속 게이트 기록](KSIC30392_NEXT_STAGE_GATES_20261026.md)을 참조한다.
