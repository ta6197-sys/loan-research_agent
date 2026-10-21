"""계획 대기와 삭제 없는 보관/복원은 이전 판·사용량을 보존한다."""
import json
from types import SimpleNamespace
import unittest
from agent.finance.research_lifecycle import ResearchLifecycle,PlanInput
from agent.finance.research_runtime import ResearchRuntime
from agent.research_store import ResearchStore,Conflict,NotFound
from tests import test_research_followup as fixtures


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.FollowupTests.asyncSetUp(self)
        self.library=SimpleNamespace(verify=lambda:None,manifest={'case_id':'synthetic-case'},fingerprint='a'*64)
        self.lifecycle=ResearchLifecycle(self.store,self.library)
        self.runtime=ResearchRuntime(self.store,self.service,self.model)
        self.addAsyncCleanup(self.runtime.close)

    def plan(self,key='plan-1',**changes):
        return PlanInput(**({'request_key':key,'expected_basis_version':1,'purpose':'합성 변경 목적','focus_sections':['borrowing']}|changes))

    async def test_new_plan_is_queued_idempotent_and_keeps_previous_execution(self):
        before=self.store.get_run(self.run)
        receipt=self.lifecycle.change_plan(self.rid,self.plan())
        self.assertEqual('queued',receipt['status']);self.assertEqual(2,receipt['basis_version'])
        self.assertEqual(receipt,self.lifecycle.change_plan(self.rid,self.plan()))
        self.assertEqual(before,self.store.get_run(self.run))
        self.assertEqual(self.first,self.store.get_report(self.first['id']))
        new=self.store.get_run(receipt['run_id']);self.assertEqual(before['budget'],new['budget'])
        self.assertEqual(['borrowing'],new['trigger']['focus_sections'])
        with self.assertRaises(Conflict):self.lifecycle.change_plan(self.rid,self.plan(purpose='다른 내용'))
        with self.assertRaises(Conflict):self.lifecycle.change_plan(self.rid,self.plan('stale'))

    async def test_same_plan_does_not_create_new_budget_and_stopped_plan_waits(self):
        purpose=self.store.get_research(self.rid)['bases'][-1]['data']['purpose']
        same=self.plan('same-plan',purpose=purpose,focus_sections=[])
        receipt=self.lifecycle.change_plan(self.rid,same)
        self.assertEqual('unchanged',receipt['status']);self.assertEqual(1,len(self.store.get_research(self.rid)['runs']))
        await self.runtime.stop(self.rid)
        changed=self.lifecycle.change_plan(self.rid,self.plan())
        self.assertEqual('stopped',changed['status'])
        self.assertTrue(self.store.get_research(self.rid)['paused'])
        self.assertEqual(receipt,self.lifecycle.change_plan(self.rid,same))
        with self.assertRaises(Conflict):self.lifecycle.change_plan(self.rid,self.plan('same-plan'))

    async def test_archive_restart_restore_preserves_files_reports_and_pause(self):
        files={str(p):p.read_bytes() for p in self.store.source_root.rglob('*') if p.is_file()}
        before=self.store.get_run(self.run)['usage']
        await self.lifecycle.archive(self.rid,self.runtime)
        reopened=ResearchStore(self.store.db_path,self.store.source_root)
        self.assertEqual([],reopened.list_researches())
        self.assertEqual(self.rid,ResearchLifecycle(reopened).archived()[0]['id'])
        for get in (lambda:reopened.get_research(self.rid),lambda:reopened.get_report(self.first['id']),lambda:reopened.events(self.rid)):
            with self.assertRaises(NotFound):get()
        with self.assertRaises(NotFound):await self.runtime.resume(self.rid,'hidden-resume')
        with self.assertRaises(NotFound):await self.runtime.stop(self.rid)
        self.lifecycle.restore(self.rid)
        self.assertEqual(self.first,reopened.get_report(self.first['id']))
        self.assertEqual(before,reopened.get_run(self.run)['usage'])
        self.assertEqual(files,{str(p):p.read_bytes() for p in reopened.source_root.rglob('*') if p.is_file()})
        self.assertTrue(reopened.get_research(self.rid)['paused'])
        receipt=await self.runtime.resume(self.rid,'resume-idle')
        self.assertEqual([],receipt['run_ids']);self.assertFalse(reopened.get_research(self.rid)['paused'])

    async def test_restored_new_answer_stays_stopped_until_resume(self):
        await self.lifecycle.archive(self.rid,self.runtime);self.lifecycle.restore(self.rid)
        receipt=self.service.receive(self.rid,fixtures.FollowupTests.value(self))
        self.assertEqual('stopped',receipt['status']);self.assertEqual(1,self.model.calls)
        self.assertEqual(1,len(self.store.get_research(self.rid)['reports']))
