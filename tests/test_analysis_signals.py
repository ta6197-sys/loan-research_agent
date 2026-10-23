import unittest

from agent.finance.analysis_signals import build


class AnalysisSignalTests(unittest.TestCase):
    def test_signal_is_followup_candidate_with_input_evidence_not_risk_finding(self):
        analytics = {"schema_version": "financial-analytics-1.0", "report_id": "report", "results": [
            {"name": "revenue_growth", "scope": "OFS", "period": {"end": "2026-06-30"}, "status": "supported", "value": "5", "inputs": [{"fact_ids": ["revenue"], "validation_ids": ["v-revenue"], "source_ids": ["s-revenue"]}]},
            {"name": "inventory_growth", "scope": "OFS", "period": {"end": "2026-06-30"}, "status": "supported", "value": "20", "inputs": [{"fact_ids": ["inventory"], "validation_ids": ["v-inventory"], "source_ids": ["s-inventory"]}]},
        ]}
        result = build(analytics)
        signal = result["signals"][0]
        self.assertEqual("candidate_for_followup", signal["status"])
        self.assertIn("fact_ids", signal["evidence"][0]["inputs"][0])
        self.assertIn("판단하지 않습니다", signal["limitation"])
        self.assertNotIn("부실 확정", signal["question"])
