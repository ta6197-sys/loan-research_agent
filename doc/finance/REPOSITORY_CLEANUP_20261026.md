# 기업금융 저장소 정리 기록 (2026-10-26)

## 범위와 판정

- 실행 중인 기업금융 조사, OpenDART 수집, 원문·XBRL 검증, 보고서·PDF·ZIP, 코호트·분석 기능은 유지했다.
- 구 모니터링 실습의 서버·채팅 UI·에이전트·runbook·실행 스크립트는 코드 참조를 제거한 뒤 삭제했다. 금융 MCP 연결은 금융 도구만 허용하도록 축소했다.
- 기존 금융 구현·검증의 역사 기록인 `doc/finance` 문서는 보존했다. 일부 문서가 과거 코드 경로를 기술하는 것은 당시 기록이다.

## 과거 검증 사본 조사

조사 대상은 `data/research/validation_*`, `data/research/evaluation_stage09*`, `data/research/sewon_output_regression_20261026`의 실제 디렉터리다. 정리 전 합계는 약 19,770,860 KiB였다. 코드·테스트·자동화 스크립트와 금융 문서의 정확한 경로 참조를 검사했다. 대용량 파일은 SHA-256으로 중복을 확인했다.

삭제한 경로는 다음 한 파일이다.

| 파일 | 크기 | 근거 |
|---|---:|---|
| `data/research/validation_stage08/20261022T000741Z-40b0be4e/session.db` | 350,101,504바이트 | `validation_stage08/20261021T130748Z-275f3f24/session.db`와 SHA-256 `d8f6f4cfe6ddc36487aa41501bcb1605bb4f5447808d6c9a0c3ce8e92bb81bb8`로 동일. 삭제 위치에는 결과·매니페스트가 없고 코드·문서의 직접 참조가 없다. 해당 위치의 `sources`는 보존. |

확보 용량은 **350,101,504바이트(약 334 MiB)**다. 검사 결과 다른 대용량 DB는 해시가 달라 고유 검증 근거일 수 있고, 일부 결과·스크립트는 특정 검증 폴더를 직접 참조한다. 따라서 19GB 전체의 삭제 조건은 충족하지 않아 보존했다. 특히 `validation_fiscal_*`와 `sewon_output_regression_20261026`은 세원정공 출력 회귀 경로에서 사용한다.

현재 조사 원문, 사례 라이브러리, XBRL 원본, 루트 `session.db`, 코호트 스냅샷, 결과 JSON과 매니페스트는 보존했다. 운영 자료에는 삭제를 적용하지 않았다.

## 회귀 검증

- `.venv/bin/python -m unittest discover -s tests -q`: **421개 통과** (로컬 HTTP 재시작 시험 때문에 루프백 권한으로 실행).
- 삭제 후 `test_report_outputs.py`: **8개 통과** (화면·PDF·ZIP 출력).
- 삭제 후 `test_financial_analytics.py`: **5개 통과**.
- 삭제 후 `test_peer_dataset.py`: **5개 통과**.
- `node sh/test_finance_analytics_ui.cjs`: 통과.
- `git diff --check`: 통과.

실제 OpenDART 재수집이나 로컬 모델 호출은 이 정리 작업에서 실행하지 않았다. 기존 검증 원문과 결과를 다시 생성했다는 뜻은 아니다.
