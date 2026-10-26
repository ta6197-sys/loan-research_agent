"""일반 기업 조사 진입은 외부 요청 없이도 불변 계획을 저장해야 한다."""
from pathlib import Path
import tempfile
import unittest

from agent.finance.general_company import GeneralStartInput, create_collection_retry, receive
from agent.finance.general_collection import (GeneralCollectionService, changed_regular_reports,
    planned_reports, select_regular_reports, comparison_targets, fiscal_month_status)
from agent.finance.report_context import prepare_context
from agent.research_store import Conflict, ResearchStore


class GeneralCompanyStartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.store = ResearchStore(root / "session.db", root / "sources")

    def value(self, **changes):
        return GeneralStartInput(**({
            "company_name": "SK", "corp_code": "00181712", "stock_code": "034730",
            "request_key": "sk-general-start", "public_cutoff":"2026-10-22",
        } | changes))

    def test_general_company_start_stores_isolated_collection_plan(self):
        receipt = receive(self.store, self.value())
        run = self.store.get_run(receipt["run_id"])
        self.assertEqual("collection_plan_pending", receipt["status"])
        self.assertEqual("general_company", run["trigger"]["kind"])
        self.assertEqual("identity_pending", run["trigger"]["phase"])
        self.assertNotIn("job_request_key", run["trigger"])
        self.assertEqual(0, run["usage"]["external_requests"])
        self.assertEqual("00181712", receipt["company"]["corp_code"])
        self.assertEqual("2026-10-22", receipt["public_cutoff"])
        self.assertEqual('2023-01-01',receipt['collection_plan']['disclosures_begin'])

    def test_same_request_is_idempotent_but_different_company_conflicts(self):
        first = receive(self.store, self.value())
        self.assertEqual(first, receive(self.store, self.value()))
        with self.assertRaises(Conflict):
            receive(self.store, self.value(company_name="다른 법인", corp_code="00144155"))

    def test_initial_budget_cannot_exceed_general_company_policy(self):
        with self.assertRaises(ValueError):
            self.value(budget={"external_requests": 61})
        with self.assertRaises(ValueError):
            self.value(budget={"model_calls": 21})
        self.assertEqual([], self.store.list_researches())

    def test_regular_report_selection_keeps_only_window_and_latest_receipt(self):
        rows = [
            {"report_nm": "사업보고서 (2023.12)", "rcept_no": "20240301000001", "rcept_dt": "20240301"},
            {"report_nm": "사업보고서 (2023.12) [정정]", "rcept_no": "20240302000001", "rcept_dt": "20240302"},
            {"report_nm": "사업보고서 (2025.12)", "rcept_no": "20260301000001", "rcept_dt": "20260301"},
            {"report_nm": "반기보고서 (2025.06)", "rcept_no": "20250814000001", "rcept_dt": "20250814"},
            {"report_nm": "반기보고서 (2026.06)", "rcept_no": "20260814000001", "rcept_dt": "20260814"},
            {"report_nm": "분기보고서 (2026.03)", "rcept_no": "20260514000001", "rcept_dt": "20260514"},
            {"report_nm": "사업보고서 (2026.12)", "rcept_no": "20270301000001", "rcept_dt": "20270301"},
        ]
        selected = select_regular_reports(rows, "2026-10-22")
        self.assertEqual([("2023", "11011", "20240302000001"), ("2025", "11011", "20260301000001"),
                          ("2025", "11012", "20250814000001"), ("2026", "11012", "20260814000001")],
                         [(str(row["year"]), row["report_code"], row["rcept_no"]) for row in selected])

    def test_collection_plan_fills_disclosure_list_gaps_with_comparison_window(self):
        targets = planned_reports([{"report_nm": "반기보고서 (2026.06)", "rcept_no": "20260814000001", "rcept_dt": "20260814"}], "2026-10-22")
        self.assertEqual([(2023, "11011", None), (2024, "11011", None), (2025, "11011", None),
                          (2025, "11012", None), (2026, "11012", "20260814000001")],
                         [(item["year"], item["report_code"], item["rcept_no"]) for item in targets])

    def test_comparison_window_moves_with_public_cutoff(self):
        targets=planned_reports([],"2028-03-01")
        self.assertEqual([(2025,"11011"),(2026,"11011"),(2027,"11011"),(2026,"11012"),(2027,"11012")],
                         [(item['year'],item['report_code']) for item in targets])
        receipt=receive(self.store,self.value(public_cutoff="2028-03-01"))
        basis=self.store.get_research(receipt['research_id'])['bases'][0]
        self.assertEqual({'annual_years':[2025,2026,2027],'interim_periods':['2026-H1','2027-H1'],
                          'fiscal_month_status':'pending_company_identity'},basis['data']['comparison_window'])
        self.assertEqual('2025-01-01',receipt['collection_plan']['disclosures_begin'])

    def test_non_calendar_targets_follow_verified_fiscal_month(self):
        self.assertEqual([(2024,'11011'),(2025,'11011'),(2026,'11011'),(2025,'11012'),(2026,'11012')],
                         comparison_targets('2026-10-25',3))
        self.assertEqual([(2024,'11011'),(2025,'11011'),(2026,'11011'),(2024,'11012'),(2025,'11012')],
                         comparison_targets('2026-10-25',6))
        rows=[{'report_nm':'사업보고서 (2026.03)','rcept_no':'20260630000001'},
              {'report_nm':'반기보고서 (2026.09)','rcept_no':'20261020000001'},
              {'report_nm':'사업보고서 (2026.12)','rcept_no':'20261021000001'}]
        selected=select_regular_reports(rows,'2026-10-25',3)
        self.assertEqual([(2026,'11011'),(2026,'11012')],[(r['year'],r['report_code']) for r in selected])

    def test_fiscal_month_requires_company_and_report_agreement(self):
        rows=[{'report_nm':'사업보고서 (2025.03)','rcept_no':'20250630000001'}]
        self.assertEqual('supported',fiscal_month_status({'company':{'acc_mt':'03'}},rows,'2026-10-25')['status'])
        self.assertEqual('unverified',fiscal_month_status({'company':{}},rows,'2026-10-25')['status'])
        self.assertEqual('conflict',fiscal_month_status({'company':{'acc_mt':'12'}},rows,'2026-10-25')['status'])

    def test_freshness_check_only_returns_new_or_corrected_receipts(self):
        rows=[{"report_nm":"사업보고서 (2025.12)","rcept_no":"20260318000001","rcept_dt":"20260318"},
              {"report_nm":"반기보고서 (2026.06)","rcept_no":"20260814000001","rcept_dt":"20260814"}]
        self.assertEqual([],changed_regular_reports({"20260318000001","20260814000001"},rows,"2026-10-22"))
        changed=changed_regular_reports({"20260318000001"},rows,"2026-10-22")
        self.assertEqual(["20260814000001"],[item["rcept_no"] for item in changed])

    def test_retry_creates_new_run_without_changing_previous_collection(self):
        first = receive(self.store, self.value())
        retry = create_collection_retry(self.store, first["research_id"])
        research = self.store.get_research(first["research_id"])
        self.assertEqual(first["run_id"], retry["previous_run_id"])
        self.assertEqual(2, len(research["runs"]))
        self.assertEqual("general_company", self.store.get_run(retry["run_id"])["trigger"]["kind"])


class GeneralCollectionPaginationTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_disclosure_pages_are_preserved_as_one_collection_result(self):
        calls=[]
        class Dart:
            async def disclosures(self,*args,**kwargs):
                calls.append(kwargs['page_no'])
                page=kwargs['page_no']
                return {'items':[{'rcept_no':str(page)}],'source_id':'source-'+str(page),'total_page':2}
        service=object.__new__(GeneralCollectionService);service.dart=Dart()
        result=await service._all_disclosures('run','00181712','2026-10-23')
        self.assertEqual([1,2],calls)
        self.assertEqual(['1','2'],[item['rcept_no'] for item in result['items']])
        self.assertEqual(['source-1','source-2'],result['source_ids'])

    async def test_disclosure_begin_moves_with_cutoff(self):
        calls=[]
        class Dart:
            async def disclosures(self,*args,**kwargs):
                calls.append(args[2])
                return {'items':[],'source_id':'source','total_page':1}
        service=object.__new__(GeneralCollectionService);service.dart=Dart()
        await service._all_disclosures('run','00181712','2028-10-23')
        self.assertEqual(['2025-01-01'],calls)


class GeneralCollectionFiscalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.store=ResearchStore(root/'session.db',root/'sources')
        receipt=receive(self.store,GeneralStartInput(company_name='합성 3월 결산 기업',
            corp_code='00181712',public_cutoff='2026-10-25',request_key='fiscal-test'))
        self.run_id=receipt['run_id'];self.research_id=receipt['research_id']

    async def test_collection_plans_march_year_end_without_promoting_numeric_validation(self):
        calls=[]
        class Dart:
            async def company(self,*args):return {'source_id':'identity','company':{'acc_mt':'03'}}
            async def disclosures(self,*args,**kwargs):
                return {'items':[{'report_nm':'사업보고서 (2026.03)','rcept_no':'20260630000001'},
                                 {'report_nm':'반기보고서 (2026.09)','rcept_no':'20261020000001'}],
                        'source_id':'disclosures','total_page':1}
            async def financials(self,run_id,corp_code,year,code,scope):
                calls.append((year,code,scope));return {'status':'no_data'}
            async def document(self,*args):return {'source_id':'document','members':[]}
            async def xbrl(self,*args):return {'source_id':'xbrl'}
        service=GeneralCollectionService(self.store,dart_service=Dart())
        result=await service.run(self.run_id)
        self.assertEqual('supported',result['fiscal_period']['status'])
        self.assertEqual(3,result['fiscal_period']['month'])
        self.assertEqual('numeric_and_xbrl_validation',result['next_phase'])
        self.assertEqual(10,len(calls))
        self.assertEqual([(2024,'11011'),(2025,'11011'),(2026,'11011'),(2025,'11012'),(2026,'11012')],
                         [(x['year'],x['report_code']) for x in result['target_reports']])
        self.assertEqual('not_performed',result['financial_verification'])
        with self.assertRaisesRegex(ValueError,'numeric_validation_required'):
            prepare_context(self.store,{'run_id':self.run_id},{})
        later=create_collection_retry(self.store,self.research_id)
        service._finish(later['run_id'],{'freshness':'no_new_or_corrected_regular_report',
                                        'next_phase':'reuse_existing_published_or_verified_result'})
        self.assertEqual(self.run_id,service._last_full_collection(later['run_id'])['id'])

    async def test_conflicting_fiscal_month_stops_financial_requests(self):
        calls=[]
        class Dart:
            async def company(self,*args):return {'source_id':'identity','company':{'acc_mt':'03'}}
            async def disclosures(self,*args,**kwargs):
                return {'items':[{'report_nm':'사업보고서 (2025.12)','rcept_no':'20260330000001'}],
                        'source_id':'disclosures','total_page':1}
            async def financials(self,*args):calls.append(args)
        result=await GeneralCollectionService(self.store,dart_service=Dart()).run(self.run_id)
        self.assertEqual('conflict',result['fiscal_period']['status'])
        self.assertEqual('fiscal_month_review_required',result['next_phase'])
        self.assertEqual([],calls)
