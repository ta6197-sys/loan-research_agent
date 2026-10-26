import unittest

from agent.finance.candidate_identity import assess_candidate, collection_plan


class CandidateIdentityTests(unittest.TestCase):
    def test_all_four_official_checks_and_industry_required(self):
        checks = dict.fromkeys(('corp_code', 'legal_address', 'factory_location', 'official_registration'), 'pass')
        self.assertEqual('미확인', assess_candidate(checks, industry_status='pass')['identity_status'])
        evidence = {key: {'source_url': 'https://data.go.kr/official', 'source_sha256': 'a' * 64,
                          'matched_value': '검증값'} for key in (*checks, 'ksic_revision_and_legal_industry')}
        self.assertEqual('확인', assess_candidate(checks, industry_status='pass', evidence=evidence)['identity_status'])
        checks['factory_location'] = 'unverified'
        self.assertEqual('미확인', assess_candidate(checks, industry_status='pass')['identity_status'])
        checks['factory_location'] = 'conflict'
        self.assertEqual('충돌', assess_candidate(checks, industry_status='pass')['identity_status'])

    def test_request_plan_excludes_unconfirmed_or_without_reports(self):
        rows = [{'corp_code': '00000001', 'identity_status': '확인', 'next_stage_allowed': True, 'eligible_report_count': 1},
                {'corp_code': '00000002', 'identity_status': '미확인', 'next_stage_allowed': False, 'eligible_report_count': 1},
                {'corp_code': '00000003', 'identity_status': '확인', 'next_stage_allowed': True, 'eligible_report_count': 0}]
        result = collection_plan(rows, public_cutoff='2026-10-26', request_limit=2)
        self.assertEqual(['00000001'], result['target_corp_codes'])
        self.assertIsNone(result['missing_numeric_value'])
        with self.assertRaisesRegex(ValueError, 'request_budget'):
            collection_plan(rows, public_cutoff='2026-10-26', request_limit=1)


if __name__ == '__main__':
    unittest.main()
