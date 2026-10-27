"""저장·근거 조회 API. 모델 실행이나 금융 검증 완료를 가장하지 않는다."""
import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from .research_models import BasisInput, ResearchInput, Contract
from pydantic import Field
from .finance.numeric_validation import NumericValidationService
from .finance.extraction_service import ExtractionService
from .finance.document_extractor import ExtractionFailure
from .finance.research_followup import FollowupInput
from .finance.cached_case import CachedStartInput
from .finance.general_company import GeneralStartInput, receive as receive_general_company, create_collection_retry
from .finance.general_collection import GeneralCollectionService
from .finance.report_view import build_view,chart_svg,compare_views, text as safe_text, location_label, public_url
from .finance.report_exports import ReportExports
from typing import Literal
from .finance.attachments import AttachmentService
from .finance.research_lifecycle import ResearchLifecycle,PlanInput
from .finance.quality_metrics import calculate as quality_metrics
from .finance.quality_exceptions import build as quality_exceptions
from .finance.quality_analytics import analyze as quality_analysis
from .finance.financial_analytics import FinancialAnalyticsService, analyze as financial_analyze
from .finance.report_context import fiscal_periods, numeric_rows, xbrl_report_gate
from .finance.disclosure_audit import analysis_receipt_policy, apply_relation_evidence
from .finance.analysis_signals import build as analysis_signals
from .finance.analysis_plan import build as analysis_plan
from .finance.peer_dataset import build_snapshot
from .finance.analytics_dataset_service import AnalyticsDatasetService
from .finance.model_readiness import evaluate as model_readiness
from .finance.anomaly_detection import detect as anomaly_detection
from .finance.analysis_questions import build as analysis_questions
from .finance.broad_303_read_model import read_company as read_broad_303_company
from .research_store import Conflict

router = APIRouter()


class CachedDraftInput(Contract):
    request_key: str = Field(min_length=1,max_length=200)


class RefreshInput(Contract):
    public_cutoff: date = Field(default_factory=lambda: datetime.now(ZoneInfo('Asia/Seoul')).date())


class ExportInput(Contract):
    format: Literal['pdf','markdown_zip']


class QualityReviewInput(Contract):
    batch_id: str = Field(min_length=1, max_length=200)
    action: Literal['acknowledged','investigating','evidence_requested','note_added','closed_unresolved']
    note: str = Field(max_length=1000)


def _quality_payload(store, batch_id, report=None):
    batch = NumericValidationService(store).batch(batch_id)
    run = store.get_run(batch['run_id'])
    # 공개 메서드만 사용해 연구 ID를 얻는다. 배치는 실행 기준에 고정되어 있다.
    with store.connection() as conn:
        basis = store._one(conn, 'research_bases', run['basis_id'])
        research_id = basis['research_id']
    exceptions = quality_exceptions(research_id, batch, report)
    reviews = store.quality_reviews(research_id, batch_id, {item['exception_id'] for item in exceptions})
    by_exception = {}
    for review in reviews:
        by_exception.setdefault(review['exception_id'], []).append(review)
    for item in exceptions:
        item['reviews'] = by_exception.get(item['exception_id'], [])
    return {'schema_version': 'quality-report-1.0', 'report_id': report['id'] if report else None,
            'research_id': research_id, 'validation_batch_id': batch_id,
            'registry_version': quality_metrics(batch, report)['registry_version'],
            'metrics': quality_metrics(batch, report), 'exceptions': exceptions,
            'limitations': ['품질지표는 정해진 범위에서 수행한 자동 검증의 결과이며, 기업 재무정보 전체의 정확성 또는 금융기관의 신용심사 결론을 보증하지 않습니다.']}


def _analysis_refs(item):
    inputs = item.get('inputs', [])
    return {'input_fact_ids': sorted({value for row in inputs for value in row.get('fact_ids', [])}),
            'input_validation_ids': sorted({value for row in inputs for value in row.get('validation_ids', [])}),
            'source_ids': sorted({value for row in inputs for value in row.get('source_ids', [])})}


def _metric_contract(report_id, batch_id, item):
    payload = {'analysis_id': 'financial:' + item['id'], 'analysis_version': 'financial-analytics-1.0',
               'report_id': report_id, 'validation_batch_id': batch_id, 'status': item['status'],
               'metric_id': item['id'], 'name': item['name'], 'unit': item.get('unit'), 'value': item.get('value'), 'period': item.get('period'),
               'scope': item.get('scope'), 'method_version': 'financial-analytics-1.0',
               'limitations': ['검증된 수치의 산술 분석이며 신용판단이 아닙니다.'],
               'reason_code': item.get('reason')}
    payload.update(_analysis_refs(item))
    return payload


def _financial_analysis_payload(store, report_id):
    report = store.get_report(report_id)
    if report['status'] != 'published':
        raise HTTPException(409, '게시된 보고서의 분석만 조회할 수 있습니다.')
    financial = FinancialAnalyticsService(store).report(report_id)
    batch_id = financial['validation_batch_id']
    signals = analysis_signals(financial)
    stored = AnalyticsDatasetService(store).for_report(report_id)
    if stored:
        company = store.get_research(report['research_id'])['company']['corp_code']
        readiness, anomaly = stored['ml_readiness'], stored['anomaly_detection']
        statistics = stored['statistics'][company]
    else:
        # 등록된 스냅샷이 없다는 실제 조회 결과에 대해서만 부족 상태를 반환한다.
        peer_snapshot = build_snapshot([])
        readiness, anomaly = model_readiness(peer_snapshot), anomaly_detection(peer_snapshot)
        statistics = {'status': 'insufficient_peer_data', 'reason_code': 'verified_peer_dataset_not_available',
                      'comparison_definition': None, 'trend_analysis': [], 'peer_comparisons': [],
                      'limitations': ['해당 보고서에 연결된 검증 비교집단 스냅샷이 없습니다.']}
    questions = analysis_questions(anomaly, statistics, store.get_research(report['research_id'])['company']['corp_code'])
    signal_rows = []
    for item in signals['signals']:
        evidence = item.get('evidence', [])
        refs = _analysis_refs({'inputs': [ref for metric in evidence for ref in metric.get('inputs', [])]})
        signal_rows.append({'analysis_id': 'signal:' + item['id'], 'analysis_version': signals['schema_version'],
                            'report_id': report_id, 'validation_batch_id': batch_id, 'status': item['status'],
                            'signal_id': item['id'], 'value': None, 'period': item.get('period'), 'scope': item.get('scope'),
                            'method_version': signals['schema_version'], 'limitations': [item['limitation']],
                            'reason_code': None, **refs, 'question': item['question']})
    metrics = [_metric_contract(report_id, batch_id, item) for item in financial['results']]
    plan = analysis_plan(report_id, batch_id, metrics, signal_rows,
                         report.get('content',{}).get('context',{}).get('financial_rows',[]))
    return {'analysis_id': 'report-analysis:' + report_id, 'analysis_version': 'report-analysis-1.0',
            'report_id': report_id, 'validation_batch_id': batch_id, 'status': 'measured',
            'financial_metrics': metrics,
            'analysis_signals': signal_rows,
            'analysis_plan': plan,
            'statistical_analysis': statistics, 'dataset_artifact_id': stored['artifact_id'] if stored else None,
            'ml_readiness': readiness, 'anomaly_detection': anomaly, 'followup_questions': questions,
            'limitations': financial['limitations'] + ['기존 보고서·PDF·원문·검증 배치는 변경하지 않는 읽기 모델입니다.']}


def _run_verified_analysis_payload(store, run_id):
    """게시본 없이 끝난 범용 수집 실행의 검증 자료만 읽는다."""
    run = store.get_run(run_id)
    collection = run.get('trigger', {}).get('collection_result', {})
    fiscal = collection.get('fiscal_period', {})
    if (run.get('status') != 'finished' or run.get('trigger', {}).get('kind') != 'general_company'
            or collection.get('next_phase') != 'numeric_and_xbrl_validation'
            or fiscal.get('status') not in {'supported', 'identity_only'}):
        raise HTTPException(409, '완료된 범용 공시 수집과 결산월 확인이 필요합니다.')
    try:
        periods = fiscal_periods(collection.get('target_reports', []), fiscal.get('month'))
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    service = NumericValidationService(store)
    history = service.history(run_id)
    numeric_ids = [b['id'] for b in history if b['policy_version'].startswith('numeric-validation-')]
    mapping_ids = [b['id'] for b in history if b['policy_version'].startswith('xbrl-mapping-')]
    if len(numeric_ids) != 1 or len(mapping_ids) != 1:
        raise HTTPException(409, '실행에 연결된 수치 검증과 독립 XBRL 배치가 각각 하나씩 필요합니다.')
    batch, mapping = service.batch(numeric_ids[0]), service.batch(mapping_ids[0])
    if not batch['validations'] or not mapping['validations']:
        raise HTTPException(409, '수치 검증과 독립 XBRL 검증 결과가 필요합니다.')
    try:
        receipt_policy = analysis_receipt_policy(batch.get('summary', {}).get('disclosure_history_audit', {}),
                                                 collection['target_reports'])
        receipt_policy = apply_relation_evidence(store,
            batch.get('summary', {}).get('disclosure_history_audit', {}), receipt_policy)
    except ValueError:
        raise HTTPException(409, '대상 보고서의 정정 공시 이력이 확인되지 않아 분석 입력을 보류합니다.') from None
    allowed = set(receipt_policy['allowed_receipts'])
    withheld = set(receipt_policy['withheld_receipts'])
    target_by_period = {(int(item['year']), item['report_code']):item['rcept_no']
                        for item in collection['target_reports']}
    withheld_ends = {item['end'] for item in periods
                     if target_by_period.get((item['year'], item['report_code'])) in withheld}
    source_receipts = {}
    def safe_validation(item):
        if item.get('rule_id') != 'numeric_fact':
            return True
        source_id = item.get('data', {}).get('source_id')
        if source_id not in source_receipts:
            source_receipts[source_id] = store.get_source(source_id)['metadata'].get('receipt_number') if source_id else None
        return source_receipts[source_id] in allowed
    accepted_batch = {**batch, 'validations':[item for item in batch['validations'] if safe_validation(item)]}
    accepted_mapping = {**mapping, 'validations':[item for item in mapping['validations']
                        if item.get('rule_id') != 'XBRL_STANDARD_CONCEPT'
                        or item.get('data', {}).get('receipt_number') in allowed]}
    period_ends = tuple(p['end'] for p in periods)
    original_rows = numeric_rows(batch, period_ends, periods)
    rows = numeric_rows(accepted_batch, period_ends, periods)
    original_status = {(row['scope'], row['end'], row['account']):row['status'] for row in original_rows}
    for row in rows:
        if row['end'] in withheld_ends:
            row.update(status='withheld', reason='correction_history_unconfirmed')
            row.pop('value', None)
        elif row['status'] != 'verified' and original_status[(row['scope'], row['end'], row['account'])]=='verified':
            row['reason']='unreviewed_correction_source_excluded'
    def raw_ok(row):
        try:
            for fact_id in row.get('fact_ids', []):
                fact = store.get_evidence(fact_id)
                if (fact['run_id'] != run_id or fact['source']['metadata'].get('receipt_number') not in allowed
                        or not NumericValidationService._raw_check(
                            store.source_content(fact['source']['id']), fact['fragment']['data']['location'])):
                    return False
            return bool(row.get('fact_ids'))
        except (ValueError, KeyError):
            return False
    for row in rows:
        if row['status'] == 'verified' and not raw_ok(row):
            row.update(status='withheld', reason='original_source_integrity_unconfirmed')
            row.pop('value', None)
    gate = xbrl_report_gate(rows, accepted_batch, accepted_mapping)
    if gate['status'] != 'pass':
        raise HTTPException(409, '독립 XBRL 계정·기간·금액 대응이 확인되지 않았습니다.')
    evidence = []
    for fact_id in sorted({identifier for row in rows if row['status'] == 'verified' for identifier in row['fact_ids']}):
        fact = store.get_evidence(fact_id)
        source, fragment = fact['source'], fact['fragment']
        metadata = source['metadata']
        evidence.append({'fact_id': fact_id, 'fragment_id': fragment['id'], 'source_id': source['id'],
                         'title': safe_text(metadata.get('title')), 'receipt_number': metadata.get('receipt_number'),
                         'url': public_url(metadata.get('url')), 'excerpt': safe_text(fragment['data'].get('text'))[:1800],
                         'location': location_label(fragment['data']['location']),
                         'content_hash': source['content_hash'], 'scope': fact['data'].get('scope'),
                         'period': fact['data'].get('period')})
    financial = financial_analyze({'content': {'context': {'financial_rows': rows}}}, accepted_batch, raw_ok=raw_ok)
    metrics = [_metric_contract(None, batch['id'], item) for item in financial['results']]
    signals = analysis_signals(financial)
    signal_rows = []
    for item in signals['signals']:
        refs = _analysis_refs({'inputs': [ref for metric in item.get('evidence', []) for ref in metric.get('inputs', [])]})
        signal_rows.append({'signal_id': item['id'], 'status': item['status'], 'question': item['question'],
                            'period': item.get('period'), 'scope': item.get('scope'), 'limitations': [item['limitation']], **refs})
    return {'analysis_id': 'run-analysis:' + run_id, 'analysis_version': 'run-analysis-1.0',
            'run_id': run_id, 'report_id': None, 'publication_status': 'not_published',
            'validation_batch_id': batch['id'], 'mapping_batch_id': mapping['id'],
            'status': 'validated_data_preview', 'financial_rows': rows,
            'disclosure_history': {**receipt_policy, 'withheld_period_ends':sorted(withheld_ends)},
            'evidence': evidence,
            'row_counts': {'verified': sum(r['status'] == 'verified' for r in rows),
                           'withheld': sum(r['status'] != 'verified' for r in rows)},
            'xbrl_gate': gate, 'financial_metrics': metrics, 'analysis_signals': signal_rows,
            'analysis_plan': analysis_plan(None, batch['id'], metrics, signal_rows, rows),
            'limitations': financial['limitations'] + ['게시 보고서가 없는 검증 자료 조회이며 비교집단·ML 결과를 포함하지 않습니다.']}


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


@router.get('/reports/{report_id}/quality')
def report_quality(report_id: str, request: Request):
    store = request.app.state.research_store
    report = store.get_report(report_id)
    if report['status'] != 'published':
        raise HTTPException(409, '게시된 보고서의 품질만 조회할 수 있습니다.')
    return _quality_payload(store, report['publication']['validation_batch_id'], report)


@router.get('/reports/{report_id}/financial-analysis')
def report_financial_analysis(report_id: str, request: Request):
    return _financial_analysis_payload(request.app.state.research_store, report_id)


@router.get('/cohorts/broad-303/companies/{corp_code}/analysis')
def broad_303_company_analysis(corp_code: str, scope: Literal['OFS','CFS'] = 'OFS',
                               snapshot_version: Literal['v1','v2'] = 'v2'):
    if len(corp_code) != 8 or not corp_code.isdigit():
        raise HTTPException(422, '8자리 DART 법인코드가 필요합니다.')
    try:
        result = read_broad_303_company(corp_code, scope, snapshot_version)
    except (OSError, ValueError, KeyError) as error:
        raise HTTPException(503, '탐색 비교 스냅샷을 확인할 수 없습니다: '+str(error)) from error
    if result is None:
        raise HTTPException(404, '고정 탐색 비교집단에 포함되지 않은 법인입니다.')
    return result


@router.get('/runs/{run_id}/verified-analysis')
def run_verified_analysis(run_id: str, request: Request):
    return _run_verified_analysis_payload(request.app.state.research_store, run_id)


@router.get('/validation-batches/{batch_id}/quality')
def batch_quality(batch_id: str, request: Request):
    return _quality_payload(request.app.state.research_store, batch_id)


@router.get('/validation-batches/{batch_id}/quality-analysis')
def batch_quality_analysis(batch_id: str, request: Request):
    store = request.app.state.research_store
    batch = NumericValidationService(store).batch(batch_id)
    run = store.get_run(batch['run_id'])
    with store.connection() as conn:
        basis = store._one(conn, 'research_bases', run['basis_id'])
        research_id = basis['research_id']
    with store.connection() as conn:
        ids = [row[0] for row in conn.execute(
            "SELECT b.id FROM research_validation_batches b JOIN research_runs r ON r.id=b.run_id "
            "JOIN research_bases a ON a.id=r.basis_id WHERE a.research_id=? AND b.created_at<=? "
            "AND (b.id=? OR EXISTS(SELECT 1 FROM research_validations v WHERE v.batch_id=b.id AND v.rule_id='numeric_fact')) "
            "ORDER BY b.created_at,b.rowid", (research_id, batch['created_at'], batch_id))]
    history = [NumericValidationService(store).batch(identifier) for identifier in ids if identifier != batch_id]
    # 분포는 요청 배치로 고정하고 추세만 과거 배치를 포함한다.
    result = quality_analysis(research_id, [batch])
    from .finance.quality_analytics import _trend
    result['batch_trend'] = _trend(history + [batch], {})
    return {'analysis_id': 'quality:' + batch_id, 'analysis_version': result['schema_version'],
            'report_id': None, 'validation_batch_id': batch_id, 'status': result['status'],
            'method_version': result['schema_version'], 'limitations': result.get('limitations', []),
            'reason_code': result.get('reason'), **result}


@router.post('/researches/{research_id}/quality-exceptions/{exception_id}/reviews', status_code=201)
def create_quality_review(research_id: str, exception_id: str, value: QualityReviewInput, request: Request):
    store = request.app.state.research_store
    try:
        payload = _quality_payload(store, value.batch_id)
        if payload['research_id'] != research_id:
            raise HTTPException(409, '다른 조사의 검증 배치에는 검토를 남길 수 없습니다.')
        if exception_id not in {item['exception_id'] for item in payload['exceptions']}:
            raise HTTPException(404, '현재 검증 배치에 없는 품질 예외입니다.')
        return store.add_quality_review(research_id, value.batch_id, exception_id, value.action, value.note)
    except HTTPException:
        raise
    except (ValueError, Conflict) as error:
        raise HTTPException(409, str(error)) from None


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


@router.post('/researches/general/start',status_code=202)
async def start_general_research(value: GeneralStartInput,request: Request):
    if request.app.state.mode!='research':raise HTTPException(409,'연구 모드에서 실행해주세요.')
    return await asyncio.to_thread(receive_general_company,request.app.state.research_store,value)


@router.post('/runs/{run_id}/collect',status_code=202)
async def collect_general_research(run_id: str,request: Request):
    if request.app.state.mode!='research':raise HTTPException(409,'연구 모드에서 실행해주세요.')
    service=GeneralCollectionService(request.app.state.research_store)
    return await service.run(run_id)


@router.post('/runs/{run_id}/report',status_code=202)
async def create_general_report(run_id: str,value: CachedDraftInput,request: Request):
    """수집을 마친 일반기업 실행을 공통 검증·보고서 작업 큐에 넣는다."""
    if request.app.state.mode!='research':raise HTTPException(409,'연구 모드에서 실행해주세요.')
    return request.app.state.research_runtime.submit(run_id,value.request_key)


@router.post('/researches/{research_id}/general-runs',status_code=202)
async def retry_general_collection(research_id: str,request: Request):
    if request.app.state.mode!='research':raise HTTPException(409,'연구 모드에서 실행해주세요.')
    return await asyncio.to_thread(create_collection_retry,request.app.state.research_store,research_id)


@router.post('/researches/{research_id}/refresh',status_code=202)
async def refresh_general_research(research_id: str,value: RefreshInput,request: Request):
    if request.app.state.mode!='research':raise HTTPException(409,'연구 모드에서 실행해주세요.')
    if value.public_cutoff>datetime.now(ZoneInfo('Asia/Seoul')).date():
        raise HTTPException(422,'공개 기준일은 오늘 이후일 수 없습니다.')
    return await GeneralCollectionService(request.app.state.research_store).refresh(research_id,value.public_cutoff)


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
