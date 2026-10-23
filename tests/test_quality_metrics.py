import unittest

from agent.finance.quality_metrics import calculate


def batch(status='pass', amount='exact', location='pass'):
    return {'id': 'a' * 32, 'policy_version': 'synthetic', 'summary': {'account_coverage': [], 'disclosure_history_audit': {'status': 'pass'}},
            'validations': [{'id': 'b' * 32, 'fact_id': 'f1', 'rule_id': 'numeric_fact', 'status': status,
                             'data': {'checks': {'V02': {'status': 'pass'}, 'V05': {'status': status},
                                                 'V07': {'status': location}, 'V13': {'status': 'not_checked'}},
                                      'amount_comparison': {'match': amount}}}], 'calculations': []}


class QualityMetricsTests(unittest.TestCase):
    def test_exact_and_rounding_are_counted_separately(self):
        metrics = calculate(batch())
        self.assertEqual(1, metrics['amount_comparison']['exact_match_count'])
        self.assertEqual(0, metrics['amount_comparison']['rounding_match_count'])
        rounded = calculate(batch(amount='rounding'))
        self.assertEqual(0, rounded['amount_comparison']['exact_match_count'])
        self.assertEqual(1, rounded['amount_comparison']['rounding_match_count'])

    def test_not_checked_is_not_a_pass_or_a_rate_denominator(self):
        data = batch(status='not_checked', location='not_checked')
        metrics = calculate(data)
        self.assertEqual(0, metrics['amount_comparison']['numerator'])
        self.assertEqual(0, metrics['amount_comparison']['denominator'])
        self.assertIsNone(metrics['amount_comparison']['rate'])
        self.assertEqual('unmeasured', metrics['source_location']['status'])

    def test_report_coverage_does_not_claim_missing_rows_are_verified(self):
        report = {'content': {'context': {'financial_rows': [{'status': 'verified'}, {'status': 'withheld'}]}}}
        coverage = calculate(batch(), report)['report_coverage']
        self.assertEqual((1, 1, 2), (coverage['verified_count'], coverage['withheld_count'], coverage['total_count']))
        self.assertEqual(0.5, coverage['rate'])

    def test_no_report_is_explicitly_unmeasured(self):
        self.assertEqual('unmeasured', calculate(batch())['report_coverage']['status'])
