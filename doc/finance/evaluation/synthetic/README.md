# 합성 후속 입력 평가

이 폴더의 회사명·수치·답변은 모두 직접 만든 시험 자료입니다. 실제 상신브레이크 또는 실제 고객 자료가 아닙니다.

## 아키텍처 구성도

```mermaid
flowchart LR
    BASE[public_context.json<br/>가상 제조기업 A] --> CASE[S01 · S02 · S03]
    CASE --> INPUT[사용자 답변·정정 입력]
    INPUT --> AGENT[조사·검증 흐름]
    AGENT --> OUTPUT[새 보고서 판·이력]
    EXPECT[followup_cases.json<br/>기대 변화·금지 사항] --> REVIEW[출력 대조]
    OUTPUT --> REVIEW
```

## 워크플로

```mermaid
flowchart TD
    A[초기 가상 문맥 준비] --> B[시나리오 하나 선택]
    B --> C[TXT 입력을 답변·정정으로 전달]
    C --> D[관련 항목 재조사·검증]
    D --> E[새 보고서 판 생성]
    E --> F[expected_changes·must_not·실행 이력 대조]
    F --> G[결과 기록]
    G --> B
```

## 시나리오

| 시나리오 | 입력 | 확인할 동작 |
| --- | --- | --- |
| S01 | [S01_collection.txt](S01_collection.txt) | 실제 회수와 예정 분리, 기존 기준일 잔액 보존 |
| S02 | [S02_loan_purpose.txt](S02_loan_purpose.txt) | 제공 조건으로 표시, 상환 자료 질문 유지 |
| S03 | [S03_conflicting_cfo.txt](S03_conflicting_cfo.txt) | 사용자 설명으로 검증 수치를 덮어쓰지 않음 |

각 시나리오는 같은 초기 문맥에서 독립적으로 시작합니다. 앞 시나리오의 답변을 다음 시나리오에 누적하지 않습니다. 가상 입력이 원문 대조 또는 실제 기업 내부 정보 확인을 뜻하지 않도록 결과에 명확히 표시합니다.
