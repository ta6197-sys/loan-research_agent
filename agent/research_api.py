"""저장·근거 조회 API. 모델 실행이나 금융 검증 완료를 가장하지 않는다."""
import asyncio
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from .research_models import BasisInput, ResearchInput, Contract
from pydantic import Field
from .finance.numeric_validation import NumericValidationService
from .finance.extraction_service import ExtractionService
from .finance.document_extractor import ExtractionFailure
from .finance.research_followup import FollowupInput
from .finance.cached_case import CachedStartInput
from .finance.report_view import build_view,chart_svg,compare_views
from .finance.report_exports import ReportExports
from typing import Literal
from .finance.attachments import AttachmentService
from .finance.research_lifecycle import ResearchLifecycle,PlanInput

router = APIRouter()


class CachedDraftInput(Contract):
    request_key: str = Field(min_length=1,max_length=200)


class ExportInput(Contract):
    format: Literal['pdf','markdown_zip']


@router.get('/research-archives')
def archived_researches(request: Request):
    return {'items':ResearchLifecycle(request.app.state.research_store).archived()}


@router.post('/researches/{research_id}/archive')
async def archive_research(research_id: str,request: Request):
    if request.app.state.mode!='research':raise HTTPException(409,'연구 모드에서 변경해주세요.')
    return await ResearchLifecycle(request.app.state.research_store).archive(research_id,request.app.state.research_runtime)


@router.post('/researches/{research_id}/restore')
def restore_research(research_id: str,request: Request):
    if request.app.state.mode!='research':raise HTTPException(409,'연구 모드에서 변경해주세요.')
    return ResearchLifecycle(request.app.state.research_store).restore(research_id)


@router.post('/researches/{research_id}/plan',status_code=202)
async def change_research_plan(research_id: str,value: PlanInput,request: Request):
    if request.app.state.mode!='research':raise HTTPException(409,'연구 모드에서 변경해주세요.')
    service=ResearchLifecycle(request.app.state.research_store,getattr(request.app.state,'case_library',None))
    receipt=await asyncio.to_thread(service.change_plan,research_id,value)
    if receipt['status']=='queued':request.app.state.research_runtime.schedule()
    return receipt


@router.post('/researches/{research_id}/attachment-bundles',status_code=201)
async def stage_attachment_bundle(research_id: str,request: Request):
    if request.app.state.mode!='research':raise HTTPException(409,'연구 모드에서 첨부해주세요.')
    try:length=int(request.headers.get('content-length','0'))
    except ValueError:raise HTTPException(411,'첨부 요청 크기가 필요합니다.') from None
    if not 0<length<=110*1024*1024:raise HTTPException(413,'첨부 요청 크기를 초과했습니다.')
    async with request.form(max_files=10,max_fields=2,max_part_size=1000) as form:
        parent=form.get('parent_report_id');files=[];total=0
        for key,value in form.multi_items():
            if key=='parent_report_id':continue
            if key!='files' or not hasattr(value,'read'):raise HTTPException(400,'첨부 형식이 잘못됐습니다.')
            raw=await value.read(20*1024*1024+1);total+=len(raw)
            if len(raw)>20*1024*1024 or total>100*1024*1024:raise HTTPException(413,'첨부 크기를 초과했습니다.')
            files.append((value.filename,raw))
        if not isinstance(parent,str):raise HTTPException(400,'기준 게시본이 필요합니다.')
        return await asyncio.to_thread(AttachmentService(request.app.state.research_store).stage,research_id,parent,files)


@router.get('/researches/{research_id}/attachment-bundles/{bundle_id}')
def attachment_bundle(research_id: str,bundle_id: str,request: Request):
    request.app.state.research_store.get_research(research_id)
    service=AttachmentService(request.app.state.research_store)
    return service.public(service.get(bundle_id,research_id))


@router.get('/reports/{report_id}/view')
def report_view(report_id: str,request: Request):
    return build_view(request.app.state.research_store,report_id)


@router.get('/reports/{report_id}/compare/{previous_id}')
def compare_report(report_id: str,previous_id: str,request: Request):
    store=request.app.state.research_store
    return compare_views(build_view(store,previous_id),build_view(store,report_id))


@router.get('/reports/{report_id}/charts/{chart_id}.svg')
def report_chart(report_id: str,chart_id: str,request: Request):
    svg=chart_svg(build_view(request.app.state.research_store,report_id),chart_id)
    return Response(svg,media_type='image/svg+xml',headers={'Content-Security-Policy':"default-src 'none'; sandbox",'X-Content-Type-Options':'nosniff'})


@router.post('/reports/{report_id}/exports',status_code=201)
def create_export(report_id: str,value: ExportInput,request: Request):
    return ReportExports(request.app.state.research_store).create(report_id,value.format)


@router.get('/exports/{export_id}')
def download_export(export_id: str,request: Request):
    metadata,data=ReportExports(request.app.state.research_store).download(export_id)
    pdf=metadata['format']=='pdf'
    return Response(data,media_type='application/pdf' if pdf else 'application/zip',headers={
        'Content-Disposition':'attachment; filename="finance-report-v'+str(metadata['version'])+('.pdf' if pdf else '.zip')+'"',
        'X-Content-Type-Options':'nosniff'})


@router.post('/runs/{run_id}/drafts',status_code=202)
async def create_cached_draft(run_id: str,value: CachedDraftInput,request: Request):
    if request.app.state.mode!='research':
        raise HTTPException(409,'금융 초안은 연구 모드에서 실행해주세요.')
    return request.app.state.research_runtime.submit(run_id,value.request_key)


@router.post('/researches/{research_id}/inputs',status_code=202)
async def followup_input(research_id: str,value: FollowupInput,request: Request):
    if request.app.state.mode!='research':raise HTTPException(409,'연구 모드에서 실행해주세요.')
    result=await asyncio.to_thread(request.app.state.followups.receive,research_id,value)
    if result['run_id'] and result['status']=='queued':request.app.state.followups.schedule()
    return result


@router.post('/researches/{research_id}/stop')
async def stop_research(research_id: str,request: Request):
    if request.app.state.mode!='research':raise HTTPException(409,'연구 모드에서 실행해주세요.')
    return await request.app.state.research_runtime.stop(research_id)


@router.post('/researches/{research_id}/resume',status_code=202)
async def resume_research(research_id: str,value: CachedDraftInput,request: Request):
    if request.app.state.mode!='research':raise HTTPException(409,'연구 모드에서 실행해주세요.')
    return await request.app.state.research_runtime.resume(research_id,value.request_key)


@router.get('/runs/{run_id}/execution')
def execution_state(run_id: str,request: Request):
    return request.app.state.research_runtime.execution(run_id)


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


@router.get('/case-companies')
def cached_companies(request: Request,query: str = Query(min_length=1,max_length=200)):
    library=getattr(request.app.state,'case_library',None)
    if library is None:raise HTTPException(503,'승인된 보관 사례가 등록되지 않았습니다.')
    return library.search(query)


@router.post('/researches/start',status_code=202)
async def start_cached_research(value: CachedStartInput,request: Request):
    if request.app.state.mode!='research':raise HTTPException(409,'연구 모드에서 실행해주세요.')
    library=getattr(request.app.state,'case_library',None)
    if library is None:raise HTTPException(503,'승인된 보관 사례가 등록되지 않았습니다.')
    result=await asyncio.to_thread(library.receive,request.app.state.research_store,value)
    request.app.state.research_runtime.schedule()
    return result


@router.post("/researches", status_code=201)
def create_research(value: ResearchInput, request: Request):
    return request.app.state.research_store.create_research(value)


@router.get("/researches")
def list_researches(request: Request):
    return {"items": request.app.state.research_store.list_researches()}


@router.get('/researches/{research_id}/summary')
def research_summary(research_id: str,request: Request):
    data=request.app.state.research_store.get_research(research_id)
    data['reports']=[{k:v for k,v in r.items() if k in {'id','version','status','created_at','run_id'}} for r in data['reports']]
    return data


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
