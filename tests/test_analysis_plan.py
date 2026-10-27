import unittest

from agent.finance.analysis_plan import build


class AnalysisPlanTests(unittest.TestCase):
    def test_verified_signal_and_gap_remain_questions(self):
        metrics = [{"metric_id": "margin:2025", "status": "withheld", "scope": "OFS",
                    "reason_code": "verified_input_withheld", "period": {"end": "2025-12-31"},
                    "input_fact_ids": ["fact-1"], "input_validation_ids": ["validation-1"],
                    "source_ids": ["source-1"]},
                   {"metric_id": "margin:2024", "status": "unmeasured", "scope": "OFS",
                    "reason_code": "verified_input_withheld", "period": {"end": "2024-12-31"},
                    "input_fact_ids": [], "input_validation_ids": [], "source_ids": []}]
        signals = [{"signal_id": "cashflow:OFS:2025", "status": "candidate_for_followup",
                    "question": "변화 원인을 확인할 수 있나요?", "scope": "OFS",
                    "period": {"end": "2025-12-31"}, "input_fact_ids": ["fact-1"],
                    "input_validation_ids": ["validation-1"], "source_ids": ["source-1"]}]
        result = build("report", "batch", metrics, signals)
        self.assertEqual(result, build("report", "batch", list(reversed(metrics)), signals))
        self.assertEqual(2, len(result["steps"]))
        gap = result["steps"][1]
        self.assertEqual("evidence_required", gap["status"])
        self.assertEqual(["margin:2024", "margin:2025"], gap["related_metric_ids"])
        self.assertEqual(["fact-1"], gap["input_fact_ids"])
        self.assertNotIn("value", gap)
        self.assertIn("신용위험", result["limitations"][0])

    def test_supported_metrics_without_signal_do_not_create_risk_claim(self):
        result = build("report", "batch", [{"metric_id": "ratio", "status": "supported"}], [])
        self.assertEqual("no_review_items", result["status"])
        self.assertEqual([], result["steps"])

    def test_unverified_account_creates_specific_evidence_question_without_number(self):
        rows=[{'scope':'OFS','end':'2026-06-30','account':'cash','label':'현금및현금성자산',
               'status':'withheld','reason':'input_missing','fact_ids':[],'validation_ids':[]},
              {'scope':'OFS','end':'2025-06-30','account':'cash','label':'현금및현금성자산',
               'status':'withheld','reason':'input_missing','fact_ids':[],'validation_ids':[]},
              {'scope':'OFS','end':'2026-06-30','account':'revenue','status':'verified','value':'0'}]
        result=build(None,'batch',[],[],rows)
        self.assertEqual(1,len(result['steps']))
        step=result['steps'][0]
        self.assertEqual('account_verification_gap',step['kind'])
        self.assertIn('현금및현금성자산',step['question'])
        self.assertEqual([{'end':'2025-06-30'},{'end':'2026-06-30'}],step['periods'])
        self.assertNotIn('value',step)


if __name__ == "__main__":
    unittest.main()
