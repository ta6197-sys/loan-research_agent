# 저장 분석 조회 계약 1.1

## 일반 기업 수집 결과의 결산월 계약

`POST /researches/general/start`는 결산월 확인 전 비교 창을 잠정 계획으로 저장한다. `POST /runs/{run_id}/collect`는 공식 기업개황 `acc_mt`와 사업보고서 월을 대조하고, 응답에 `fiscal_period:{status,reason,month,annual_report_months}`, `target_reports`, `next_phase`를 포함한다. 결산월이 누락·충돌하면 `target_reports=[]`, `financial_requests=0`, `next_phase=fiscal_month_review_required`다. 비12월 결산 수집 결과의 `next_phase=non_calendar_numeric_mapping_pending`은 기존 흐름 식별자다. 수집은 숫자 검증을 자동 실행하지 않는다. 별도 숫자 검증 배치가 통과해도 비12월 보고서 생성은 아직 차단된다. 상세 규칙은 [결산월 수집 정책](FISCAL_PERIOD_COLLECTION_POLICY.md)에 있다.

## GET /reports/{report_id}/financial-analysis

게시된 보고서에 대한 읽기 요청이다. 외부 수집, 모델 호출, 스냅샷 생성·연결을 수행하지 않는다.

- 기존 필드 `financial_metrics`, `analysis_signals`, `ml_readiness`, `anomaly_detection`, `followup_questions`를 유지한다.
- `analysis_plan`: `analysis-plan-1.0` 읽기 결과. 검증된 확인 신호와 계산 보류·미측정 사유를 자료 요청 단계로 묶는다. 각 단계는 `kind`, `status`, `reason_code`, `question`, `requested_information`, `related_metric_ids`, 기간·범위와 원문·검증 ID를 담는다. 공백은 숫자값이나 위험 판정으로 표시하지 않는다.
- `dataset_artifact_id`: 해당 보고서에 연결된 불변 분석 산출물 해시. 없으면 null.
- `statistical_analysis`: 연결된 스냅샷의 코호트 정의, 추세, 분위수 비교, 강건 통계, 관계 분석. 지표마다 표본 요건에 따라 보류한다.
- 스냅샷이 없으면 `insufficient_peer_data`와 `verified_peer_dataset_not_available`을 반환한다.
- `financial_metrics`는 Decimal 값 문자열과 `name`, `unit`, 공통 분석 메타데이터를 포함한다.
- 근거 변조·스냅샷 해시 불일치는 오류로 처리한다. 다른 스냅샷으로 자동 대체하지 않는다.

## POST /researches/{research_id}/refresh

요청 본문은 `{}` 또는 `{"public_cutoff":"YYYY-MM-DD"}`다. 생략 시 한국 시간의 오늘을 사용한다. 기존 공개 기준일보다 이른 날짜와 미래 날짜는 거절한다. 기준일이 전진하면 기존 기준·실행을 수정하지 않고 새 불변 기준 버전과 조회 실행을 만든다. 같은 날짜의 재조회는 기준 버전만 재사용한다. 매번 공식 기업개황·전체 정기공시 목록을 확인한다.

대상은 `general_company` 정기공시 조사다. 기존 `cached_case` 조사는 화면에서 이 엔드포인트를 자동 호출하지 않고 저장된 게시본을 그대로 연다.

- 새·정정 정기공시가 없으면 `status=unchanged`를 반환하고 직전 게시본 또는 검증 자료를 사용한다.
- 새 공시가 있으면 새 실행에서 원문·API·XBRL을 수집하고 별도 숫자 검증과 XBRL 매핑 배치를 실행한다. 수집만으로 게시나 분석 적격을 표시하지 않는다.
- 결산월 변경·불명확성 또는 수집/검증 미완료는 검토 필요 상태로 보류한다.
- 이미 수집·갱신 실행이 대기 또는 진행 중이면 `status=in_progress`와 기존 실행 ID를 반환하고 새 실행·기준 버전을 만들지 않는다. 같은 조사를 여러 화면에서 열어도 확인이 겹치지 않는다.
- 새 공시를 수집한 뒤 검증에 실패하면 해당 실행의 `phase=validation_failed`를 기록한다. 화면은 이 실행을 분석 자료로 선택하지 않고 직전 검증 결과를 보여준다. 오류 자체도 화면에 남긴다.
- 기존 보고서·검증 배치·코호트 스냅샷은 변경하지 않는다.

## GET /validation-batches/{batch_id}/quality-analysis

분포는 요청 배치에 고정한다. `batch_trend`에는 같은 조사의 요청 배치 시점 이전 숫자 검증 배치와 요청 배치를 포함한다. 미검증은 분모에 성공·오류로 섞지 않는다.

## 명시적 관리 명령

프로젝트 루트에서 다음 명령을 사용한다. 외부 호출은 `collect`에만 존재한다. 실행 ID는 기존 일반 기업 조사 접수 기능으로 먼저 생성한다.

```sh
.venv/bin/python sh/manage_analytics_dataset.py register policy.json
.venv/bin/python sh/manage_analytics_dataset.py collect RUN_ID [RUN_ID ...]
.venv/bin/python sh/manage_analytics_dataset.py build COHORT_HASH REPORT_ID [REPORT_ID ...]
.venv/bin/python sh/manage_analytics_dataset.py bind REPORT_ID ARTIFACT_HASH
```

등록 정책 필수 필드: `industry_system`, `industry_version`, `industry_code`, `accounting_standard`, `scope`, `periods`, `public_cutoff`, `policy_version`, `members`.
`periods`는 `{start,end,kind:duration}` 목록이며 `members`는 법인코드, 저장된 `identity_source_id`, `accounting_standard_evidence:{source_id,location}`를 포함한다. 원문 위치는 바이트 범위·해시·검증 플래그를 가지며 생성 시 실제 바이트와 다시 대조된다. 등록만으로 수치·정정·계정 매핑을 검증 완료 처리하지 않는다.
제11차 KSIC 세세분류를 쓰면 `industry_system=KSIC`, `industry_version=11`, 5자리 `industry_code`, `industry_name`을 지정한다. 각 회원은 `industry_evidence:{source_id,location}`에 해당 법인의 DART XBRL `StandardIndustryCode` 원문 위치를 제공해야 한다. 같은 법인의 기준일 이전 최신 XBRL 코드가 등록 코드와 다르거나 근거가 구버전이면 회원을 제외한다. 기업개황의 3자리 업종코드만으로 5자리 코드를 확정하지 않는다.

코호트·스냅샷·보고서 연결은 독립 SQLite 테이블에 추가 전용으로 저장한다. 동일 보고서의 연결을 바꾸지 않는다. 수집은 중복 실행 ID를 제거하고 완료 실행을 다시 호출하지 않으며, 실패 시 기존 실행 예산과 이벤트를 보존한다. 자동 재시도·한도 초기화는 없다.
보고서를 스냅샷에 연결하려면 해당 보고서와 법인에 속한 검증 관측치가 실제로 한 행 이상 있어야 한다. 제외 사유만 있는 빈 스냅샷은 연결하지 않는다.
