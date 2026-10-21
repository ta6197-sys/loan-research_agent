"""새 계획의 불변 대기와 보관/복원. 원문·보고서·사용량을 삭제하지 않는다."""
import json
from uuid import uuid4
from pydantic import Field,field_validator
from agent.research_models import Contract,BasisInput,Budget
from agent.research_store import Conflict,digest,safe_payload,row_data,now


def paused(conn,research_id):
    row=conn.execute("SELECT kind FROM research_events WHERE research_id=? AND kind IN ('research_stopped','research_resumed') ORDER BY sequence DESC LIMIT 1",(research_id,)).fetchone()
    return bool(row and row['kind']=='research_stopped')


class PlanInput(Contract):
    request_key:str=Field(min_length=1,max_length=200)
    expected_basis_version:int=Field(ge=1)
    purpose:str=Field(min_length=1,max_length=2000)
    focus_sections:list[str]=Field(default_factory=list,max_length=7)

    @field_validator('focus_sections')
    @classmethod
    def known_sections(cls,value):
        from .report_context import SECTIONS
        if len(value)!=len(set(value)) or not set(value)<={s for s,_ in SECTIONS}:raise ValueError('unknown_or_duplicate_focus_section')
        return value


class ResearchLifecycle:
    def __init__(self,store,library=None):self.store=store;self.library=library

    def change_plan(self,research_id,value):
        if self.library is None:raise Conflict('현재 기준을 지원하는 보관 자료 목록이 필요합니다.')
        self.library.verify();payload=value.model_dump();hashed=digest(safe_payload(payload).encode())
        with self.store.connection(write=True) as conn:
            self.store.require_visible(conn,research_id);research=self.store._one(conn,'research_researches',research_id)
            old=conn.execute("SELECT payload_json FROM research_events WHERE research_id=? AND kind='plan_change_received' AND json_extract(payload_json,'$.request_key')=?",(research_id,value.request_key)).fetchone()
            if old:
                receipt=json.loads(old['payload_json'])
                if receipt['payload_hash']!=hashed:raise Conflict('같은 계획 요청 키로 다른 내용을 전송했습니다.')
                return receipt
            basis=conn.execute('SELECT * FROM research_bases WHERE research_id=? ORDER BY version DESC LIMIT 1',(research_id,)).fetchone()
            if basis['version']!=value.expected_basis_version:raise Conflict('계획 기준이 변경됐습니다. 최신 기준을 확인해주세요.')
            data=json.loads(basis['data_json']);company=json.loads(research['company_json'])
            if company.get('corp_code')!='00129642' or data['public_cutoff']!='2026-10-20':raise Conflict('다른 기업/기준일은 새 자료 수집 범위를 먼저 정해야 합니다.')
            latest=conn.execute('SELECT r.* FROM research_runs r JOIN research_bases b ON r.basis_id=b.id WHERE b.research_id=? ORDER BY r.created_at DESC,r.id DESC LIMIT 1',(research_id,)).fetchone()
            if data['purpose']==value.purpose and json.loads(latest['trigger_json']).get('focus_sections',[])==value.focus_sections:
                receipt={'request_key':value.request_key,'payload_hash':hashed,'status':'unchanged','run_id':latest['id'],'basis_id':basis['id'],'basis_version':basis['version']}
                self.store._event(conn,research_id,basis['id'],latest['id'],'plan_change_received',receipt)
                return receipt
            data['purpose']=value.purpose;data=BasisInput.model_validate(data).model_dump(mode='json')
            bid=uuid4().hex;rid=uuid4().hex;version=basis['version']+1
            conn.execute('INSERT INTO research_bases VALUES (?,?,?,?,?)',(bid,research_id,version,safe_payload(data),now()))
            trigger={'kind':'cached_case','case_id':self.library.manifest['case_id'],'case_manifest_hash':self.library.fingerprint,
                'job_request_key':'plan-'+value.request_key,'focus_sections':value.focus_sections,'plan_previous_basis_id':basis['id']}
            self.store._insert_run(conn,rid,bid,latest['id'],trigger,Budget(**json.loads(latest['budget_json'])))
            status='stopped' if paused(conn,research_id) or latest['status'] in {'stopped','stopping'} else 'queued'
            conn.execute('UPDATE research_runs SET status=? WHERE id=?',(status,rid))
            receipt={'request_key':value.request_key,'payload_hash':hashed,'basis_id':bid,'basis_version':version,'run_id':rid,
                'status':status,'focus_sections':value.focus_sections,'current_execution_unchanged':True,'previous_versions_preserved':True}
            self.store._event(conn,research_id,bid,rid,'plan_change_received',receipt)
            return receipt

    async def archive(self,research_id,runtime):
        with self.store.connection() as conn:
            self.store._one(conn,'research_researches',research_id)
            if self.store.is_archived(conn,research_id):return {'research_id':research_id,'status':'archived','files_preserved':True}
        await runtime.stop(research_id)
        with self.store.connection(write=True) as conn:
            basis=conn.execute('SELECT id FROM research_bases WHERE research_id=? ORDER BY version DESC LIMIT 1',(research_id,)).fetchone()
            self.store._event(conn,research_id,basis['id'],None,'research_archived',{'files_preserved':True,'automatic_resume':False})
        return {'research_id':research_id,'status':'archived','files_preserved':True}

    def archived(self):
        with self.store.connection() as conn:
            return [row_data(r) for r in conn.execute('SELECT * FROM research_researches ORDER BY created_at,id') if self.store.is_archived(conn,r['id'])]

    def restore(self,research_id):
        with self.store.connection(write=True) as conn:
            self.store._one(conn,'research_researches',research_id)
            if self.store.is_archived(conn,research_id):
                basis=conn.execute('SELECT id FROM research_bases WHERE research_id=? ORDER BY version DESC LIMIT 1',(research_id,)).fetchone()
                self.store._event(conn,research_id,basis['id'],None,'research_restored',{'automatic_resume':False,'files_preserved':True})
        return {'research_id':research_id,'status':'restored','automatic_resume':False}
