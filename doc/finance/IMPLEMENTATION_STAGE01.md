# 0~1단계 저장 기반 사용 안내

설계 기준 1.0 · 2026-10-20 (한국시간)

이 문서는 0~1단계 완료 당시의 안내다. 이후 2단계에서 기본 서버에 금융 전용 MCP와 회사 검색을 연결했다. 현재 동작은 [2단계 안내·검증 기록](IMPLEMENTATION_STAGE02.md)을 함께 확인한다. 자동 조사·보고서 생성은 여전히 연결 전이다.

현재는 조사 기록을 저장하고 다시 읽는 기반이다. 자동 기업 조사·금융 검증·보고서 게시 기능은 아직 연결하지 않았다. `queued`는 실행 기록의 대기 상태이며 지금 백그라운드 조사 작업이 수행된다는 뜻이 아니다. 이는 단계별 구현 상태이며 최종 설계의 자동 조사 흐름을 변경한 것이 아니다.

## 실행

프로젝트 폴더에서:

```bash
bash sh/run_research.sh
```

서버: `http://127.0.0.1:8100`. API 목록: `http://127.0.0.1:8100/docs`.

종료는 실행한 터미널에서 Ctrl+C다. DB는 지우지 않는다. 포트가 이미 사용 중이면 기존 프로세스를 강제 종료하지 않고 시작 실패를 표시한다.

처음 환경을 다시 구성할 때는 `python3 -m venv .venv`, `.venv/bin/python -m pip install -r requirements.lock.txt`를 사용한다. 이 잠금 파일은 이번 Python 3.14.7/macOS 환경의 실제 설치 버전이다. 기존 `requirements.txt`의 허용 범위는 보존했다. 모든 실행·설치·검증에 프로젝트 `.venv/bin/python`을 사용한다.

## 기본 모드를 저장 기능으로 둔 이유

기존 채팅은 장애 진단용 모델·MCP·그래프를 함께 준비한다. 조사 저장 서버의 시작이 이 실습 구성에 의존하면, 저장 상태 확인에 관계없는 모델/도구 문제까지 함께 발생한다. 기본 저장 모드는 그 의존 없이 DB를 확인하고 이후 금융 도구·그래프를 연결할 기반이다. 기존 채팅 코드는 학습 모드로 보존했다.

학습 기능만 별도로 확인하려면:

```bash
APP_MODE=legacy LLM_BACKEND=fake .venv/bin/python server.py
```

`fake`는 학습용 가짜 모델이다. 금융 자료를 조사하거나 정확성을 평가하는 모드가 아니다. 금융 조사 모델은 아직 선정하지 않았다.

## 저장 위치와 이력

| 저장 대상 | 위치/방식 |
| --- | --- |
| 대화 | 최상위 `session.db`의 기존 `messages` 테이블 |
| 조사·기준·실행·스냅샷·근거·보고서 후보·이벤트 | 같은 DB의 `research_` 테이블 |
| 원문 | `data/research/{조사ID}/{official 또는 provided}/{원문ID}/{해시}.bin` |
| 패키지 환경 | `.venv/`와 `requirements.lock.txt` |

DB는 디스크 파일이며 RAM 메모리가 아니다. 파일 하나 안에서 대화와 조사는 서로 다른 테이블에 저장한다. 조사 상태를 대화에서 추측해 복원하지 않는다. 원문 파일은 원본 바이트를 보존하며 DB의 해시·위치와 연결한다.

기준과 보고서 후보는 새 버전을 추가한다. 스냅샷은 단계·입력·계획·다음 작업·사용량 등을 직접 기록하는 구조다. 현재는 저장·조회 계약을 제공하며 실제 그래프에서의 저장 시점과 자동 재개는 이후 연결한다.

## 현재 HTTP API

| 동작 | 경로 |
| --- | --- |
| 상태 | `GET /health` |
| 조사 생성 | `POST /researches` |
| 조사 목록/상세 | `GET /researches`, `GET /researches/{id}` |
| 새 기준 저장 | `POST /researches/{id}/bases?expected_version=1` |
| 실행·저장 스냅샷 | `GET /runs/{id}` |
| 저장 이벤트 | `GET /researches/{id}/events?after=0` |
| 보고서 후보 목록/버전 | `GET /researches/{id}/reports`, `GET /reports/{id}` |
| 수치→위치→원문 근거 | `GET /evidence/{id}` |
| 원문 메타데이터/바이트 | `GET /sources/{id}`, `GET /sources/{id}/view` |

생성 요청 예시는 `/docs`의 `POST /researches`에서 다음과 같이 입력할 수 있다. 비교 창은 명시적으로 저장하며 이 요청만으로 실제 자료를 조회하지 않는다.

```json
{
  "request_key": "my-first-research-1",
  "company": {"name": "상신브레이크", "corp_code": "00129642", "stock_code": "041650"},
  "purpose": "공개 자료를 활용한 가상 운전자금 신규 대출 사전조사",
  "public_cutoff": "2026-10-20",
  "comparison_window": {
    "annual_years": [2023, 2024, 2025],
    "interim_periods": ["2025-H1", "2026-H1"],
    "flow_comparison": "same_period",
    "balance_comparison": "previous_year_end"
  }
}
```

같은 요청 키와 같은 내용으로 재전송하면 중복 생성하지 않는다. 키는 같고 내용이 다르면 409다. 새 기준은 기대 버전이 현재와 다르면 409를 반환한다. 회사 변경은 새 조사 요청으로 처리하며 기준 변경 API에는 회사 변경 필드가 없다.

현재의 원문·구간·수치·스냅샷·보고서 후보 쓰기는 `ResearchStore`의 내부 메서드다. 파서/금융 도구/그래프가 나중에 이 계약을 사용한다. 임의 파일 경로를 받는 API나 사용자 업로드 API는 제공하지 않는다. 수치 후보와 보고서 후보를 실제 검증·게시 완료로 바꾸는 API도 없다.

## 내부 저장 연결

`agent/research_models.py`의 계약을 사용한다.

1. `create_research(ResearchInput)` → 조사·기준·초기 실행 ID.
2. `save_source(run_id, SourceInput, bytes)` → 원문 버전·해시.
3. `save_fragment(source_id, FragmentInput)` → 원문 내 위치·문맥·경고.
4. `save_fact(run_id, fragment_id, FactInput)` → 정밀 수치 문자열 또는 결측 이유. 상태는 `candidate`.
5. `save_snapshot(run_id, expected_revision=..., stage=..., state=..., usage=Usage(...))` → 새 저장 지점. 이전 사용량 감소 금지.
6. `save_report(run_id, ReportInput)` → 새 보고서 후보 버전. 상태는 `building`. 한계와 근거 ID를 저장하며 아직 확인된 사실을 추가할 수 없다.

숫자는 `"9007199254740993.123456789"`처럼 문자열로 전달한다. 표시용 반올림 값이나 float로 바꾸지 않는다. 원문 문자열과 기간·범위·단위·통화·계정도 함께 저장한다. 저장 형식 검사와 금융 의미 검증은 구분한다.

## 자료·설정 보호

`.env`는 그대로 보존했다. `APP_ALLOW_EXTERNAL_MODEL`, `APP_ALLOW_EXTERNAL_TRACE`는 기본 false이며 키가 있다는 이유로 활성화하지 않는다. 저장 모드는 모델·MCP를 시작하지 않고 추적도 끈다. 향후 자료별 전송 허용 여부까지 확인해야 하며 전역 설정만으로 제한 자료 전송이 허용되지 않는다.

메타데이터의 인증 필드·인증 URL, 설정된 비밀값·원시 프롬프트는 저장을 거부한다. 알려지지 않은 비밀값 전체를 탐지한다고 보장하지 않는다. 입력/원문에 API 키를 넣지 않는다.

NCS 자료는 자동으로 읽거나 적재하지 않는다. 원문·제3자 도표·서식의 이용 제한은 [기존 원칙](NCS_REVIEW.md)을 유지한다. 코드가 출처를 기록했다는 이유만으로 복제·전송 권리가 생기지 않는다.

## 재현 검증과 한계

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip check
```

16개 시험은 합성 데이터와 임시 DB를 사용한다. 실제 서버 프로세스의 강제 종료/재시작 후 HTTP 조회 비교가 포함된다. 저장 상태의 복원 확인이며 **실제 조사 자동 재개 시험은 아니다**. 원문/수치의 금융적 정확성, 실제 모델, DART 재조회, PDF·차트·사용성은 검증하지 않았다.

DB와 원문 파일은 함께 보관해야 한다. 원문 파일만 삭제하면 해시/파일 검사를 통과하지 못한다. 실행 중 DB/파일을 별도로 복사하는 백업 기능과 조사 삭제 수명주기는 아직 구현하지 않았다.

자세한 결과와 다음 단계는 [진행 기록](IMPLEMENTATION_LOG.md)에 있다.
