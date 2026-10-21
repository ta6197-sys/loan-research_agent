"""로컬 서버 내부의 저장형 실행 관리자. DB별 OS 잠금으로 복구 소유자를 제한한다."""
import asyncio
import fcntl
import json
import os
from uuid import uuid4

from agent.research_store import Conflict,now,safe_payload,row_data
from .research_stage05 import Stage05Service


class ResearchRuntime:
    def __init__(self,store,followups,model=None,case_library=None):
        self.store=store;self.followups=followups;self.model=model;self.case_library=case_library
        self.lock_fd=None;self.task=None;self.current=None;self.current_run=None
        self.wake=asyncio.Event();self.closing=False;self.active=False
        self.followups.runtime=self

    def start(self):
        path=str(self.store.db_path)+'.research-worker.lock'
        fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd);return False
        self.lock_fd=fd;self.active=True;self.followups.runtime=self
        self._recover()
        self.task=asyncio.create_task(self._loop());return True

    def _recover(self):
        with self.store.connection(write=True) as conn:
            rows=conn.execute("SELECT e.*,r.status FROM research_executions e JOIN research_runs r ON e.run_id=r.id "
                "WHERE r.status IN ('running','interrupted') AND e.phase NOT IN ('complete','limited') ORDER BY e.updated_at DESC").fetchall()
            seen=set()
            for row in rows:
                if row['run_id'] in seen:continue
                seen.add(row['run_id'])
                failures=row['recovery_failures']+row['recovering']
                run,basis=self.store._context(conn,row['run_id'])
                usage=json.loads(run['usage_json']);usage['recovery_failures']+=row['recovering']
                status='stopped' if failures>=2 else 'queued'
                conn.execute('UPDATE research_runs SET status=?,usage_json=? WHERE id=?',
                    (status,safe_payload(usage),row['run_id']))
                conn.execute('UPDATE research_executions SET recovering=1,recovery_failures=?,updated_at=? WHERE run_id=? AND request_key=?',
                    (failures,now(),row['run_id'],row['request_key']))
                self.store._event(conn,basis['research_id'],basis['id'],row['run_id'],
                    'recovery_blocked' if failures>=2 else 'recovery_queued',
                    {'request_key':row['request_key'],'consecutive_failures':failures,'usage_preserved':True})
            # 옛 실행은 재개 위치가 없으므로 자동으로 다시 모델을 호출하지 않는다.
            for row in conn.execute("SELECT r.* FROM research_runs r WHERE status='running' AND NOT EXISTS "
                '(SELECT 1 FROM research_executions e WHERE e.run_id=r.id)').fetchall():
                _,basis=self.store._context(conn,row['id'])
                conn.execute("UPDATE research_runs SET status='interrupted' WHERE id=?",(row['id'],))
                self.store._event(conn,basis['research_id'],basis['id'],row['id'],'recovery_unavailable',{'reason':'checkpoint_missing'})

        # 접수는 완료됐지만 원문 등록 직전에 종료된 입력도 같은 ID로 완성한다.
        with self.store.connection() as conn:
            inputs=conn.execute("SELECT e.research_id,e.payload_json FROM research_events e JOIN research_runs r ON e.run_id=r.id "
                "WHERE e.kind='followup_received' AND r.status IN ('queued','interrupted')").fetchall()
        from .research_followup import FollowupInput
        for item in inputs:
            payload=json.loads(item['payload_json'])
            try:self.followups.receive(item['research_id'],FollowupInput(**{k:payload[k] for k in FollowupInput.model_fields if k in payload}))
            except (ValueError,OSError):
                # 원문/권한 문제가 있는 입력은 실행하지 않고 저장 상태로 남긴다.
                continue

    def schedule(self):self.wake.set()

    def submit(self,run_id,key):
        with self.store.connection(write=True) as conn:
            run,basis=self.store._context(conn,run_id)
            old=conn.execute('SELECT id FROM research_reports WHERE run_id=? AND request_key=?',(run_id,key)).fetchone()
            if old:return self.store.get_report(old['id'])
            trigger=json.loads(run['trigger_json']);existing=trigger.get('job_request_key')
            if existing and existing!=key and run['status'] in {'queued','running','interrupted','stopped'}:
                raise Conflict('기존 실행 요청을 먼저 완료하거나 재개해주세요.')
            if run['status'] in {'stopped','stopping','interrupted'}:raise Conflict('중단된 실행은 재개 API를 사용해주세요.')
            if trigger.get('kind')=='followup':raise Conflict('후속 실행은 접수된 입력의 고정 요청 키를 사용합니다.')
            # 저장 전용 신규 기업을 전체 자동 조사로 잘못 시작하지 않는다.
            company=json.loads(self.store._one(conn,'research_researches',basis['research_id'])['company_json'])
            if company.get('corp_code')!='00129642' or json.loads(basis['data_json'])['public_cutoff']!='2026-10-20':
                raise ValueError('stage05_cached_case_only')
            if conn.execute('SELECT 1 FROM research_executions WHERE run_id=? AND request_key=? AND phase IN (\'complete\',\'limited\')',
                            (run_id,key)).fetchone():raise Conflict('종료된 실행입니다. 같은 요청의 한도를 초기화하지 않습니다.')
            trigger['job_request_key']=key
            conn.execute("UPDATE research_runs SET status='queued',trigger_json=? WHERE id=? AND status!='running'",
                (safe_payload(trigger),run_id))
            self.store._event(conn,basis['research_id'],basis['id'],run_id,'execution_queued',{'request_key':key})
        self.schedule();return {'run_id':run_id,'request_key':key,'status':self.store.get_run(run_id)['status']}

    async def stop(self,research_id):
        result=self.followups.stop(research_id)
        if self.current and self.current_run:
            research=self.store.get_research(research_id)
            if self.current_run in {r['id'] for r in research['runs']}:
                self.current.cancel();await asyncio.gather(self.current,return_exceptions=True)
        return result

    async def resume(self,research_id,key):
        # 같은 서버에서 이전 취소 처리가 끝나기 전에 같은 실행을 되살리지 않는다.
        if self.current and self.current_run:
            research=self.store.get_research(research_id)
            if self.current_run in {r['id'] for r in research['runs']} and not self.current.done():
                if self.store.get_run(self.current_run)['status'] in {'stopped','stopping'}:
                    self.current.cancel();await asyncio.gather(self.current,return_exceptions=True)
        with self.store.connection(write=True) as conn:
            self.store._one(conn,'research_researches',research_id)
            old=conn.execute("SELECT payload_json FROM research_events WHERE research_id=? AND kind='research_resumed' "
                "AND json_extract(payload_json,'$.request_key')=?",(research_id,key)).fetchone()
            if old:return json.loads(old['payload_json'])
            rows=conn.execute("SELECT r.* FROM research_runs r JOIN research_bases b ON r.basis_id=b.id WHERE b.research_id=? "
                "AND r.status IN ('stopped','interrupted') ORDER BY r.created_at,r.id",(research_id,)).fetchall()
            resumed=[]
            for row in rows:
                budget=json.loads(row['budget_json']);usage=json.loads(row['usage_json'])
                if usage['execution_seconds']>=budget['execution_seconds']:continue
                execution=conn.execute('SELECT * FROM research_executions WHERE run_id=? ORDER BY updated_at DESC LIMIT 1',(row['id'],)).fetchone()
                trigger=json.loads(row['trigger_json'])
                if execution and execution['phase'] in {'complete','limited'}:continue
                if not execution and usage['model_calls']>0:continue
                if not execution and trigger.get('kind')!='followup' and not trigger.get('job_request_key'):continue
                if execution:
                    conn.execute('UPDATE research_executions SET owner_id=? WHERE run_id=? AND request_key=?',(uuid4().hex,row['id'],execution['request_key']))
                conn.execute("UPDATE research_runs SET status='queued' WHERE id=?",(row['id'],));resumed.append(row['id'])
            from .research_lifecycle import paused
            if not resumed and not paused(conn,research_id):raise Conflict('남은 한도로 재개할 수 있는 저장 실행이 없습니다.')
            basis=conn.execute('SELECT id FROM research_bases WHERE research_id=? ORDER BY version DESC LIMIT 1',(research_id,)).fetchone()['id']
            result={'request_key':key,'run_ids':resumed,'status':'queued' if resumed else 'idle','usage_preserved':True}
            self.store._event(conn,research_id,basis,None,'research_resumed',result)
        self.schedule();return result

    def execution(self,run_id):
        with self.store.connection() as conn:
            self.store._context(conn,run_id)
            rows=[row_data(r) for r in conn.execute('SELECT * FROM research_executions WHERE run_id=? ORDER BY updated_at',(run_id,))]
        for row in rows:row.pop('owner_id',None)
        return {'run_id':run_id,'worker_active':self.active,'items':rows}

    async def _loop(self):
        while not self.closing:
            self.wake.clear()
            with self.store.connection() as conn:
                busy=conn.execute("SELECT 1 FROM research_runs WHERE status='running' LIMIT 1").fetchone()
                rows=[] if busy else conn.execute("SELECT r.* FROM research_runs r WHERE status='queued' AND "
                    "(json_extract(trigger_json,'$.kind')='followup' OR json_extract(trigger_json,'$.job_request_key') IS NOT NULL) "
                    "AND COALESCE(json_array_length(trigger_json,'$.pending_attachment_input_ids'),0)=0 "
                    'ORDER BY created_at,id').fetchall()
            if rows:
                row=rows[0];trigger=json.loads(row['trigger_json'])
                with self.store.connection() as conn:
                    execution=conn.execute("SELECT request_key FROM research_executions WHERE run_id=? AND phase NOT IN ('complete','limited') "
                        'ORDER BY updated_at DESC LIMIT 1',(row['id'],)).fetchone()
                key=trigger.get('job_request_key') or (execution['request_key'] if execution else 'followup-'+row['id'])
                self.current_run=row['id'];self.current=asyncio.create_task(Stage05Service(self.store,self.model,worker_owned=True,case_library=self.case_library).run(row['id'],key))
                try:await self.current
                except asyncio.CancelledError:
                    if self.closing:raise
                except Conflict:
                    pass
                except Exception as error:
                    with self.store.connection(write=True) as conn:
                        run,basis=self.store._context(conn,row['id'])
                        execution=conn.execute('SELECT * FROM research_executions WHERE run_id=? AND request_key=?',(row['id'],key)).fetchone()
                        if isinstance(error,OSError) and execution and run['status'] not in {'stopped','stopping'}:
                            failures=execution['recovery_failures']+execution['recovering']
                            usage=json.loads(run['usage_json']);usage['recovery_failures']+=execution['recovering']
                            status='stopped' if failures>=2 else 'queued'
                            conn.execute('UPDATE research_runs SET status=?,usage_json=? WHERE id=?',(status,safe_payload(usage),row['id']))
                            conn.execute('UPDATE research_executions SET recovering=1,recovery_failures=?,updated_at=? WHERE run_id=? AND request_key=?',
                                (failures,now(),row['id'],key))
                            kind='recovery_blocked' if failures>=2 else 'recovery_queued'
                        else:
                            conn.execute("UPDATE research_runs SET status='failed' WHERE id=? AND status IN ('queued','running')",(row['id'],))
                            kind='execution_failed'
                        self.store._event(conn,basis['research_id'],basis['id'],row['id'],kind,{'error_type':type(error).__name__})
                finally:self.current=None;self.current_run=None
                # 저장 미완료/실행 경합도 무한 즉시 반복하지 않는다.
            try:await asyncio.wait_for(self.wake.wait(),timeout=.2)
            except asyncio.TimeoutError:pass

    async def close(self):
        self.closing=True
        if self.current:self.current.cancel()
        if self.task:self.task.cancel()
        await asyncio.gather(*(t for t in (self.current,self.task) if t),return_exceptions=True)
        if self.lock_fd is not None:
            fcntl.flock(self.lock_fd,fcntl.LOCK_UN);os.close(self.lock_fd);self.lock_fd=None
        self.active=False
