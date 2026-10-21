"""답변·정정의 불변 접수, 입력 고정과 보관 사례 후속 실행. 재시작 복구는 별도다."""
import asyncio
import json
from typing import Literal
from uuid import uuid4

from pydantic import Field
from agent.research_models import Contract, Budget, SourceInput
from agent.research_store import Conflict, digest, now, row_data, safe_payload

ISSUE_SECTIONS = {
    'U_borrowing': {'borrowing'},
    'U_working_capital': {'purpose', 'cashflow'},
    'U_repayment': {'purpose', 'cashflow'},
    'U_scope': {'finance', 'disclosure'},
}


class FollowupInput(Contract):
    parent_report_id: str = Field(min_length=1, max_length=100)
    request_key: str = Field(min_length=1, max_length=200)
    kind: Literal['answer', 'correction', 'memo']
    text: str = Field(min_length=1, max_length=5000)
    question_ids: list[str] = Field(default_factory=list, max_length=20)
    reflect_in_research: bool = False
    attachment_bundle_id: str | None = Field(default=None,pattern=r'^[0-9a-f]{64}$')


def input_source(store, conn, input_id):
    rows = conn.execute("SELECT * FROM research_sources WHERE json_extract(metadata_json, '$.reporting_period.followup_input_id')=?",
                        (input_id,)).fetchall()
    if len(rows) != 1:
        raise Conflict('후속 입력 원문 저장이 완료되지 않았습니다.')
    source = row_data(rows[0])
    if source['metadata']['kind'] != 'user':
        raise ValueError('followup_source_kind_invalid')
    return source


def freeze_followup(store, conn, run, basis):
    """쓰기 트랜잭션 안에서 실행 시작 직전 입력을 고정한다."""
    trigger = json.loads(run['trigger_json'])
    if trigger.get('kind') != 'followup':
        return run['id']
    if trigger.get('pending_attachment_input_ids'):raise Conflict('첨부 묶음의 저장이 끝난 뒤 같은 입력으로 실행합니다.')
    if trigger.get('frozen'):
        return trigger['evidence_run_id']
    parent = conn.execute('SELECT p.* FROM research_reports p JOIN research_report_publications v ON p.id=v.report_id '
        'JOIN research_runs r ON r.id=p.run_id WHERE p.research_id=? AND r.basis_id=? ORDER BY p.version DESC LIMIT 1',
        (basis['research_id'], basis['id'])).fetchone()
    if not parent:
        raise Conflict('게시된 이전 판이 필요합니다.')
    parent = store.get_report(parent['id'])
    parent_context = parent['content']['context']
    ids = list(dict.fromkeys(parent_context.get('followup_input_ids', []) + trigger['input_ids']))
    source_ids = []
    affected = set()
    for iid in ids:
        source = input_source(store, conn, iid)
        _, source_basis = store._context(conn, source['run_id'])
        if source_basis['id'] != basis['id']:
            raise Conflict('다른 조사 기준의 답변을 혼합할 수 없습니다.')
        store.source_content(source['id'])
        source_ids.append(source['id'])
        if iid in trigger['input_ids']:
            issues = source['metadata']['reporting_period']['question_ids']
            affected.update(s for issue in issues for s in ISSUE_SECTIONS[issue])
            if not issues:
                from .report_context import SECTIONS
                affected.update(s for s, _ in SECTIONS)
    batch = store._one(conn, 'research_validation_batches', parent_context['numeric_batch_id'])
    _, evidence_basis = store._context(conn, batch['run_id'])
    if evidence_basis['id'] != basis['id']:
        raise Conflict('다른 기준의 수치 검증을 재사용할 수 없습니다.')
    trigger.update(frozen=True, followup_input_ids=ids, frozen_source_ids=source_ids,
        effective_parent_report_id=parent['id'], evidence_run_id=batch['run_id'], affected_sections=sorted(affected))
    conn.execute('UPDATE research_runs SET trigger_json=? WHERE id=?', (safe_payload(trigger), run['id']))
    store._event(conn, basis['research_id'], basis['id'], run['id'], 'followup_frozen', trigger)
    return batch['run_id']


class FollowupService:
    def __init__(self, store, model=None):
        self.store = store
        self.model = model
        self.lock = asyncio.Lock()
        self.tasks = set()

    def receive(self, research_id, value: FollowupInput):
        payload = value.model_dump(mode='json',exclude_none=True)
        if value.attachment_bundle_id:
            from .attachments import AttachmentService
            AttachmentService(self.store).get(value.attachment_bundle_id,research_id,value.parent_report_id)
        payload_hash = digest(safe_payload(payload).encode())
        content_hash = digest(safe_payload({k:v for k,v in payload.items() if k!='request_key'}).encode())
        parent = self.store.get_report(value.parent_report_id)
        if parent['research_id'] != research_id or parent['status'] != 'published':
            raise Conflict('같은 조사의 게시된 보고서를 선택해주세요.')
        allowed = {u.get('evidence_id') for u in parent['content']['context']['unknowns']}
        if not set(value.question_ids) <= (allowed & ISSUE_SECTIONS.keys()):
            raise ValueError('unknown_followup_question')
        active = value.kind != 'memo' or value.reflect_in_research
        with self.store.connection(write=True) as conn:
            parent_run, basis = self.store._context(conn, parent['run_id'])
            latest_basis = conn.execute('SELECT id FROM research_bases WHERE research_id=? ORDER BY version DESC LIMIT 1',
                                        (research_id,)).fetchone()['id']
            old = conn.execute("SELECT * FROM research_events WHERE research_id=? AND kind IN ('followup_received','followup_memo_saved','followup_duplicate') "
                "AND json_extract(payload_json,'$.request_key')=? ORDER BY sequence LIMIT 1", (research_id, value.request_key)).fetchone()
            if old:
                receipt = row_data(old)['payload']
                if receipt['input_hash'] != payload_hash:
                    raise Conflict('같은 요청 키로 다른 후속 내용을 보낼 수 없습니다.')
            else:
                duplicate=conn.execute("SELECT * FROM research_events WHERE research_id=? AND kind IN ('followup_received','followup_memo_saved') "
                    "AND json_extract(payload_json,'$.content_hash')=? ORDER BY sequence LIMIT 1", (research_id,content_hash)).fetchone()
                if duplicate:
                    receipt={**row_data(duplicate)['payload'],'request_key':value.request_key,'input_hash':payload_hash}
                    self.store._event(conn,research_id,basis['id'],receipt['run_id'],'followup_duplicate',receipt)
                else:
                    receipt=None
            if not old and receipt is None:
                if latest_basis != basis['id']:
                    raise Conflict('조사 기준이 바뀌었습니다. 최신 기준의 보고서에 답변해주세요.')
                run_id = None
                if active:
                    queued = conn.execute("SELECT * FROM research_runs WHERE basis_id=? AND status='queued' "
                        "AND json_extract(trigger_json,'$.kind')='followup' ORDER BY created_at,id LIMIT 1", (basis['id'],)).fetchone()
                    latest = conn.execute('SELECT * FROM research_runs WHERE basis_id=? ORDER BY created_at DESC,id DESC LIMIT 1',
                                          (basis['id'],)).fetchone()
                    from .research_lifecycle import paused
                    stopped = latest['status'] in {'stopped','stopping'} or paused(conn,research_id)
                    if queued and not stopped:
                        run_id = queued['id']
                    else:
                        run_id = uuid4().hex
                        self.store._insert_run(conn, run_id, basis['id'], latest['id'],
                            {'kind':'followup', 'input_ids':[]}, Budget(**json.loads(parent_run['budget_json'])))
                        if stopped:
                            conn.execute("UPDATE research_runs SET status='stopped' WHERE id=?", (run_id,))
                receipt = {'input_id':uuid4().hex, 'input_hash':payload_hash, 'content_hash':content_hash, **payload,
                    'run_id':run_id, 'received_at':now(), 'basis_id':basis['id']}
                self.store._event(conn, research_id, basis['id'], run_id,
                    'followup_received' if active else 'followup_memo_saved', receipt)
                if run_id:
                    run = self.store._one(conn, 'research_runs', run_id)
                    trigger = json.loads(run['trigger_json'])
                    trigger['input_ids'].append(receipt['input_id'])
                    if value.attachment_bundle_id:trigger.setdefault('pending_attachment_input_ids',[]).append(receipt['input_id'])
                    conn.execute('UPDATE research_runs SET trigger_json=? WHERE id=?', (safe_payload(trigger), run_id))
        # 중간 종료 후 재전송도 같은 접수/원문을 완성하며 새 실행·한도를 만들지 않는다.
        if receipt['run_id']:
            with self.store._source_lock(receipt['input_id'], blocking=True):
                self.store.recover_source_writes()
                with self.store.connection() as conn:
                    found = conn.execute("SELECT id FROM research_sources WHERE json_extract(metadata_json, '$.reporting_period.followup_input_id')=?",
                                         (receipt['input_id'],)).fetchone()
                if found:
                    source = self.store.get_source(found['id'])
                else:
                    source = self.store.save_source(receipt['run_id'], SourceInput(kind='user', title='후속 '+value.kind,
                        publisher='사용자', retrieved_at=receipt['received_at'], media_type='text/plain; charset=utf-8',
                        rights_status='user_authorized', reporting_period={'operation':'followup_input',
                            'followup_input_id':receipt['input_id'], 'question_ids':value.question_ids, 'kind':value.kind,
                            **({'attachment_bundle_id':value.attachment_bundle_id} if value.attachment_bundle_id else {})},
                        usage_restrictions=['user_provided_unverified', 'local_only']), value.text.encode())
                source_id = source['id']
            if value.attachment_bundle_id:
                from .attachments import AttachmentService
                AttachmentService(self.store).attach_to_run(value.attachment_bundle_id,research_id,value.parent_report_id,receipt['run_id'],receipt['input_id'])
                with self.store.connection(write=True) as conn:
                    run,basis=self.store._context(conn,receipt['run_id']);trigger=json.loads(run['trigger_json'])
                    pending=trigger.get('pending_attachment_input_ids',[])
                    if receipt['input_id'] in pending:
                        pending.remove(receipt['input_id']);trigger['pending_attachment_input_ids']=pending
                        conn.execute('UPDATE research_runs SET trigger_json=? WHERE id=?',(safe_payload(trigger),receipt['run_id']))
                        self.store._event(conn,research_id,basis['id'],receipt['run_id'],'followup_attachments_ready',{'input_id':receipt['input_id'],'bundle_id':value.attachment_bundle_id})
            status = self.store.get_run(receipt['run_id'])['status']
        else:
            source_id = None
            status = 'memo_saved'
        return {k:v for k,v in receipt.items() if k not in {'text','input_hash'}} | {'source_id':source_id, 'status':status}

    def stop(self, research_id):
        with self.store.connection(write=True) as conn:
            self.store.require_visible(conn,research_id)
            self.store._one(conn, 'research_researches', research_id)
            conn.execute("UPDATE research_runs SET status='stopped' WHERE basis_id IN "
                "(SELECT id FROM research_bases WHERE research_id=?) AND status IN ('queued','running','stopping','interrupted')", (research_id,))
            basis_id=conn.execute('SELECT id FROM research_bases WHERE research_id=? ORDER BY version DESC LIMIT 1',
                                  (research_id,)).fetchone()['id']
            self.store._event(conn, research_id, basis_id, None, 'research_stopped', {'pending_included':True})
        return {'status':'stopped', 'pending_included':True}

    def schedule(self):
        if getattr(self,'runtime',None):return self.runtime.schedule()
        task = asyncio.create_task(self.drain())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        # 오류 내용/사용자 텍스트를 외부 추적에 보내지 않는다. 실행 상태와 내부 이벤트를 보존한다.
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    async def drain(self):
        from .research_stage05 import Stage05Service
        async with self.lock:
            while True:
                with self.store.connection() as conn:
                    if conn.execute("SELECT 1 FROM research_runs WHERE status='running' LIMIT 1").fetchone():
                        return
                    run = conn.execute("SELECT id FROM research_runs WHERE status='queued' AND "
                        "json_extract(trigger_json,'$.kind')='followup' ORDER BY created_at,id LIMIT 1").fetchone()
                if not run:
                    return
                try:
                    await Stage05Service(self.store, self.model).run(run['id'], 'followup-'+run['id'])
                except Conflict:
                    return
                except Exception as error:
                    # 실패는 상태에 남긴다. 같은 입력을 무제한 자동 재실행하지 않는다.
                    with self.store.connection(write=True) as conn:
                        current,basis=self.store._context(conn,run['id'])
                        conn.execute("UPDATE research_runs SET status='failed' WHERE id=? AND status IN ('queued','running')",(run['id'],))
                        self.store._event(conn,basis['research_id'],basis['id'],run['id'],'followup_failed',
                                          {'error_type':type(error).__name__})
                    continue

    async def close(self):
        for task in self.tasks.copy():
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
