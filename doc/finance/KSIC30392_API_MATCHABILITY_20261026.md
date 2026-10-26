# 감사보고서와 OpenDART 재무 API 대조 가능성

기준일 2026-10-26. 기존 9건의 감사보고서 원문 후보와 2023-01-01 이후 보관 공시목록을 연결했다. 새 API 요청은 0회다. [대조 가능성 결과](KSIC30392_API_MATCHABILITY_20261026.json)에 입력 해시와 법인별 판정을 저장했다.

OpenDART [단일회사 전체 재무제표 API](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020)는 `corp_code`, 사업연도, `reprt_code`(사업·반기·분기), `fs_div`를 요구한다. [XBRL 원본파일 API](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019019)는 접수번호와 같은 정기보고서 코드 4종을 요구한다. 두 공식 계약에는 감사보고서 전용 코드가 없다.

9개 법인의 보관 공시목록에는 조회 범위의 정기 사업·반기·분기보고서가 모두 0건이다. 따라서 현재 감사보고서 접수번호와 **동일 접수번호**로 공식 재무 API 또는 XBRL을 대조하는 경로가 문서화된 계약에 없다. `fnlttSinglAcntAll`을 임의의 11011 코드로 조회해 다른 접수번호의 자료를 얻더라도 같은 감사보고서의 독립 검증 근거로 삼을 수 없다.

이 판정은 공식 계약과 보관 목록의 **대응 가능성** 감사다. 라이브 API에 9개 감사 접수번호를 넣어 `no_data`를 실측한 결과가 아니며, 감사보고서 내 재무제표가 없다고 단정하지 않는다. 실제 원문 후보 24개는 유지하지만 숫자 검증·코호트 편입은 9곳 모두 보류한다.

재현 명령: `.venv/bin/python sh/audit_30392_api_matchability.py`. 입력 해시와 결과를 재검사하며 새 요청을 하지 않는다.
