"""일반 기업 조사의 식별·기준·수집 계획 접수.

보관 사례 자료를 재사용하지 않으며, 실제 OpenDART 수집기는 이 계획을 읽어 별도 단계에서
실행한다. 법인 목록 검색 결과를 선택한 뒤에도 기업개황 응답으로 다시 식별해야 한다.
"""
from datetime import date

from pydantic import Field, field_validator

from agent.research_models import BasisInput, Budget, Company, Contract, ResearchInput


def disclosures_begin(cutoff):
    return f'{cutoff.year-3:04d}-01-01'


def comparison_window(cutoff):
    latest_half_year=cutoff.year if cutoff.month>=8 else cutoff.year-1
    return {"annual_years":list(range(cutoff.year-3,cutoff.year)),
            "interim_periods":[f"{latest_half_year-1}-H1",f"{latest_half_year}-H1"],
            "fiscal_month_status":"pending_company_identity"}


class GeneralStartInput(Contract):
    company_name: str = Field(min_length=1, max_length=200)
    corp_code: str = Field(pattern=r"^\d{8}$")
    stock_code: str | None = Field(default=None, pattern=r"^[0-9A-Z]{6}$")
    purpose: str = Field(default="공개 공시 기반 운전자금 사전조사", min_length=1, max_length=2000)
    request_key: str = Field(min_length=1, max_length=200)
    public_cutoff: date = Field(default_factory=date.today)
    budget: Budget = Field(default_factory=Budget)

    @field_validator("budget")
    @classmethod
    def bounded_budget(cls, value):
        if value.external_requests > 60 or value.model_calls > 20 or value.execution_seconds > 1800:
            raise ValueError("일반 기업 초기 조사는 외부 요청60회·모델20회·시간1800초 이내입니다.")
        return value


def receive(store, value: GeneralStartInput):
    """새 조사와 불변 수집 계획을 저장한다. 네트워크와 모델은 호출하지 않는다."""
    company = Company(name=value.company_name.strip(), corp_code=value.corp_code,
                      stock_code=value.stock_code)
    research = ResearchInput(
        company=company,
        purpose=value.purpose,
        public_cutoff=value.public_cutoff,
        comparison_window=comparison_window(value.public_cutoff),
        request_key=value.request_key,
        budget=value.budget,
    )
    trigger = {
        "kind": "general_company",
        "phase": "identity_pending",
        "collection_plan": {
            "source": "opendart",
            "company_verification": True,
            "disclosures_begin": disclosures_begin(value.public_cutoff),
            "collect_regular_reports": True,
            "collect_xbrl": True,
            "collect_financial_api": True,
            "official_ir": "deferred_until_dart_gap_and_rights_check",
        },
    }
    stored = store.create_research(research, initial_trigger=trigger)
    run = stored["runs"][0]
    return {
        "research_id": stored["id"],
        "basis_id": stored["bases"][0]["id"],
        "run_id": run["id"],
        "status": "collection_plan_pending",
        "company": stored["company"],
        "public_cutoff": str(value.public_cutoff),
        "collection_plan": trigger["collection_plan"],
        "external_requests_used": 0,
        "model_calls_used": 0,
    }


def create_collection_retry(store, research_id):
    """이전 실패/완료 수집을 보존한 채 같은 기준으로 새 수집 실행을 만든다."""
    research = store.get_research(research_id)
    basis = research["bases"][-1]
    previous = research["runs"][-1]
    trigger = {
        "kind": "general_company",
        "phase": "identity_pending",
        "collection_plan": {
            "source": "opendart", "company_verification": True, "disclosures_begin": disclosures_begin(date.fromisoformat(basis['data']['public_cutoff'])),
            "collect_regular_reports": True, "collect_xbrl": True, "collect_financial_api": True,
            "official_ir": "deferred_until_dart_gap_and_rights_check",
        },
        "supersedes_collection_run_id": previous["id"],
    }
    run = store.create_run(basis["id"], previous_run_id=previous["id"], trigger=trigger,
                           budget=Budget.model_validate(previous["budget"]))
    return {"research_id": research_id, "basis_id": basis["id"], "run_id": run["id"],
            "status": "collection_plan_pending", "previous_run_id": previous["id"]}


def create_refresh_run(store, research_id, cutoff):
    """Advance the immutable cutoff when needed, then create a fresh bounded check run."""
    research=store.get_research(research_id)
    if not research['company'].get('corp_code'):
        raise ValueError('company_identity_required')
    if not any(run['trigger'].get('kind')=='general_company' for run in research['runs']):
        raise ValueError('general_company_research_required')
    latest=research['bases'][-1]
    previous=date.fromisoformat(latest['data']['public_cutoff'])
    if cutoff<previous:
        raise ValueError('cutoff_cannot_move_backward')
    if cutoff>previous:
        basis=BasisInput.model_validate({**latest['data'],'public_cutoff':cutoff,
                                         'comparison_window':comparison_window(cutoff)})
        store.create_basis(research_id,basis,expected_version=latest['version'])
    return create_collection_retry(store,research_id)
