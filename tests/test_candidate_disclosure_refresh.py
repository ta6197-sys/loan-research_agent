import unittest

from agent.finance.candidate_disclosure_refresh import refresh_plan, new_regular_reports


class CandidateDisclosureRefreshTests(unittest.TestCase):
    def test_plan_advances_cutoff_and_caps_one_request_per_company(self):
        prior = {'as_of': '2026-10-26', 'checked_count': 2, 'records': [
            {'corp_code': '00000001'}, {'corp_code': '00000002'}]}
        plan = refresh_plan(prior, cutoff='2026-10-27', request_limit=2)
        self.assertEqual('2026-10-27', plan['begin_date'])
        self.assertEqual(['00000001', '00000002'], plan['target_corp_codes'])
        self.assertEqual(2, plan['request_count'])
        with self.assertRaisesRegex(ValueError, 'cutoff_must_advance'):
            refresh_plan(prior, cutoff='2026-10-26', request_limit=2)
        with self.assertRaisesRegex(ValueError, 'request_limit_below_candidate_count'):
            refresh_plan(prior, cutoff='2026-10-27', request_limit=1)

    def test_only_annual_and_half_reports_with_valid_receipts(self):
        response = {'total_page': 1, 'list': [
            {'report_nm': '사업보고서 (2025.12)', 'rcept_no': '20261027000001'},
            {'report_nm': '반기보고서 (2026.06)', 'rcept_no': '20261027000002'},
            {'report_nm': '분기보고서 (2026.03)', 'rcept_no': '20261027000003'},
            {'report_nm': '사업보고서 (2025.12)', 'rcept_no': 'bad'},
            {'report_nm': '사업보고서 (2025.12)', 'rcept_no': '20261026000001'},
            {'report_nm': '사업보고서 (2025.12)', 'rcept_no': '20261028000001'}]}
        result = new_regular_reports(response, begin_date='2026-10-27', cutoff='2026-10-27')
        self.assertEqual(['20261027000001', '20261027000002'],
                         [row['rcept_no'] for row in result])
        with self.assertRaisesRegex(ValueError, 'pagination_review_required'):
            new_regular_reports({'total_page': 2, 'list': []}, begin_date='2026-10-27', cutoff='2026-10-27')


if __name__ == '__main__':
    unittest.main()
