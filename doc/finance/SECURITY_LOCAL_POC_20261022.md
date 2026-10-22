# 로컬 단일 사용자 PoC 보안 보완 기록 — 2026-10-22

## 범위와 판단

이 작업은 로컬 단일 사용자 PoC의 저장물과 HTTP 응답만 보완했다. 사용자 인증, JWT,
RBAC, 데이터베이스 교체, 네트워크 공개 운영을 추가하지 않았다. 실제 금융 자료의
정확성은 이 보안 시험으로 검증하지 않는다.

## 기존 구현 확인

- 금융 MCP는 `agent/finance/tool_policy.py`의 명시적 도구 허용목록과 Pydantic 입력
  검증을 사용한다. 일반 MCP 호출은 금융 도구를 우회 호출할 수 없다.
- 조사 저장은 비밀정보로 보이는 필드·인증 URL·환경변수의 알려진 비밀값을 저장 전에
  거부한다. 첨부 파일도 알려진 환경 비밀값을 거부한다.
- 조사 모드의 외부 모델은 기본으로 선택되지 않으며, 원문 전송은 정책상 명시 허용이
  있어야 한다. 첨부 파서는 별도 프로세스에서 외부 추적을 끈 환경으로 실행한다.
- 기존 로컬 요청 경계는 localhost만 허용하고, 외부 Origin 및 교차 사이트 쓰기를
  거부한다.

## 이번 구현

- `session.db` 및 SQLite 부속 파일(WAL/SHM/journal/작업 잠금)은 `0600`으로, 조사
  원문과 첨부 루트 및 하위 디렉터리는 `0700`, 일반 파일은 `0600`으로 맞춘다.
  저장소를 다시 열 때 기존 저장물도 같은 규칙으로 다시 정리한다.
- 원문·첨부 생성 시에도 위 권한을 적용한다. 심볼릭 링크는 따라가거나 권한을 바꾸지
  않으며, 기존 경로 검증과 복구 절차가 근거 저장·자동 복구를 보류한다.
- 모든 FastAPI 응답(초기 403 거부 포함)에 `Cache-Control: no-store`, `X-Content-Type-
  Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, 제한된
  `Permissions-Policy`를 붙인다. 로컬 HTTP PoC라 HSTS와 전역 CSP는 넣지 않았다.

## 검증 기록

변경 전 핵심 보안·저장·첨부 시험:

```text
.venv/bin/python -m unittest tests.test_finance_security tests.test_research_storage tests.test_attachments
Ran 30 tests — OK
```

변경 후 확장 보안 시험:

```text
.venv/bin/python -m unittest tests.test_finance_security tests.test_research_storage tests.test_attachments tests.test_local_finance_model
Ran 37 tests — OK
```

변경 후 전체 회귀 시험:

```text
.venv/bin/python -m unittest discover -s tests
Ran 316 tests — OK
```

실제 로컬 저장소를 재개방해 복구 상태가 비어 있음을 확인했고, `session.db`는 `0600`,
`data/research`는 `0700`, 표본 원문·검증 산출물은 `0600`으로 확인했다. 시험에서
사용한 금융 값은 합성 자료이며, 실제 금융 공시의 정확성 확인 결과가 아니다.

## 운영에서 아직 하지 않은 것

- 다중 사용자 인증·권한 분리, 운영자 감사 로그의 외부 보존
- 저장 데이터 암호화, 키 관리, 백업 암호화·복구 훈련
- HTTPS/HSTS, 공개 도메인·프록시 배포 구성, 네트워크 방화벽
- 악성코드 검사, 완전한 DLP, OS 관리자·백업 계정으로부터의 보호
- 라이브러리 취약점 상시 감시와 배포 환경의 침해 대응

이 항목들은 로컬 단일 사용자 PoC의 이번 범위를 넘는다. 외부 공개나 다중 사용자
운영 전에는 별도의 운영 보안 설계와 검토가 필요하다.
