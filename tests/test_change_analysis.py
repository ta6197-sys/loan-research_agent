import unittest

from agent.finance.change_analysis import analyze_financial_changes,investigate_hypotheses
from agent.finance.report_context import context_period_ends


def row(account, end, value, fact_id):
    return {"scope": "OFS", "account": account, "end": end, "status": "verified", "value": value,
            "fact_ids": [fact_id], "label": account}


class ChangeAnalysisTests(unittest.TestCase):
    def test_report_periods_follow_saved_comparison_window(self):
        self.assertEqual(('2025-12-31','2026-12-31','2027-12-31','2026-06-30','2027-06-30'),
                         context_period_ends({'annual_years':[2025,2026,2027],
                                              'interim_periods':['2026-H1','2027-H1']}))

    def test_working_capital_pattern_is_a_hypothesis_not_a_cause(self):
        rows = []
        values = {
            "2025-06-30": {"short_term_borrowings": "10", "current_long_term_borrowings": "2", "long_term_borrowings": "8", "inventory": "30", "receivables": "40", "operating_cashflow": "20"},
            "2026-06-30": {"short_term_borrowings": "20", "current_long_term_borrowings": "2", "long_term_borrowings": "10", "inventory": "45", "receivables": "55", "operating_cashflow": "5"},
        }
        for end, accounts in values.items():
            rows += [row(account, end, value, end + account) for account, value in accounts.items()]
        result = analyze_financial_changes(rows)
        self.assertEqual(1, len(result["hypotheses"]))
        hypothesis = result["hypotheses"][0]
        self.assertEqual("possible_explanation", hypothesis["status"])
        self.assertIn("입증하지는 않는다", hypothesis["statement"])
        self.assertEqual(1, len(result["questions"]))

    def test_missing_borrowing_component_does_not_create_total_or_hypothesis(self):
        rows = [row("short_term_borrowings", "2025-06-30", "10", "a"), row("short_term_borrowings", "2026-06-30", "20", "b")]
        result = analyze_financial_changes(rows)
        self.assertEqual([], result["hypotheses"])
        self.assertFalse(any(item["account"] == "borrowing_total" for item in result["changes"]))

    def test_hypothesis_research_records_searches_without_claiming_causality(self):
        hypothesis={'id':'hypothesis:test'}
        actions=investigate_hypotheses([hypothesis],lambda query:[{'match_count':1 if query=='차입금' else 0}])
        self.assertEqual('stored_disclosure_matches_found',actions[0]['status'])
        self.assertIn('입증하지 않으며',actions[0]['limitation'])
