import unittest

from agent.finance.analysis_signals import build
from agent.finance.financial_analytics import analyze
from tests.test_financial_analytics import fixture


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

    def signal_kinds(self, report, batch):
        return {item["signal"]: item for item in build(analyze(report, batch))["signals"]}

    def current(self, report, account):
        return next(row for row in report["content"]["context"]["financial_rows"]
                    if row["account"] == account and row["end"] == "2026-06-30")

    def test_positive_profit_negative_cashflow_requires_two_verified_same_scope_period_inputs(self):
        report, batch = fixture()
        self.current(report, "operating_profit")["value"] = "100"; self.current(report, "operating_cashflow")["value"] = "80"
        self.assertNotIn("positive_profit_negative_operating_cashflow", self.signal_kinds(report, batch))

        report, batch = fixture()
        self.current(report, "operating_profit")["value"] = "100"; self.current(report, "operating_cashflow")["value"] = "-20"
        signal = self.signal_kinds(report, batch)["positive_profit_negative_operating_cashflow"]
        refs = signal["evidence"][0]["inputs"]
        self.assertEqual({"fact-operating_profit2026-06-30", "fact-operating_cashflow2026-06-30"}, {fact for row in refs for fact in row["fact_ids"]})
        self.assertEqual({"source-operating_profit2026-06-30", "source-operating_cashflow2026-06-30"}, {source for row in refs for source in row["source_ids"]})

        report, batch = fixture()
        self.current(report, "operating_profit")["value"] = "-10"; self.current(report, "operating_cashflow")["value"] = "-20"
        self.assertNotIn("positive_profit_negative_operating_cashflow", self.signal_kinds(report, batch))

        report, batch = fixture(); self.current(report, "operating_cashflow")["status"] = "withheld"
        self.assertNotIn("positive_profit_negative_operating_cashflow", self.signal_kinds(report, batch))

        report, batch = fixture(); self.current(report, "operating_cashflow")["scope"] = "CFS"
        self.assertNotIn("positive_profit_negative_operating_cashflow", self.signal_kinds(report, batch))

        report, batch = fixture(); self.current(report, "operating_cashflow")["period"]["end"] = "2026-05-31"
        self.assertNotIn("positive_profit_negative_operating_cashflow", self.signal_kinds(report, batch))
