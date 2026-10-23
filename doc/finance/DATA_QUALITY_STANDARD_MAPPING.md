# 데이터 품질 기준 대응표

이 문서는 PoC의 구현 근거를 기록한다. BCBS 239 또는 국내 금융기관 인증 준수를 주장하지 않는다.

| 기관·문서 | 적용 원칙 | 구현 | 테스트 | 상태 |
|---|---|---|---|---|
| BCBS 239 Principle 3, §36.28·§36.31 | 일관된 규칙 설명과 정확성 측정 | `quality_registry.py`, `quality_metrics.py` | `test_quality_registry.py`, `test_quality_metrics.py` | implemented_and_tested |
| BCBS 239 Principle 4, §36.34 | 완전성 공백과 예외 식별 | `quality_exceptions.py`, 보고서 보류 행 | `test_quality_exceptions.py` | implemented_and_tested |
| BCBS 239 Principle 5 | 공개 기준일·정정 이력 상태 분리 | V02·V13 지표 | `test_quality_metrics.py` | implemented_and_tested |
| BCBS 239 Principle 7, §36.43 | 규칙 목록·대조·예외 절차 | 레지스트리, 품질 API, 불변 검토 이벤트 | `test_quality_api.py`, `test_quality_migration.py` | implemented_and_tested |
| 금융보안원 금융 AI 안전성·신뢰성 평가 프레임워크 | 정확성·완전성·정합성·외부 데이터 검증 | 기존 V01~V18 결과를 품질 차원으로 투영 | 기존 수치 검증·신규 품질 테스트 | partially_implemented |
| 금융위원회 금융분야 AI 가이드라인 | 검증 가능성·AI 보조수단성 | 지표는 결정론적 계산, 검토 메모가 검증 상태를 바꾸지 않음 | `test_quality_api.py` | implemented_and_tested |

현재 범위 밖: 실제 금융기관 중요성 평가, 조직별 책임자 인증, 전사 데이터 계보, 독립 감사 및 공식 기준 전체 준수 평가는 구현하지 않았다.

## 원본 설계와 레지스트리 1.1 대응

기준: `design/VALIDATION_AND_EVALUATION.md` 규칙 표. V14–V22의 메타데이터를 정정했다.

| 규칙 | 원본 의미 | 현재 구현 근거 |
|---|---|---|
| V14 | 사실마다 지원 근거 | checked_draft, 게시 수치·근거 연결 |
| V15 | 미지원 원인·위험 확정 금지 | 모델 출력 검사·해석 한계 |
| V16 | 질문 전 합리적 공개 조회 | 공시 수집·추가 조사; 전체 합리성 판단은 부분 구현 |
| V17 | 모든 기본 항목 범위·잔여 기록 | 보고서 sections·unknowns |
| V18 | 빈 값과 0 구분 | 필수 숫자 검증 |
| V19 | 본문·표·차트·출력 버전 일치 | report_view·ReportExports 해시 검증 |
| V20 | 후속 영향·이전 판 보존 | 후속 조사·불변 게시본 |
| V21 | 키·권리·외부 전송 경계 | 로컬 보안·외부 모델 이용 제한 |
| V22 | 한도 소진의 검증 통과 금지 | limited 결과와 검증 상태 분리 |

V14–V17·V19–V22는 제품 경로에 부분 구현되어 있으나 독립 V 규칙의 실행 완료를 뜻하지 않는다. 레지스트리 조회는 기존 검증 배치의 상태·해시를 변경하지 않는다.
