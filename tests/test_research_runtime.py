"""저장형 실행 복구·동일 예산·중복 게시 방지·마감의 합성 시험."""
import asyncio
import json
import unittest
from unittest.mock import patch

from agent.finance.research_runtime import ResearchRuntime
from agent.finance.research_stage05 import Stage05Service
from agent.finance.research_followup import FollowupService
from agent.research_store import Conflict
from agent.finance.evaluation import FixedSequenceGraph
from agent.finance.execution_journal import ExecutionJournal
from tests import test_research_followup as followup_tests


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await followup_tests.FollowupTests.asyncSetUp(self)

    def receive(self,*args,**kwargs):return followup_tests.FollowupTests.receive(self,*args,**kwargs)
    def value(self,*args,**kwargs):return followup_tests.FollowupTests.value(self,*args,**kwargs)

    async def wait(self,predicate):
        for _ in range(300):
            if predicate():return
            await asyncio.sleep(.01)
        self.fail('상태 전환 시간 초과')

    def runtime(self,model=None):
        runtime=ResearchRuntime(self.store,self.service,model or self.model)
        self.addAsyncCleanup(runtime.close);return runtime

    async def test_one_owner_and_direct_execution_cannot_bypass_worker_lock(self):
        first=self.runtime();second=self.runtime()
        self.assertTrue(first.start());self.assertFalse(second.start())
        with self.assertRaises(Conflict):await Stage05Service(self.store,self.model).run(self.run,'bypass')
        await first.close();self.assertTrue(second.start())

    async def test_stop_resume_preserves_input_calls_and_old_reports(self):
        self.model.entered=asyncio.Event();self.model.release=asyncio.Event()
        receipt=self.receive();runtime=self.runtime();runtime.start()
        await asyncio.wait_for(self.model.entered.wait(),3)
        await runtime.stop(self.rid)
        stopped=self.store.get_run(receipt['run_id']);before=stopped['usage'].copy()
        self.assertEqual('stopped',stopped['status']);self.assertEqual(1,before['model_calls'])
        frozen=stopped['trigger'].copy();self.model.release.set()
        response=await runtime.resume(self.rid,'resume-one')
        self.assertEqual([receipt['run_id']],response['run_ids'])
        await self.wait(lambda:len(self.store.get_research(self.rid)['reports'])==2)
        completed=self.store.get_run(receipt['run_id'])
        self.assertEqual(frozen,completed['trigger']);self.assertEqual(2,completed['usage']['model_calls'])
        self.assertGreaterEqual(completed['usage']['execution_seconds'],before['execution_seconds'])
        self.assertEqual(self.first,self.store.get_report(self.first['id']))
        # 재개 명령 재전송은 완료된 실행·한도를 다시 만들지 않는다.
        self.assertEqual(response,await runtime.resume(self.rid,'resume-one'))
        self.assertEqual(2,len(self.store.get_research(self.rid)['reports']))

    async def test_restart_recovers_interrupted_without_new_budget(self):
        self.model.entered=asyncio.Event();self.model.release=asyncio.Event()
        receipt=self.receive();first=self.runtime();first.start()
        await asyncio.wait_for(self.model.entered.wait(),3)
        await first.close()
        old=self.store.get_run(receipt['run_id']);self.assertEqual('interrupted',old['status'])
        self.model.release.set();second=self.runtime();second.start()
        await self.wait(lambda:len(self.store.get_research(self.rid)['reports'])==2)
        new=self.store.get_run(receipt['run_id'])
        self.assertEqual(old['budget'],new['budget']);self.assertEqual(2,new['usage']['model_calls'])
        self.assertEqual(2,len(self.store.get_research(self.rid)['runs']))
        record=second.execution(receipt['run_id'])['items'][0]
        self.assertEqual('complete',record['phase']);self.assertEqual(2,record['model_attempts'])
        self.assertNotIn('owner_id',record)

    async def test_explicit_stop_is_not_resumed_on_restart_or_new_input(self):
        receipt=self.receive();first=self.runtime();first.start()
        await first.stop(self.rid);await first.close()
        second=self.runtime();second.start();await asyncio.sleep(.05)
        self.assertEqual('stopped',self.store.get_run(receipt['run_id'])['status'])
        later=self.receive('later',text='새 합성 답변입니다.');second.schedule();await asyncio.sleep(.05)
        self.assertEqual('stopped',self.store.get_run(later['run_id'])['status'])
        self.assertEqual(1,len(self.store.get_research(self.rid)['reports']))

    async def test_two_consecutive_failed_recoveries_are_durable_and_blocked(self):
        self.model.entered=asyncio.Event();self.model.release=asyncio.Event()
        receipt=self.receive();first=self.runtime();first.start()
        await asyncio.wait_for(self.model.entered.wait(),3);await first.close()
        with self.store.connection(write=True) as conn:
            conn.execute('UPDATE research_executions SET recovering=1 WHERE run_id=?',(receipt['run_id'],))
        second=self.runtime();second._recover()
        with self.store.connection(write=True) as conn:
            conn.execute("UPDATE research_runs SET status='interrupted' WHERE id=?",(receipt['run_id'],))
        second._recover();second._recover()
        run=self.store.get_run(receipt['run_id']);self.assertEqual('stopped',run['status'])
        self.assertEqual(2,run['usage']['recovery_failures']);self.assertEqual(1,run['usage']['model_calls'])
        self.assertEqual(2,second.execution(receipt['run_id'])['items'][0]['recovery_failures'])

    async def test_saved_draft_is_reused_after_publish_interruption(self):
        receipt=self.receive()
        with patch.object(Stage05Service,'_publish',side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                await Stage05Service(self.store,self.model).run(receipt['run_id'],'followup-'+receipt['run_id'])
        self.assertEqual(2,self.model.calls)
        runtime=self.runtime();runtime.start()
        await self.wait(lambda:len(self.store.get_research(self.rid)['reports'])==2)
        self.assertEqual(2,self.model.calls)
        self.assertEqual(1,self.store.get_run(receipt['run_id'])['usage']['model_calls'])
        await runtime.close();runtime2=self.runtime();runtime2.start();await asyncio.sleep(.05)
        self.assertEqual(2,len(self.store.get_research(self.rid)['reports']))

    async def test_post_publication_heartbeat_race_returns_committed_report_once(self):
        receipt=self.receive();published=asyncio.Event();original=Stage05Service._publish
        class SlowGraphFinish(FixedSequenceGraph):
            async def ainvoke(self,state):
                result=await super().ainvoke(state)
                await asyncio.Event().wait()
                return result
        def commit(service,*args):
            result=original(service,*args);published.set();return result
        async def heartbeat(journal):
            await published.wait();journal.account('post_publication_race')
        key='followup-'+receipt['run_id'];service=Stage05Service(self.store,self.model)
        with patch('agent.finance.research_stage05.StateGraph',SlowGraphFinish),patch.object(Stage05Service,'_publish',commit),patch.object(ExecutionJournal,'heartbeat',heartbeat):
            report=await asyncio.wait_for(service.run(receipt['run_id'],key),3)
        self.assertEqual('published',report['status']);self.assertEqual(2,report['version'])
        before=self.store.get_run(receipt['run_id'])['usage']
        self.assertEqual(1,before['model_calls']);self.assertEqual(2,self.model.calls)
        self.assertEqual(report,await Stage05Service(self.store,self.model).run(receipt['run_id'],key))
        self.assertEqual(before,self.store.get_run(receipt['run_id'])['usage'])
        self.assertEqual(2,len(self.store.get_research(self.rid)['reports']))

    async def test_post_publication_race_does_not_hide_different_execution_owner(self):
        receipt=self.receive();published=asyncio.Event();original=Stage05Service._publish
        class SlowGraphFinish(FixedSequenceGraph):
            async def ainvoke(self,state):
                await super().ainvoke(state);await asyncio.Event().wait()
        def commit(service,*args):
            result=original(service,*args);published.set();return result
        async def heartbeat(journal):
            await published.wait()
            with self.store.connection(write=True) as conn:
                conn.execute("UPDATE research_executions SET owner_id='different-worker' WHERE run_id=?",(receipt['run_id'],))
            journal.account('post_publication_race')
        key='followup-'+receipt['run_id']
        with patch('agent.finance.research_stage05.StateGraph',SlowGraphFinish),patch.object(Stage05Service,'_publish',commit),patch.object(ExecutionJournal,'heartbeat',heartbeat):
            with self.assertRaises(Conflict):await asyncio.wait_for(Stage05Service(self.store,self.model).run(receipt['run_id'],key),3)
        self.assertEqual(2,len(self.store.get_research(self.rid)['reports']))

    async def test_model_attempt_limit_survives_repeated_interruptions(self):
        receipt=self.receive()
        class InterruptingModel:
            async def draft(self,evidence,*,before_request,**kwargs):
                await before_request();raise asyncio.CancelledError()
        key='followup-'+receipt['run_id']
        for _ in range(3):
            with self.store.connection(write=True) as conn:
                conn.execute("UPDATE research_runs SET status='queued' WHERE id=?",(receipt['run_id'],))
            with self.assertRaises(asyncio.CancelledError):
                await Stage05Service(self.store,InterruptingModel()).run(receipt['run_id'],key)
        before=self.model.calls
        with self.store.connection(write=True) as conn:
            conn.execute("UPDATE research_runs SET status='queued' WHERE id=?",(receipt['run_id'],))
        report=await Stage05Service(self.store,self.model).run(receipt['run_id'],key)
        self.assertEqual(before,self.model.calls)
        self.assertEqual('unavailable_or_rejected',report['content']['model_status'])
        self.assertEqual(3,self.store.get_run(receipt['run_id'])['usage']['model_calls'])

    async def test_hard_deadline_cancels_stalled_model_and_keeps_unpublished_limit(self):
        receipt=self.receive()
        with self.store.connection(write=True) as conn:
            budget=self.store.get_run(receipt['run_id'])['budget']
            budget.update(execution_seconds=2,writing_reserve_seconds=0)
            conn.execute('UPDATE research_runs SET budget_json=? WHERE id=?',(json.dumps(budget),receipt['run_id']))
        self.model.entered=asyncio.Event();self.model.release=asyncio.Event()
        result=await asyncio.wait_for(Stage05Service(self.store,self.model).run(receipt['run_id'],'followup-'+receipt['run_id']),3)
        self.assertEqual('limited',result['status']);self.assertEqual('not_published',result['publication_status'])
        run=self.store.get_run(receipt['run_id']);self.assertEqual(2,run['usage']['execution_seconds'])
        self.assertEqual('finished_with_gaps',run['status']);self.assertEqual(1,len(self.store.get_research(self.rid)['reports']))
        runtime=self.runtime()
        with self.assertRaises(Conflict):await runtime.resume(self.rid,'exhausted')

    async def test_no_checkpoint_legacy_run_is_not_automatically_reexecuted(self):
        with self.store.connection(write=True) as conn:
            conn.execute('DELETE FROM research_executions')
            conn.execute("UPDATE research_runs SET status='running' WHERE id=?",(self.run,))
        runtime=self.runtime();runtime.start();await asyncio.sleep(.05)
        self.assertEqual('interrupted',self.store.get_run(self.run)['status'])
        self.assertEqual(1,self.model.calls)
        with self.assertRaises(Conflict):await runtime.resume(self.rid,'legacy')

    async def test_transient_recovery_failure_stops_after_two_without_budget_reset(self):
        receipt=self.receive();runtime=self.runtime()
        with patch('agent.finance.research_stage05.NumericValidationService.validate_run',side_effect=OSError('synthetic temporary failure')) as failing:
            runtime.start()
            await self.wait(lambda:self.store.get_run(receipt['run_id'])['status']=='stopped')
        self.assertEqual(3,failing.call_count)
        run=self.store.get_run(receipt['run_id'])
        self.assertEqual(2,run['usage']['recovery_failures'])
        self.assertEqual(0,run['usage']['model_calls'])

    async def test_startup_finishes_received_input_missing_its_source(self):
        with patch.object(self.store,'save_source',side_effect=OSError('synthetic write interruption')):
            with self.assertRaises(OSError):self.receive()
        runtime=self.runtime();runtime.start()
        await self.wait(lambda:len(self.store.get_research(self.rid)['reports'])==2)
        self.assertEqual(2,len(self.store.get_research(self.rid)['runs']))
        with self.store.connection() as conn:
            self.assertEqual(1,conn.execute("SELECT COUNT(*) FROM research_sources WHERE json_extract(metadata_json,'$.kind')='user'").fetchone()[0])

    async def test_resume_from_other_server_fences_previous_owner_late_result(self):
        self.model.entered=asyncio.Event();self.model.release=asyncio.Event()
        receipt=self.receive();first=self.runtime();first.start()
        await asyncio.wait_for(self.model.entered.wait(),3)
        second=self.runtime();self.assertFalse(second.start())
        await second.stop(self.rid);await second.resume(self.rid,'other-server-resume')
        self.model.release.set()
        await self.wait(lambda:len(self.store.get_research(self.rid)['reports'])==2)
        self.assertEqual(2,self.store.get_run(receipt['run_id'])['usage']['model_calls'])
        self.assertEqual(2,len(self.store.get_research(self.rid)['reports']))
