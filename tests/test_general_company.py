"""일반 기업 조사 진입은 외부 요청 없이도 불변 계획을 저장해야 한다."""
from pathlib import Path
import tempfile
import unittest

from agent.finance.general_company import GeneralStartInput, create_collection_retry, receive
from agent.finance.general_collection import changed_regular_reports, planned_reports, select_regular_reports
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
            "request_key": "sk-general-start",
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
