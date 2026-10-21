"""승인된 보관 사례를 동일 조건 3회씩 비교한다. 외부 수집/모델/추적 없음.

평가 파일은 새 폴더에만 쓴다. 실패한 회차도 보존하고 성공 회차만 고르지 않는다.
"""
import asyncio
from contextlib import nullcontext
from datetime import datetime,timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time
from unittest.mock import patch
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.update(OPENDART_API_KEY='',LANGCHAIN_TRACING_V2='false',LANGSMITH_TRACING='false',
    APP_ALLOW_EXTERNAL_MODEL='false',APP_ALLOW_EXTERNAL_TRACE='false')
import httpx
from agent.finance.cached_case import CachedCaseLibrary,CachedStartInput,preferred_manifest
from agent.finance.evaluation import FixedSequenceGraph,grade_report
from agent.finance.local_model import LocalFinanceModel
from agent.finance.report_view import build_view
from agent.finance.research_stage05 import Stage05Service
from agent.finance.stage04_completion import code_fingerprint
from agent.research_store import ResearchStore,safe_payload


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):Path(path).write_text(safe_payload(value))
def fingerprints(folder):return {str(p.relative_to(ROOT)):sha(p) for p in folder.rglob('*') if p.is_file()}


def prepare_reference(library):
    """기존 수작업 수치의 API 원문을 별도로 읽고 동일 계정/범위/기간 값을 확인한다.

    수작업 초안을 독립 전문가 정답으로 간주하지 않는다. 구성 사용 보류 정책만
    고정4단계 기록에서 읽으며 정답 값은 수작업/API 원문에서 가져온다.
    """
    case=ROOT/'doc/finance/cases/sangsin_brake';manual=json.loads((case/'financial_evidence.json').read_text())
    integrated=json.loads((library.directory/'numeric_batch.json').read_text())
    from agent.finance.report_context import numeric_rows
    eligibility={(r['scope'],r['end'],r['account']):r['status'] for r in numeric_rows(integrated)}
    aliases={'current_receivables':'receivables','short_borrowings':'short_term_borrowings',
        'current_long_borrowings':'current_long_term_borrowings','noncurrent_borrowings':'long_term_borrowings'}
    rows=[];files={};skip={'total_borrowings','current_borrowings'}
    for record in manual['records']:
        path=case/record['source_file'];files[str(path.relative_to(ROOT))]=sha(path)
        payload=json.loads(path.read_text());query=payload['query']
        assert query['corp_code']=='00129642' and query['fs_div']==record['scope']
        for name,metric in record['metrics'].items():
            if name in skip:continue
            account=aliases.get(name,name);end=record['balance_date'];key=(record['scope'],end,account)
            matches=[(i,r) for i,r in enumerate(payload['response']['list']) if r['account_id']==metric['account_id']
                and r['sj_div']==metric['statement'] and r['rcept_no']==metric['receipt']]
            matches=[(i,r) for i,r in matches if r['corp_code']=='00129642'
                and Decimal(str(r.get(metric['amount_field'],'')).replace(',',''))==Decimal(str(metric['value_krw']))]
            if len(matches)!=1:raise ValueError('manual_reference_api_not_unique')
            i,raw=matches[0]
            rows.append({'scope':record['scope'],'end':end,'account':account,'value':str(metric['value_krw']),
                'eligibility':eligibility[key],'reference_file':str(path.relative_to(ROOT)),
                'reference_hash':files[str(path.relative_to(ROOT))],'json_pointer':f'/response/list/{i}/{metric["amount_field"]}',
                'account_id':raw['account_id'],'statement':raw['sj_div'],'receipt':raw['rcept_no']})
    if len(rows)!=130:raise ValueError('reference_grid_incomplete')
    narrative=[
        ('cashflow_working_capital','cashflow',['24,504,960,486','4,459,391,461','5,497,759,301'],'CASE_REPORT.md:115; 2026H1 별도 주석27'),
        ('noncash_fx','cashflow',['4,750,951,157'],'CASE_REPORT.md:121; 2026H1 별도 주석25/27'),
        ('subsequent_guarantee','disclosure',['10,972,896,000','119,125,950,000'],'CASE_REPORT.md:145; 20260820800110'),
        ('audit_opinion','disclosure',['적정','종속기업투자주식'],'CASE_REPORT.md:149; 2025 감사보고서'),
        ('litigation','disclosure',['주휴수당'],'CASE_REPORT.md:153; 2026H1 별도 주석31'),
        ('business_structure','business',['보수용','주문자생산'],'CASE_REPORT.md:우선 질문표; 2026H1 사업의 내용')]
    return {'version':'stage09-reference-1','company':manual['corp_code'],'public_cutoff':'2026-10-20',
        'rows':rows,'reference_files':files,'manual_evidence_hash':sha(case/'financial_evidence.json'),
        'manual_report_hash':sha(case/'CASE_REPORT.md'),'eligibility_policy_hash':sha(library.directory/'numeric_batch.json'),
        'unknown_ids':['U_borrowing','U_working_capital','U_repayment','U_scope'],
        'section_ids':['identity','business','finance','cashflow','borrowing','disclosure','purpose'],
        'narrative_checks':[{'id':i,'section':s,'needles':n,'source_reference':r} for i,s,n,r in narrative],
        'independent_expert_reference':False,'numeric_reference_scope':'manual values reread from archived API; body/location rechecked separately by report view',
        'narrative_reference_scope':'manual-case review checklist; keyword presence never treated as semantic verification'}


class MeasuredModel(LocalFinanceModel):
    def __init__(self):super().__init__();self.attempts=[];self.reservations=0
    async def draft(self,evidence,*,before_request,**kwargs):
        item={'evidence_hash':hashlib.sha256(safe_payload(evidence).encode()).hexdigest(),
            'feedback':kwargs.get('validation_feedback')};started=time.monotonic()
        async def reserve():
            await before_request();self.reservations+=1;item['request_reserved']=True
        try:
            result=await super().draft(evidence,before_request=reserve,**kwargs)
            item.update(status='validated',usage=result['usage']);return result
        except Exception as error:
            item.update(status='rejected_or_unavailable',error_type=type(error).__name__,reason=str(error)[:120]);raise
        finally:item['elapsed_seconds']=round(time.monotonic()-started,3);self.attempts.append(item)


async def main():
    library=CachedCaseLibrary(ROOT,preferred_manifest(ROOT));library.verify()
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    target=ROOT/'data/research/evaluation_stage09'/stamp;target.mkdir(parents=True)
    protected=fingerprints(library.directory)|fingerprints(ROOT/'doc/finance/cases')
    protected[str(library.path.relative_to(ROOT))]=sha(library.path)
    if (ROOT/'.env').exists():protected['.env']=sha(ROOT/'.env')
    save(target/'preservation_before.json',protected)
    reference=prepare_reference(library);save(target/'reference.json',reference)
    order=[('fixed',1),('agent',1),('agent',2),('fixed',2),('fixed',3),('agent',3)]
    with httpx.Client(trust_env=False,timeout=5) as client:
        version=client.get('http://127.0.0.1:11434/api/version');version.raise_for_status()
        tags=client.get('http://127.0.0.1:11434/api/tags');tags.raise_for_status()
        selected=[m for m in tags.json()['models'] if m['name']=='qwen3.5:2b']
        if len(selected)!=1:raise ValueError('approved_local_model_missing')
    protocol={'version':'stage09-comparison-1','order':order,'code_fingerprint':code_fingerprint(ROOT),
        'evaluated_source_files':[{'path':str(p.relative_to(ROOT)),'sha256':sha(p)}
            for directory in ('agent','sh','tests') for p in sorted((ROOT/directory).rglob('*.py'))],
        'source_manifest_hash':library.fingerprint,'reference_hash':sha(target/'reference.json'),
        'model':'qwen3.5:2b','model_digest':selected[0]['digest'],'ollama':version.json()['version'],
        'python':platform.python_version(),'temperature':0,'num_ctx':8192,'num_predict':2048,
        'external_requests_per_run':0,'model_budget_per_run':20,'draft_attempt_limit':3,'execution_seconds_per_run':1800,
        'same_fresh_database_and_source_policy':True,'stored_validation_flags_copied':False,
        'control':'same initial research nodes/queries/validators/model; fixed sequential execution instead of LangGraph',
        'cache_policy':'fresh application DB each run; archived source bytes shared; no application result reuse; Ollama prompt/OS cache not reset',
        'timing_interpretation':'descriptive only; first model load and shared cache prevent causal speed comparison',
        'independent_expert':False,'external_trace':False}
    save(target/'protocol.json',protocol);print(safe_payload({'evaluation_directory':str(target.relative_to(ROOT)),'protocol_frozen':True}),flush=True)
    outcomes=[]
    for arm,repeat in order:
        folder=target/f'{arm}-{repeat}';folder.mkdir();started=time.monotonic()
        model=MeasuredModel();store=ResearchStore(folder/'session.db',folder/'sources')
        receipt=library.receive(store,CachedStartInput(company='상신브레이크',
            purpose='공개 공시 보관 자료로 수행하는 가상 운전자금 사전조사 비교 평가. 실제 대출 신청이 아니다.',request_key='stage09-input'))
        print(safe_payload({'arm':arm,'repeat':repeat,'status':'started'}),flush=True)
        outcome={'arm':arm,'repeat':repeat,'run_id':receipt['run_id']}
        try:
            context=patch('agent.finance.research_stage05.StateGraph',FixedSequenceGraph) if arm=='fixed' else nullcontext()
            with context:report=await Stage05Service(store,model,case_library=library).run(receipt['run_id'],'cached-case-initial')
            save(folder/'report.json',report)
            outcome.update(grade_report(report,reference));outcome['report_id']=report['id']
            outcome['model_status']=report['content']['model_status']
            view=build_view(store,report['id']);save(folder/'view.json',view)
            outcome['original_location_readback']='pass'
            outcome['evidence_count']=len(view['evidence'])
            with store.connection() as conn:
                outcome['source_count']=conn.execute('SELECT count(*) FROM research_sources WHERE run_id=?',(receipt['run_id'],)).fetchone()[0]
            outcome['status']='completed'
        except Exception as error:
            outcome.update(status='failed',error_type=type(error).__name__,reason=str(error)[:200])
        outcome.update(elapsed_seconds=round(time.monotonic()-started,3),usage=store.get_run(receipt['run_id'])['usage'],
            model_attempts=model.attempts,local_request_reservations=model.reservations)
        save(folder/'outcome.json',outcome);outcomes.append(outcome);save(target/'outcomes.json',outcomes)
        print(safe_payload({k:outcome.get(k) for k in ('arm','repeat','status','numeric_status','model_status','elapsed_seconds','local_request_reservations')}),flush=True)
    changed=[name for name,h in protected.items() if (ROOT/name).exists() and sha(ROOT/name)!=h]
    deleted=[name for name in protected if not (ROOT/name).exists()]
    result={'status':'recorded','scope':'one_archived_company_and_synthetic_tests','comparisons':outcomes,
        'six_runs_attempted':len(outcomes)==6,'financial_confirmation':'incomplete','overall_acceptance':'not_demonstrated',
        'time_saving_claim':False,'independent_expert_review':False,'external_requests':0,'external_trace':False,
        'local_model_requests':sum(x['local_request_reservations'] for x in outcomes),
        'preservation':{'original_file_count':len(protected),'changed':len(changed),'deleted':len(deleted)}}
    save(target/'result.json',result)
    if changed or deleted:raise ValueError('original_preservation_failed')
    print(safe_payload({'result_file':str((target/'result.json').relative_to(ROOT)),
        'six_runs_attempted':True,'local_model_requests':result['local_model_requests'],'preservation':result['preservation']}),flush=True)


if __name__=='__main__':asyncio.run(main())
