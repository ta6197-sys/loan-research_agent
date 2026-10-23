import unittest

from agent.finance.quality_analytics import analyze


def batch(batch_id, *, v05='pass', v07='not_checked', numeric='pass', created='2026-01-01T00:00:00+00:00'):
    return {'id': batch_id, 'created_at': created, 'policy_version': 'synthetic', 'summary': {'disclosure_history_audit': {'status': 'not_checked'}, 'account_coverage': []},
            'validations': [
                {'id': 'numeric-' + batch_id, 'fact_id': 'fact-' + batch_id, 'rule_id': 'numeric_fact', 'status': numeric,
                 'data': {'account': 'revenue', 'scope': 'OFS', 'period': {'kind': 'duration', 'start': '2025-01-01', 'end': '2025-06-30'},
                          'checks': {'V05': {'status': v05, 'reason': 'value_mismatch' if v05 == 'fail' else 'ok'},
                                     'V07': {'status': v07, 'reason': 'original_location_missing' if v07 != 'pass' else 'ok'},
                                     'V13': {'status': 'not_checked', 'reason': 'history_unavailable'}},
                          'amount_comparison': {'match': 'exact'}}},
                {'id': 'coverage-' + batch_id, 'fact_id': None, 'rule_id': 'V07_source_coverage', 'status': 'not_checked',
                 'data': {'reason': 'matching_original_not_available'}},
            ], 'calculations': []}


class QualityAnalyticsTests(unittest.TestCase):
    def test_rule_distribution_keeps_rule_denominators_separate(self):
        result = analyze('research', [batch('a')])
        v05 = next(item for item in result['rule_distribution'] if item['rule_id'] == 'V05')
        v07 = next(item for item in result['rule_distribution'] if item['rule_id'] == 'V07')
        self.assertEqual((1, 1, 1, 'measured'), (v05['observed_count'], v05['denominator'], v05['numerator'], v05['measurement_status']))
        self.assertEqual((1, 0, 1, 'unmeasured'), (v07['observed_count'], v07['denominator'], v07['not_checked'], v07['measurement_status']))
        self.assertEqual(1, result['quality_metrics']['numeric_fact']['total_count'])
        self.assertEqual(2, v05['observed_count'] + v07['observed_count'])

    def test_account_and_period_gaps_count_one_numeric_fact_once(self):
        result = analyze('research', [batch('a')])
        account = result['account_gaps'][0]; period = result['period_coverage'][0]
        self.assertEqual(('revenue', 1, 1, 0), (account['account'], account['observed_count'], account['not_checked'], account['pass']))
        self.assertEqual(('2025-06-30', 1), (period['period'], period['observed_count']))

    def test_mapping_correction_and_exception_gaps_remain_unmeasured_or_reviewable(self):
        result = analyze('research', [batch('a')])
        self.assertEqual('unmeasured', result['correction_history']['v13']['measurement_status'])
        reasons = {item['reason'] for item in result['source_and_mapping_issues']}
        self.assertIn('original_location_missing', reasons)
        exception_reasons = {item['reason_code'] for item in result['exception_reasons']}
        self.assertIn('original_location_missing', exception_reasons)
        self.assertEqual('not_eligible', result['model_dataset_eligibility']['status'])

    def test_batch_trend_only_compares_measured_rates(self):
        first = batch('a', v05='pass', v07='pass', created='2026-01-01T00:00:00+00:00')
        second = batch('b', v05='fail', v07='not_checked', created='2026-02-01T00:00:00+00:00')
        result = analyze('research', [second, first])
        self.assertEqual('unmeasured', result['batch_trend'][0]['comparison_to_previous'])
        self.assertEqual('measured', result['batch_trend'][1]['comparison_to_previous'])
        self.assertEqual(-1.0, result['batch_trend'][1]['amount_comparison_rate_delta'])
