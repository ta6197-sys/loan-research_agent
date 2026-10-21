"""금융 전용 stdio MCP. 시작 시 네트워크/모델 호출 없이 로컬 저장소만 연결한다."""
import logging
import os

from fastmcp import FastMCP

from agent.finance.dart_service import DartService
from agent.research_store import ResearchStore
from agent.session_store import DB_PATH
from agent.finance.numeric_validation import NumericValidationService
from agent.finance.extraction_service import ExtractionService
from agent.finance.document_extractor import ExtractionFailure
import asyncio

# 네트워크 라이브러리의 상세 디버그 로그는 금융 프로세스에서 사용하지 않는다.
logging.getLogger('httpcore').setLevel(logging.WARNING)
service = DartService(ResearchStore(
    os.environ.get('APP_DB_PATH', str(DB_PATH)), os.environ.get('APP_SOURCE_ROOT') or None,
), os.environ.get('OPENDART_API_KEY', ''))
mcp = FastMCP(name='corporate-loan-research-finance')
extraction = ExtractionService(service.store)


@mcp.tool()
async def validate_numbers(run_id: str) -> dict:
    """저장 자료만으로 수치 대조·계산 이력 생성. 미실행/차입 구성/정정 한계는 보존한다."""
    try:
        result=await asyncio.to_thread(NumericValidationService(service.store).validate_run,run_id)
        return {'status':'ok','batch_id':result['id'],'reused':result['reused'],**result['summary']}
    except (ValueError,KeyError):
        return {'status':'validation_failed','reason':'stored_data_not_validated'}


@mcp.tool()
async def extract_source(source_id: str) -> dict:
    """저장된 DART 원문의 표·셀·API 레코드 후보를 로컬 추출. 경고/누락을 표시한다."""
    try:
        return await asyncio.to_thread(extraction.extract_source,source_id)
    except ExtractionFailure as error:
        return {'status':'extraction_failed','reason':str(error),'financial_verification':'not_performed'}
    except (ValueError,KeyError):
        return {'status':'extraction_failed','reason':'invalid_source','financial_verification':'not_performed'}


@mcp.tool()
async def search_source(source_id: str, query: str, limit: int = 10) -> dict:
    """저장한 인덱스에서 키워드 검색. 결과 없음은 원문 전체의 내용 부재와 다르다."""
    try:
        return await asyncio.to_thread(extraction.search,source_id,query,limit)
    except ValueError:
        return {'status':'search_failed','financial_verification':'not_performed'}


@mcp.tool()
async def read_fragment(fragment_id: str) -> dict:
    """선택한 표/구간의 행·열·머리글·단위·위치·추출 경고를 반환한다."""
    return await asyncio.to_thread(extraction.fragment,fragment_id)


@mcp.tool()
async def search_companies(query: str) -> dict:
    """공식 고유번호 목록에서 후보 검색. 최초 검색 때 갱신하고 실패 시 이전 목록임을 표시."""
    return await service.call('search_companies', {'query':query})


@mcp.tool()
async def dart_company(run_id: str, corp_code: str) -> dict:
    """선택 법인의 기업개황 확보. 제조업 지원 판정·재무 검증과 구별한다."""
    return await service.call('company', {'run_id':run_id,'corp_code':corp_code})


@mcp.tool()
async def dart_disclosures(run_id: str, corp_code: str, begin: str, end: str,
                           page_no: int = 1, page_count: int = 100, last_report: str = 'N') -> dict:
    """공개 기준일 안의 공시 이력 한 페이지. 전체 페이지 여부와 원본 저장 참조 반환."""
    return await service.call('disclosures',dict(run_id=run_id,corp_code=corp_code,begin=begin,end=end,
                                              page_no=page_no,page_count=page_count,last_report=last_report))


@mcp.tool()
async def dart_financials(run_id: str, corp_code: str, year: int, report_code: str, scope: str) -> dict:
    """전체 재무제표 API 원본 저장. OFS/CFS를 구분하고 기준일 뒤 접수본은 사용 불가 표시."""
    return await service.call('financials',dict(run_id=run_id,corp_code=corp_code,year=year,
                                             report_code=report_code,scope=scope))


@mcp.tool()
async def dart_document(run_id: str, receipt: str) -> dict:
    """같은 조사에서 확보한 접수번호의 공시 ZIP·원문 저장. 원문 본문은 도구 결과에 넣지 않음."""
    return await service.call('document',dict(run_id=run_id,receipt=receipt))


if __name__ == '__main__':
    mcp.run(transport='stdio',show_banner=False)
