import unittest
from copy import deepcopy

from agent.finance.analysis_signals import build
from agent.finance.financial_analytics import analyze
from agent.finance.validation_acceptance import MANDATORY_CHECKS


def row(account, value, end, *, flow=False, validation_id=None, borrowing=False):
    identifier = validation_id or account + end
    period = {'kind': 'duration', 'start': end[:4] + '-01-01', 'end': end} if flow else {'kind': 'instant', 'as_of': end}
    return {'scope': 'OFS', 'account': account, 'end': end, 'period': period, 'status': 'verified', 'value': str(value),
            'fact_ids': ['fact-' + identifier], 'validation_ids': [identifier], 'source_ids': ['source-' + identifier],
            '_borrowing': borrowing}


def fixture():
    rows = []
    for end, values in {
        '2025-06-30': {'revenue': 100, 'operating_profit': 10, 'net_profit': 8, 'operating_cashflow': 5,
                       'inventory': 10, 'receivables': 10, 'assets': 100, 'liabilities': 50, 'equity': 50,
                       'short_term_borrowings': 10, 'current_long_term_borrowings': 5, 'long_term_borrowings': 10},
        '2026-06-30': {'revenue': 110, 'operating_profit': 11, 'net_profit': 9, 'operating_cashflow': -3,
                       'inventory': 30, 'receivables': 25, 'assets': 120, 'liabilities': 72, 'equity': 48,
                       'short_term_borrowings': 18, 'current_long_term_borrowings': 5, 'long_term_borrowings': 14},
    }.items():
        for account, value in values.items():
            rows.append(row(account, value, end, flow=account in {'revenue', 'operating_profit', 'net_profit', 'operating_cashflow'}, borrowing=account in {'short_term_borrowings', 'current_long_term_borrowings', 'long_term_borrowings'}))
    validations = []
    for item in rows:
        checks = {key: {'status': 'pass'} for key in MANDATORY_CHECKS}
        if item['account'] in {'short_term_borrowings', 'current_long_term_borrowings', 'long_term_borrowings'}: checks['V12'] = {'status': 'pass'}
        validations.append({'id': item['validation_ids'][0], 'rule_id': 'numeric_fact', 'status': 'pass',
                            'data': {'checks': checks, 'amount_comparison': {'original_normalized': item['value']}}})
    calculations = []
    for account in {item['account'] for item in rows}:
        before = next(item for item in rows if item['account'] == account and item['end'] == '2025-06-30')
        after = next(item for item in rows if item['account'] == account and item['end'] == '2026-06-30')
        calculations.append({'id': 'v11-' + account, 'formula_version': 'numeric-validation-1.27:V11_change_same_period', 'status': 'pass',
                             'data': {'result': str(int(after['value']) - int(before['value'])), 'inputs': {
                                 'current': {'validation_id': after['validation_ids'][0]}, 'previous': {'validation_id': before['validation_ids'][0]}}}})
    report = {'id': 'report', 'content': {'context': {'financial_rows': rows}}}
    return report, {'id': 'batch', 'validations': validations, 'calculations': calculations}


class FinancialAnalyticsTests(unittest.TestCase):
    def result(self, values, name, end='2026-06-30'):
        return next(item for item in values['results'] if item['name'] == name and (item['period'].get('end') or item['period'].get('as_of')) == end)

    def test_only_verified_rows_with_mandatory_checks_and_v11_are_used(self):
        report, batch = fixture(); values = analyze(report, batch)
        margin = self.result(values, 'operating_margin')
        self.assertEqual(('supported', '10.0', 'percent'), (margin['status'], margin['value'], margin['unit']))
        growth = self.result(values, 'revenue_growth')
        self.assertEqual(('supported', '10.0'), (growth['status'], growth['value']))
        self.assertEqual('(current - previous) / previous * 100', growth['formula'])
        self.assertEqual(['v11-revenue'], growth['comparison_calculation_ids'])
        self.assertEqual(['fact-revenue2025-06-30'], growth['inputs'][0]['fact_ids'])
        debt = self.result(values, 'liabilities_to_equity')
        self.assertEqual('150.0', debt['value'])

    def test_zero_or_negative_growth_base_and_missing_v11_are_withheld(self):
        report, batch = fixture()
        before = next(item for item in report['content']['context']['financial_rows'] if item['account'] == 'revenue' and item['end'] == '2025-06-30')
        before['value'] = '0'
        next(item for item in batch['calculations'] if item['id'] == 'v11-revenue')['data']['result'] = '110'
        values = analyze(report, batch)
        growth = self.result(values, 'revenue_growth')
        self.assertEqual(('withheld', 'zero_or_nonpositive_comparison_base'), (growth['status'], growth['reason']))
        report, batch = fixture(); batch['calculations'] = [item for item in batch['calculations'] if 'revenue' not in item['id']]
        self.assertEqual('withheld', self.result(analyze(report, batch), 'revenue_growth')['status'])

    def test_borrowing_total_requires_composition_and_signals_remain_followup_only(self):
        report, batch = fixture(); values = analyze(report, batch)
        total = self.result(values, 'borrowing_total_change')
        self.assertEqual(('supported', '12'), (total['status'], total['value']))
        signals = build(values)
        kinds = {item['signal'] for item in signals['signals']}
        self.assertIn('positive_profit_negative_operating_cashflow', kinds)
        self.assertIn('borrowing_increase_with_cashflow_deterioration', kinds)
        self.assertTrue(all(item['status'] == 'candidate_for_followup' for item in signals['signals']))
        borrowed = next(item for item in report['content']['context']['financial_rows'] if item['account'] == 'short_term_borrowings' and item['end'] == '2026-06-30')
        validation = next(item for item in batch['validations'] if item['id'] == borrowed['validation_ids'][0])
        validation['data']['checks']['V12']['status'] = 'not_checked'
        self.assertEqual('withheld', self.result(analyze(report, batch), 'borrowing_total_change')['status'])

    def test_raw_evidence_failure_never_becomes_analysis_input(self):
        report, batch = fixture()
        values = analyze(report, batch, raw_ok=lambda item: item['account'] != 'revenue')
        self.assertEqual('unmeasured', self.result(values, 'operating_margin')['status'])

    def test_same_input_is_reproducible_and_scope_mismatch_is_not_mixed(self):
        report, batch = fixture()
        self.assertEqual(analyze(report, batch), analyze(deepcopy(report), deepcopy(batch)))
        current = next(row for row in report['content']['context']['financial_rows'] if row['account'] == 'revenue' and row['end'] == '2026-06-30')
        current['scope'] = 'CFS'
        self.assertFalse(any(item['name'] == 'revenue_growth' and item['status'] == 'supported'
                             for item in analyze(report, batch)['results']))
