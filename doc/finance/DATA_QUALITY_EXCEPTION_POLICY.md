# 데이터 품질 예외관리 정책

예외는 실패 또는 미검증 검사의 읽기 모델이다. 예외 생성은 원문·검증 배치·보고서를 수정하지 않는다.

- `critical`: 필수 V01·V02·V03·V04·V05·V06·V07·V13·V18 검사가 실패한 경우
- `high`: 원문 위치, 계정 매핑, 기간·범위, 차입 구성, 정정 이력의 보류
- `normal`: 그 밖의 검토 공백과 보고서 수치 보류

허용 검토 조치는 `acknowledged`, `investigating`, `evidence_requested`, `note_added`, `closed_unresolved`다. 검토자는 로컬 PoC에서 `local_demo_operator`로만 기록한다. 메모나 버튼 조작은 검증 상태를 `pass`로 바꾸지 않으며, 새 원문 근거를 기존 금융 검증 엔진이 통과시킬 때만 수치 사용 상태가 변경될 수 있다.

검토 이벤트는 추가만 가능하며 UPDATE·DELETE 트리거로 차단한다.
