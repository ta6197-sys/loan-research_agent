# 데이터 품질관리 구현 보고서

## 구현 결과

- 완료: V01~V22 품질 규칙 레지스트리, 결정론적 품질지표, 예외 읽기 모델, SQLite v6 불변 검토 이력, 품질 API 3개, Vue `데이터 품질` 탭, 계정 표준사전
- 부분 구현: 기존 V14~V17·V19의 일부는 현재 보고서·첨부 범위에서만 상태를 표시한다. 새 검증 규칙을 추가하거나 기존 검증 통과 조건을 완화하지 않았다.
- 미구현: 로그인·승인권자 인증, 기관별 중요성 기준, 전사 데이터 계보, 공식 인증과 독립 감사

## 수정 파일

| 파일 | 목적 |
|---|---|
| `agent/finance/quality_registry.py` | 기존 V 규칙의 품질 차원·우선순위·처리 정책 |
| `agent/finance/quality_metrics.py` | 검증 배치·게시본 기반 결정론적 지표 |
| `agent/finance/quality_exceptions.py` | 실패·미검증·보류의 안정적 예외 ID와 읽기 모델 |
| `agent/finance/quality_dictionary.py` | 기존 계정 규칙과 XBRL ID의 설명용 표준사전 |
| `agent/research_schema_v6.sql`, `agent/research_store.py` | 불변 검토 이벤트와 v6 마이그레이션 |
| `agent/research_api.py`, `web_demo_server.py` | 품질 조회·검토 API와 명시적 프록시 허용경로 |
| `static/finance.js`, `static/finance-template.html`, `static/finance-render.js` | 품질 대시보드와 검토 메모 UI |

## 검증 결과

`node sh/build_finance_ui.cjs`를 실행했다.

신규 품질 테스트와 보고서·보안 회귀를 포함한 34건은 통과했다.

` .venv/bin/python -m unittest discover -s tests` 전체 실행은 350건 중 349건이 통과했다. 남은 한 건 `ProcessRestartTests.test_actual_http_process_kill_restart_retains_history`는 샌드박스의 포트 바인딩 제한으로 `PermissionError`가 났다. 같은 테스트를 실제 로컬 환경에서 개별 실행해 통과시켰다.

## 실제 품질지표의 해석

품질 API는 게시 보고서가 저장한 `validation_batch_id`만 읽는다. 이후 검증 배치가 생성돼도 과거 보고서의 지표는 바뀌지 않는다. 현재 구현 보고서의 테스트 사례는 합성 데이터이며 실제 기업 품질 측정 결과로 해석하지 않는다.
