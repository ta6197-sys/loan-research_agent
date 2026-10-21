"""같은 실행의 단계/모델 시도/시간을 영속화한다. 재시작은 새 한도가 아니다."""
import asyncio
import fcntl
import os
from contextlib import contextmanager
import json
import math
import time
from uuid import uuid4
from agent.research_store import Conflict,now,row_data,safe_payload


class ExecutionDeadline(TimeoutError):
    pass


class ExecutionJournal:
    def __init__(self,store,run_id,key,conn):
        self.store=store;self.run_id=run_id;self.key=key;self.owner=uuid4().hex
        old=conn.execute('SELECT * FROM research_executions WHERE run_id=? AND request_key=?',(run_id,key)).fetchone()
        if old and old['phase'] in {'complete','limited'}:raise Conflict('종료된 실행 기록을 다시 생성할 수 없습니다.')
        if old:
            self.state=json.loads(old['state_json'])
            if old['model_pending']:
                self.state.setdefault('attempt_errors',[]).append({'attempt':old['model_attempts'],
                    'reason':'previous_model_result_uncertain','error_type':'Interrupted'})
            conn.execute('UPDATE research_executions SET owner_id=?,state_json=?,model_pending=0,updated_at=? WHERE run_id=? AND request_key=?',
                (self.owner,safe_payload(self.state),now(),run_id,key))
        else:
            self.state={}
            conn.execute('INSERT INTO research_executions VALUES (?,?,?,?,?,0,0,0,0,?)',
                (run_id,key,self.owner,'created','{}',now()))
        run=store._one(conn,'research_runs',run_id)
        self.base=json.loads(run['usage_json'])['execution_seconds'];self.started=time.monotonic()
        self.limit=json.loads(run['budget_json'])['execution_seconds']
        self.remaining=max(0,self.limit-self.base)

    def owned(self,conn):
        row=conn.execute('SELECT * FROM research_executions WHERE run_id=? AND request_key=?',(self.run_id,self.key)).fetchone()
        if not row or row['owner_id']!=self.owner:raise Conflict('이전 실행의 늦은 결과입니다.')
        return row

    def account(self,stage,*,reserve_model=False,progress=False,phase=None,updates=None,precharge=0):
        elapsed=math.ceil(time.monotonic()-self.started)
        expired=self.base+elapsed>=self.limit
        with self.store.connection(write=True) as conn:
            execution=self.owned(conn);run,basis=self.store._context(conn,self.run_id)
            if run['status']!='running':raise Conflict('실행이 중단되었거나 종료됐습니다.')
            usage=json.loads(run['usage_json']);budget=json.loads(run['budget_json'])
            usage['execution_seconds']=max(usage['execution_seconds'],min(self.limit,self.base+math.ceil(time.monotonic()-self.started+precharge)))
            if reserve_model:
                if expired:raise ExecutionDeadline('execution_deadline')
                if usage['model_calls']>=budget['model_calls']:raise ValueError('model_budget_exhausted')
                if execution['model_attempts']>=3:raise ValueError('model_attempts_exhausted')
                usage['model_calls']+=1
                conn.execute('UPDATE research_executions SET model_attempts=model_attempts+1,model_pending=1 WHERE run_id=? AND request_key=?',
                    (self.run_id,self.key))
            revision=run['revision']+1
            conn.execute('INSERT INTO research_snapshots VALUES (?,?,?,?,?,?,?)',
                (uuid4().hex,self.run_id,revision,stage,safe_payload({'workflow':'finance-durable-1.0'}),safe_payload(usage),now()))
            conn.execute('UPDATE research_runs SET usage_json=?,revision=? WHERE id=?',(safe_payload(usage),revision,self.run_id))
            state={**json.loads(execution['state_json']),**(updates or {})}
            conn.execute('UPDATE research_executions SET state_json=?,phase=?,model_pending=?,recovering=?,recovery_failures=?,updated_at=? '
                'WHERE run_id=? AND request_key=?',
                (safe_payload(state),phase or execution['phase'],1 if reserve_model else 0 if progress else execution['model_pending'],
                 0 if progress else execution['recovering'],0 if progress else execution['recovery_failures'],now(),self.run_id,self.key))
            self.state=state
            self.store._event(conn,basis['research_id'],basis['id'],self.run_id,'execution_checkpoint',{'stage':stage,'revision':revision})
        if expired:raise ExecutionDeadline('execution_deadline')

    def attempts(self):
        with self.store.connection() as conn:return self.owned(conn)['model_attempts']

    async def heartbeat(self):
        while True:
            self.account('execution_time_reserved',precharge=1)
            await asyncio.sleep(1)

    def end(self,status,reason):
        """중단/마감 처리도 소유자를 검사한다. 이미 게시된 판은 바꾸지 않는다."""
        with self.store.connection(write=True) as conn:
            execution=self.owned(conn);run,basis=self.store._context(conn,self.run_id)
            if execution['phase']=='complete':return
            usage=json.loads(run['usage_json'])
            usage['execution_seconds']=max(usage['execution_seconds'],min(self.limit,self.base+math.ceil(time.monotonic()-self.started)))
            phase=execution['phase'];state=json.loads(execution['state_json'])
            if status=='finished_with_gaps':
                phase='limited';state['limited_result']={'reason':reason,'publication_status':'not_published',
                    'saved_verified_context_available':bool(state.get('context')),
                    'numeric_batch_id':state.get('context',{}).get('numeric_batch_id'),
                    'limitation':'마감 전에 최종 검증·게시를 마치지 못했다. 이전 게시본과 저장된 근거를 유지한다.'}
            # 명시 중단은 취소/종료 처리보다 우선한다.
            final_status=run['status'] if run['status'] in {'stopped','stopping'} else status
            conn.execute('UPDATE research_runs SET status=?,usage_json=? WHERE id=?',(final_status,safe_payload(usage),self.run_id))
            conn.execute('UPDATE research_executions SET phase=?,state_json=?,updated_at=? WHERE run_id=? AND request_key=?',
                (phase,safe_payload(state),now(),self.run_id,self.key))
            self.store._event(conn,basis['research_id'],basis['id'],self.run_id,'execution_ended',{'reason':reason,'status':final_status})


@contextmanager
def worker_lease(store):
    fd=os.open(str(store.db_path)+'.research-worker.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    acquired=False
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);acquired=True
        except BlockingIOError:raise Conflict('같은 DB의 실행 관리자가 이미 실행 중입니다.') from None
        yield
    finally:
        if acquired:fcntl.flock(fd,fcntl.LOCK_UN)
        os.close(fd)
