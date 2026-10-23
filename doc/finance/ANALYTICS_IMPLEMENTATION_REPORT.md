# 금융 분석 구현 보고

기준일: 2026-10-23.

| 단계 | 기능 | 실제 검증 데이터 | 상태 |
|---|---|---|---|
| 0 | 저장소·표본 감사 | 상신브레이크·SK 2개 조사 | 완료 |
| 1–2 | 결정론적 분석·품질 분석 | 게시 보고서·검증 배치 | 완료 |
| 3 | 기술·강건 통계 | peer 부족 시 보류 | 완료 |
| 4–5 | 데이터셋·IsolationForest | 합성 테스트로 실행 검증 | 실제 학습 차단 |
| 6 | API·프록시·Vue 읽기 화면 | 게시본·배치 읽기 | 완료 |
| 7 | 테스트·문서화 | 아래 명령 결과 | 완료 |

## 수정 범위

분석 코드는 `agent/finance/`의 analytics, dataset, readiness, anomaly, evaluation 모듈에 있다. API·UI는 `agent/research_api.py`, `web_demo_server.py`, `static/finance.*`에 연결했다. 원문·검증 배치·게시 보고서·PDF·ZIP는 변경하지 않는 읽기 모델이다.

## 검증

```sh
.venv/bin/python -m unittest tests.test_financial_analytics tests.test_analysis_signals tests.test_quality_analytics tests.test_statistical_analysis tests.test_peer_dataset tests.test_model_readiness tests.test_anomaly_detection tests.test_model_evaluation tests.test_analytics_api -v
.venv/bin/python -m unittest discover -s tests
```

지정 분석 테스트 28건이 통과했다. 실제 다기업 검증 표본이 없어 통계 비교와 ML 학습은 `insufficient_peer_data` 또는 `not_trained_insufficient_data`다. 합성 결과를 실제 금융 부실·오류 성능으로 사용하지 않는다.
