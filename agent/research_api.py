"""저장·근거 조회 API. 모델 실행이나 금융 검증 완료를 가장하지 않는다."""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from .research_models import BasisInput, ResearchInput
from .finance.numeric_validation import NumericValidationService
from .finance.extraction_service import ExtractionService
from .finance.document_extractor import ExtractionFailure

router = APIRouter()


@router.get('/fragments/{fragment_id}')
def fragment(fragment_id: str, request: Request):
    return ExtractionService(request.app.state.research_store).fragment(fragment_id)


@router.get('/fragments/{fragment_id}/original')
def original_fragment(fragment_id: str, request: Request):
    try:
        content=ExtractionService(request.app.state.research_store).original(fragment_id)
    except ExtractionFailure:
        raise HTTPException(409,'원문 바이트 위치가 확인되지 않은 구간입니다.') from None
    return Response(content,media_type='application/octet-stream',headers={
        'Content-Disposition':f'attachment; filename="fragment-{fragment_id}.bin"',
        'X-Content-Type-Options':'nosniff'})


@router.get('/companies')
async def companies(request: Request, query: str = Query(min_length=1, max_length=200)):
    finance = getattr(request.app.state,'finance_mcp',None)
    if finance is None:
        raise HTTPException(503, '금융 조회 도구가 연결되지 않았습니다. 저장 자료는 계속 열람할 수 있습니다.')
    return await finance.call_finance('search_companies',{'query':query})


@router.post("/researches", status_code=201)
def create_research(value: ResearchInput, request: Request):
    return request.app.state.research_store.create_research(value)


@router.get("/researches")
def list_researches(request: Request):
    return {"items": request.app.state.research_store.list_researches()}


@router.get("/researches/{research_id}")
def get_research(research_id: str, request: Request):
    return request.app.state.research_store.get_research(research_id)


@router.post("/researches/{research_id}/bases", status_code=201)
def create_basis(research_id: str, value: BasisInput, request: Request,
                 expected_version: int = Query(ge=1)):
    return request.app.state.research_store.create_basis(research_id, value, expected_version)


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request):
    return request.app.state.research_store.get_run(run_id)


@router.get("/researches/{research_id}/events")
def events(research_id: str, request: Request, after: int = Query(default=0, ge=0)):
    # 저장 순서 조회. 실시간 SSE 구독/실행 수명 관리는 7단계에서 연결한다.
    return {"items": request.app.state.research_store.events(research_id, after)}


@router.get("/researches/{research_id}/reports")
def reports(research_id: str, request: Request):
    return {"items": request.app.state.research_store.get_research(research_id)["reports"]}


@router.get("/reports/{report_id}")
def report(report_id: str, request: Request):
    return request.app.state.research_store.get_report(report_id)


@router.get("/evidence/{fact_id}")
def evidence(fact_id: str, request: Request):
    return request.app.state.research_store.get_evidence(fact_id)


@router.get("/sources/{source_id}")
def source(source_id: str, request: Request):
    return request.app.state.research_store.get_source(source_id)


@router.get("/sources/{source_id}/view")
def source_content(source_id: str, request: Request):
    content = request.app.state.research_store.source_content(source_id)
    # HTML/XML을 앱 출처에서 실행하지 않는다. 향후 근거 뷰어에서 안전하게 표시한다.
    return Response(content, media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="source-{source_id}.bin"',
        "X-Content-Type-Options": "nosniff",
    })


@router.get('/runs/{run_id}/validations')
def validation_history(run_id: str, request: Request):
    return {'items':NumericValidationService(request.app.state.research_store).history(run_id)}


@router.get('/validation-batches/{batch_id}')
def validation_batch(batch_id: str, request: Request):
    return NumericValidationService(request.app.state.research_store).batch(batch_id)


@router.get('/storage/recovery')
def storage_recovery_status(request: Request):
    return request.app.state.research_store.source_write_status()
