import unittest

from agent.finance.quality_exceptions import build


class QualityExceptionsTests(unittest.TestCase):
    def test_mandatory_failure_is_critical_and_id_is_stable(self):
        batch = {'id': 'a' * 32, 'validations': [{'id': 'b' * 32, 'fact_id': 'f', 'rule_id': 'numeric_fact',
                 'status': 'fail', 'created_at': '2026-01-01T00:00:00+00:00',
                 'data': {'account': 'revenue', 'scope': 'OFS', 'period': {'end': '2025-12-31'},
                          'checks': {'V01': {'status': 'fail', 'reason': 'company_mismatch'}}}}], 'calculations': []}
        first = build('c' * 32, batch)
        second = build('c' * 32, batch)
        critical = next(item for item in first if item['rule_id'] == 'V01')
        self.assertEqual('critical', critical['severity'])
        self.assertEqual(first, second)

    def test_withheld_report_row_becomes_reviewable_gap(self):
        batch = {'id': 'a' * 32, 'validations': [], 'calculations': []}
        report = {'content': {'context': {'financial_rows': [{'account': 'cash', 'scope': 'OFS', 'end': '2025-12-31', 'status': 'withheld'}]}}}
        item = build('c' * 32, batch, report)[0]
        self.assertEqual(('V17', 'not_checked', 'report_financial_row_withheld'),
                         (item['rule_id'], item['validation_status'], item['reason_code']))
