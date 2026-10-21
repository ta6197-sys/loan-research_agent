"""금융 MCP 호출의 고정 허용목록. 문서·모델 출력이 도구 권한을 추가하지 못한다."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ToolInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class RunInput(ToolInput):
    run_id: str = Field(min_length=1)


class CompanyInput(RunInput):
    corp_code: str = Field(pattern=r'^\d{8}$')


class SearchInput(ToolInput):
    query: str = Field(min_length=1, max_length=200)


class DisclosuresInput(CompanyInput):
    begin: str
    end: str
    page_no: int = Field(default=1, ge=1)
    page_count: int = Field(default=100, ge=1, le=100)
    last_report: Literal['N', 'Y'] = 'N'


class FinancialsInput(CompanyInput):
    year: int = Field(ge=2015)
    report_code: Literal['11011', '11012', '11013', '11014']
    scope: Literal['OFS', 'CFS']


class DocumentInput(RunInput):
    receipt: str = Field(pattern=r'^\d{14}$')


class SourceInput(ToolInput):
    source_id: str = Field(min_length=1)


class SourceSearchInput(SourceInput):
    query: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=10, ge=1, le=50)


class FragmentInput(ToolInput):
    fragment_id: str = Field(min_length=1)


class XbrlInput(DocumentInput):
    report_code: Literal['11011', '11012', '11013', '11014']


class TaxonomyInput(RunInput):
    statement: str = Field(pattern=r'^(?:BS[1-4]|IS[1-4]|CIS[1-4]|DCIS[1-8]|CF[1-4]|SCE[12])$')


FINANCE_TOOL_INPUTS = {
    'search_companies': SearchInput,
    'dart_company': CompanyInput,
    'dart_disclosures': DisclosuresInput,
    'dart_financials': FinancialsInput,
    'dart_document': DocumentInput,
    'extract_source': SourceInput,
    'search_source': SourceSearchInput,
    'read_fragment': FragmentInput,
    'validate_numbers': RunInput,
    'dart_xbrl': XbrlInput,
    'dart_taxonomy': TaxonomyInput,
    'validate_account_mapping': RunInput,
}


def checked_finance_arguments(name, arguments):
    """허용 도구/필드/형식만 전달한다. 실패 시 입력·비밀값을 반환하지 않는다.

    조사 소속·회사·기준일·잔여 예산은 기존 서비스에서 추가 검사한다.
    이 목록은 OS 샌드박스나 다중 사용자 인가를 대신하지 않는다.
    """
    if not isinstance(name, str) or name not in FINANCE_TOOL_INPUTS:
        raise ValueError('tool_not_allowed')
    if not isinstance(arguments, dict):
        raise ValueError('invalid_tool_arguments')
    try:
        return FINANCE_TOOL_INPUTS[name].model_validate(arguments).model_dump()
    except ValidationError:
        raise ValueError('invalid_tool_arguments') from None
