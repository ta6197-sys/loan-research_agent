"""저장된 공시의 일곱 항목→금융 초안→검증 표/차트→불변 게시본을 연결한다."""
import asyncio
import time
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import StateGraph,START,END
from langgraph.errors import NodeCancelledError
from langsmith import tracing_context

from agent.research_store import Conflict,safe_payload,digest,now,row_data
from .local_model import LocalFinanceModel,checked_draft
from .numeric_validation import NumericValidationService
from .xbrl_mapping import XbrlMappingService
from .report_context import SECTIONS,prepare_context
from .research_followup import freeze_followup
from .execution_journal import ExecutionJournal,ExecutionDeadline,worker_lease


class ResearchState(TypedDict,total=False):
    plan:list
    context:dict
    model:dict
    model_status:str
    report:dict


class Stage05Service:
    def __init__(self,store,model=None,*,worker_owned=False,case_library=None):
        self.store=store;self.model=model or LocalFinanceModel();self.worker_owned=worker_owned;self.case_library=case_library

    def _existing(self,run_id,key):
        with self.store.connection() as conn:
            old=conn.execute('SELECT id FROM research_reports WHERE run_id=? AND request_key=?',(run_id,key)).fetchone()
        return self.store.get_report(old['id']) if old else None

    def _checkpoint(self,run_id,stage,*,reserve=False):
        self.journal.account(stage,reserve_model=reserve)

    async def run(self,run_id,request_key):
        if self.worker_owned:return await self._run(run_id,request_key)
        with worker_lease(self.store):return await self._run(run_id,request_key)

    async def _run(self,run_id,request_key):
        previous=self._existing(run_id,request_key)
        if previous and previous['status']=='published':return previous
        with self.store.connection(write=True) as conn:
            run,basis=self.store._context(conn,run_id)
            if run['status'] in {'stopped','stopping'}:raise Conflict('중단된 조사입니다.')
            if conn.execute("SELECT 1 FROM research_runs WHERE status='running' LIMIT 1").fetchone():
                raise Conflict('다른 조사가 실행 중입니다. 후속 작업은 대기합니다.')
            evidence_run_id=freeze_followup(self.store,conn,run,basis)
            self.journal=ExecutionJournal(self.store,run_id,request_key,conn)
            conn.execute("UPDATE research_runs SET status='running' WHERE id=?",(run_id,))
            self.store._event(conn,basis['research_id'],basis['id'],run_id,'stage05_started',{'request_key':request_key})


        async def plan(state):
            if state.get('plan'):return {'plan':state['plan']}
            self._checkpoint(run_id,'stage05_plan')
            trigger=self.store.get_run(run_id)['trigger']
            affected=trigger.get('affected_sections',[sid for sid,_ in SECTIONS])
            focus=trigger.get('focus_sections',[])
            ordered=sorted(SECTIONS,key=lambda item:(item[0] not in focus,focus.index(item[0]) if item[0] in focus else 0))
            items=[{'section':sid,'title':label,'source_policy':'stored_disclosures_and_unverified_user_input',
                    'action':'recheck' if sid in affected else 'reuse_previous','user_focus':sid in focus} for sid,label in ordered]
            with self.store.connection(write=True) as conn:
                _,basis=self.store._context(conn,run_id)
                self.store._event(conn,basis['research_id'],basis['id'],run_id,'research_plan_created',{'items':items})
            self.journal.account('plan_saved',phase='planned',updates={'plan':items},progress=True)
            return {'plan':items}

        async def research(state):
            if self.store.get_run(run_id)['trigger'].get('kind')=='cached_case':
                if self.case_library is None:raise ValueError('cached_case_library_unavailable')
                await asyncio.to_thread(self.case_library.prepare,self.store,run_id,lambda:self._checkpoint(run_id,'cached_source_guard'))
            self.batch=await asyncio.to_thread(NumericValidationService(self.store).validate_run,evidence_run_id)
            self.mapping=await asyncio.to_thread(XbrlMappingService(self.store).validate_run,evidence_run_id)
            context=await asyncio.to_thread(prepare_context,self.store,self.batch,self.mapping,report_run_id=run_id)
            # 원문 위치와 해시를 제공할 수 있는 수치만 모델 사실 입력으로 사용한다.
            def check_raw():
                for row in context['financial_rows']:
                    if row['status']!='verified':continue
                    for fid in row['fact_ids']:
                        fact=self.store.get_evidence(fid)
                        if not NumericValidationService._raw_check(self.store.source_content(fact['source']['id']),fact['fragment']['data']['location']):
                            raise ValueError('report_fact_original_location_invalid')
            await asyncio.to_thread(check_raw)
            if state.get('context') and state['context']!=context:raise ValueError('saved_context_changed')
            self.journal.account('research_saved',phase='researched' if not state.get('model_status') else 'written',updates={'context':context},progress=not bool(state.get('context')))
            return {'context':context}

        async def write(state):
            if state.get('model_status'):
                return {'model':state['model'],'model_status':state['model_status']}
            evidence=state['context']['model_evidence']
            policy=state['context'].get('external_model_policy',{})
            if getattr(self.model,'requires_external_model_permission',False):
                override=(getattr(self.model,'allow_archived_public_override',False)
                          and policy.get('archived_public_only',False))
                if not (policy.get('allowed',False) or override):
                    result={'model':{'rejected_attempts':[], 'external_model_policy':{
                        'allowed':False,'reason':policy.get('reason','source_external_model_permission_missing'),
                        'blocked_source_count':policy.get('blocked_source_count',0),
                        'archived_public_override':False}},
                            'model_status':'withheld_by_source_policy'}
                    self.journal.account('external_model_withheld_saved',phase='written',updates=result,progress=True)
                    return result
            attempts=list(self.journal.state.get('attempt_errors',[]))
            feedback=self.journal.state.get('validation_feedback')
            while self.journal.attempts()<3:
                returned=False
                try:
                    async def reserve():self._checkpoint(run_id,'stage05_model_reserved',reserve=True)
                    remaining=self.store.get_run(run_id)
                    timeout=min(180,remaining['budget']['execution_seconds']-remaining['usage']['execution_seconds'])
                    result=await self.model.draft(evidence,before_request=reserve,timeout=max(.01,timeout),validation_feedback=feedback)
                    returned=True
                    checked_draft(result['draft'],evidence)
                    result={'model':{**result,'rejected_attempts':attempts,
                        'external_model_policy':{'allowed':True,
                            'permission_mode':'source_metadata' if policy.get('allowed') else 'user_approved_archived_public_override'}},
                            'model_status':'validated'}
                    self.journal.account('draft_saved',phase='written',updates=result,progress=True)
                    return result
                except ExecutionDeadline:raise
                except (ValueError,KeyError,asyncio.TimeoutError) as error:
                    if returned:raise
                    reason=str(error)
                    allowed={'withheld_evidence_not_preserved','unsupported_or_modified_fact','invalid_followup_question','duplicate_fact_reference','followup_question_missing'}
                    attempts.append({'attempt':self.journal.attempts(),'error_type':type(error).__name__,
                                     'reason':reason if reason in allowed else 'model_unavailable_or_invalid'})
                    feedback=reason if reason in allowed else None
                    self.journal.account('model_attempt_saved',updates={'attempt_errors':attempts,'validation_feedback':feedback},progress=True)
                    if reason not in allowed:break
                except Conflict:raise
                except Exception as error:
                    attempts.append({'attempt':self.journal.attempts(),'error_type':type(error).__name__,'reason':'model_unavailable_or_invalid'})
                    break
            result={'model':{'rejected_attempts':attempts},'model_status':'unavailable_or_rejected'}
            self.journal.account('model_withheld_saved',phase='written',updates=result,progress=True)
            return result

        async def validate_and_publish(state):
            # 생성 중에 원문이 바뀌었어도 이전 확인 플래그로 게시하지 않는다.
            batch=await asyncio.to_thread(NumericValidationService(self.store).validate_run,evidence_run_id)
            if batch['id']!=self.batch['id']:raise ValueError('evidence_changed_during_draft')
            current=await asyncio.to_thread(prepare_context,self.store,batch,self.mapping,report_run_id=run_id)
            if current!=state['context']:raise ValueError('report_context_changed')
            if state['model_status']=='validated':
                checked_draft(state['model']['draft'],current['model_evidence'])
            self._checkpoint(run_id,'stage05_publication_validation')
            content={'workflow_version':'stage05-general-company-1.0','context':current,
                'model_status':state['model_status'],'model':state['model'],
                'confirmed_facts':[r for r in current['financial_rows'] if r['status']=='verified'],
                'interpretations':[],'unknowns':current['unknowns'],
                'questions':required_questions(current),
                'limitations':current['limitations']}
            gaps={u['evidence_id'] for u in current['unknowns'] if u.get('evidence_id')}
            candidates=state['model'].get('draft',{}).get('questions',[])
            content['agent_review_questions']=[q for q in candidates if not (set(q['evidence_ids'])&gaps)]
            for question in candidates:
                if set(question['evidence_ids'])&gaps:
                    related=[q for q in content['questions'] if set(q['evidence_ids'])&set(question['evidence_ids'])]
                    content['questions'].append({**question,'reason':' / '.join(q['reason'] for q in related),
                        'requested_information':' / '.join(q['requested_information'] for q in related),
                        'status':'answered_unverified' if any(q['status']=='answered_unverified' for q in related) else 'unresolved'})
            if state['model_status']!='validated':
                reason=('원문별 외부 모델 이용 허가가 없어 원격 모델 호출을 하지 않았다.'
                        if state['model_status']=='withheld_by_source_policy'
                        else '모델 설명·질문은 미작성/거부 상태다. 검증된 수치·근거와 미확인 범위를 우선 제공한다.')
                content['limitations'].append(reason)
            report=self._publish(run_id,request_key,content,batch['id'])
            return {'report':report}

        graph=StateGraph(ResearchState)
        for name,node in (('plan',plan),('research',research),('write',write),('validate_and_publish',validate_and_publish)):
            graph.add_node(name,node)
        graph.add_edge(START,'plan');graph.add_edge('plan','research');graph.add_edge('research','write')
        graph.add_edge('write','validate_and_publish');graph.add_edge('validate_and_publish',END)
        heartbeat=asyncio.create_task(self.journal.heartbeat())
        graph_task=None
        try:
            with tracing_context(enabled=False):
                graph_task=asyncio.create_task(graph.compile().ainvoke(self.journal.state))
                done,_=await asyncio.wait({graph_task,heartbeat},timeout=self.journal.remaining,return_when=asyncio.FIRST_COMPLETED)
                if graph_task in done:return graph_task.result()['report']
                if heartbeat in done:
                    try:heartbeat.result()
                    except Conflict:
                        # 게시 트랜잭션 직후 그래프의 반환보다 heartbeat가 먼저
                        # 종료 상태를 읽을 수 있다. 같은 실행권/완료/게시본만 반환한다.
                        with self.store.connection() as conn:
                            execution=self.journal.owned(conn)
                            run,_=self.store._context(conn,run_id)
                            committed=execution['phase']=='complete' and run['status'] in {'finished','finished_with_gaps'}
                        report=self._existing(run_id,request_key) if committed else None
                        if report and report['status']=='published':return report
                        raise
                raise ExecutionDeadline('execution_deadline')
        except ExecutionDeadline:
            self.journal.end('finished_with_gaps','execution_deadline')
            with self.store.connection() as conn:
                record=conn.execute('SELECT * FROM research_executions WHERE run_id=? AND request_key=?',(run_id,request_key)).fetchone()
            return {'status':'limited','run_id':run_id,'publication_status':'not_published',
                    'limited_result':row_data(record)['state']['limited_result']}
        except NodeCancelledError as error:
            self.journal.end('interrupted','process_interrupted')
            raise asyncio.CancelledError() from error
        except BaseException as error:
            self.journal.end('interrupted' if isinstance(error,asyncio.CancelledError) else 'failed',
                             'process_interrupted' if isinstance(error,asyncio.CancelledError) else type(error).__name__)
            raise
        finally:
            for task in (graph_task,heartbeat):
                if task and not task.done():task.cancel()
            await asyncio.gather(*(t for t in (graph_task,heartbeat) if t),return_exceptions=True)

    def _publish(self,run_id,key,content,batch_id):
        context=content['context']
        if context.get('comparison_periods') and context.get('fiscal_end_month')!=12:
            if context.get('xbrl_report_gate',{}).get('status')!='pass':
                raise ValueError('independent_xbrl_period_account_amount_unconfirmed')
        data=safe_payload(content);content_hash=digest(data.encode())
        fact_ids={fid for row in content['confirmed_facts'] for fid in row['fact_ids']}
        with self.store.connection(write=True) as conn:
            run,basis=self.store._context(conn,run_id)
            if run['status']!='running':raise Conflict('중단되었거나 실행 중이 아닌 결과는 게시하지 않습니다.')
            self.journal.owned(conn)
            if time.monotonic()-self.journal.started>=self.journal.remaining:raise ExecutionDeadline('execution_deadline')
            old=conn.execute('SELECT id FROM research_reports WHERE research_id=? AND request_key=?',(basis['research_id'],key)).fetchone()
            if old:raise Conflict('같은 요청 키의 후보를 보존합니다. 다른 요청 키가 필요합니다.')
            for fid in fact_ids:
                fact=self.store._one(conn,'research_facts',fid)
                _,fact_basis=self.store._context(conn,fact['run_id'])
                if fact_basis['id']!=basis['id']:raise ValueError('report_evidence_basis_mismatch')
            version=conn.execute('SELECT COALESCE(MAX(version),0)+1 FROM research_reports WHERE research_id=?',(basis['research_id'],)).fetchone()[0]
            report_id=uuid4().hex
            conn.execute('INSERT INTO research_reports VALUES (?,?,?,?,?,?,?,?,?)',
                (report_id,basis['research_id'],run_id,version,key,content_hash,data,'building',now()))
            conn.executemany('INSERT INTO research_report_evidence VALUES (?,?)',[(report_id,fid) for fid in sorted(fact_ids)])
            verification={'numeric_batch_id':batch_id,'mapping_batch_id':content['context']['mapping_batch_id'],
                'fact_values_from_validation_only':True,'unknowns_preserved':True,'model_status':content['model_status']}
            conn.execute('INSERT INTO research_report_publications VALUES (?,?,?,?,?)',
                (report_id,batch_id,content_hash,safe_payload(verification),now()))
            conn.execute("UPDATE research_runs SET status='finished_with_gaps' WHERE id=?",(run_id,))
            conn.execute("UPDATE research_executions SET phase='complete',recovering=0,recovery_failures=0,model_pending=0,updated_at=? WHERE run_id=? AND request_key=?",(now(),run_id,key))
            self.store._event(conn,basis['research_id'],basis['id'],run_id,'report_published',
                {'report_id':report_id,'version':version,'financial_confirmation':'incomplete'})
        return self.store.get_report(report_id)


def required_questions(context):
    templates={
        'U_borrowing':('기준일별 차입 유형·유동성 대체·은행별 만기·담보 내역을 확인할 수 있습니까?',
                       '차입금 명세와 만기표·담보 내역','계정 합계 확인과 은행별 상환 일정 확인이 분리돼 있다.'),
        'U_working_capital':('신청 자금의 용도·집행 일정과 채권 회수·재고 보유 내역을 확인할 수 있습니까?',
                            '자금 집행 계획·채권 연령표·재고 명세','기타채권 포함 합계만으로 회수기간이나 소요 운전자금을 계산할 수 없다.'),
        'U_repayment':('영업 현금 유입과 대출 기간의 원리금 상환 계획을 확인할 수 있습니까?',
                      '자금수지 계획·거래처 회수 일정·상환 계획','공시의 과거 현금흐름만으로 신청 대출의 상환재원을 확정할 수 없다.'),
        'U_scope':('동일 연결 범위 조정과 공식 계정 버전 대응의 근거 자료를 확보할 수 있습니까?',
                  '연결 범위 변동·재작성 근거·적법하게 이용 가능한 공식 대응 자료','범위·버전 미확인 비교를 증감 설명의 근거로 사용할 수 없다.'),
    }
    supporting={'U_borrowing':['F_cash'],'U_working_capital':['F_receivables','F_inventory'],
        'U_repayment':['F_operating_cashflow','F_operating_profit'],'U_scope':[]}
    facts={e['evidence_id']:e for e in context.get('model_evidence',[]) if e['state']=='verified'}
    return [{'question':templates[u['evidence_id']][0],'requested_information':templates[u['evidence_id']][1],
             'reason':templates[u['evidence_id']][2],'evidence_ids':[u['evidence_id']],
             'observed_basis':[{'id':key,'text':facts[key]['text']} for key in supporting[u['evidence_id']] if key in facts],
             'answer_effect':{'U_borrowing':'은행별 만기·담보와 차주 상환 일정을 구분해 재검사한다.',
                 'U_working_capital':'채권/재고 구성과 집행·회수·지급 시점을 연결해 설명을 보완한다.',
                 'U_repayment':'공개 현금흐름과 실제 신청 기간의 상환 계획을 구분해 검토한다.',
                 'U_scope':'범위·버전이 같은 비교만 설명 근거로 채택할 수 있는지 다시 검사한다.'}[u['evidence_id']],
             'period_scope':context.get('public_cutoff'),'status':u.get('status','unresolved'),
             'received_source_ids':u.get('received_source_ids',[])}
            for u in context['unknowns'] if u.get('evidence_id') in templates]
