import unittest

from agent.finance.ksic_correspondence import reconcile_30392_candidate


class KsicCorrespondenceTests(unittest.TestCase):
    def test_revision_10_has_official_meaning_but_no_legal_identity(self):
        source = {'industry_code': '30392', 'industry_classification_version': '10',
                  'corp_code': None, 'identity_status': '미확인', 'next_stage_allowed': False}
        result = reconcile_30392_candidate(source)
        self.assertEqual('supported', result['ksic11_30392_meaning'])
        self.assertEqual('10', result['ksic_correspondence_evidence']['old_version'])
        self.assertEqual('11', result['ksic_correspondence_evidence']['new_version'])
        self.assertEqual('미확인', result['identity_status'])
        self.assertFalse(result['next_stage_allowed'])
        self.assertEqual('identity_review_required', result['cohort_candidate_status'])
        self.assertNotIn('ksic11_30392_meaning', source)

    def test_revision_9_same_code_is_excluded(self):
        result = reconcile_30392_candidate({
            'industry_code': '30392', 'industry_classification_version': '9',
            'corp_code': None, 'identity_status': '미확인', 'next_stage_allowed': False})
        self.assertEqual('not_equivalent', result['ksic11_30392_meaning'])
        self.assertEqual('excluded_ksic9_30392_electrical_equipment', result['cohort_candidate_status'])
        self.assertFalse(result['next_stage_allowed'])

    def test_revision_11_direct_evidence_still_needs_identity(self):
        result = reconcile_30392_candidate({
            'industry_code': '30392', 'industry_classification_version': '11',
            'corp_code': None, 'identity_status': '미확인', 'next_stage_allowed': False})
        self.assertEqual('direct_source', result['ksic11_30392_meaning'])
        self.assertEqual('identity_review_required', result['cohort_candidate_status'])
        self.assertFalse(result['next_stage_allowed'])

    def test_other_code_fails_closed(self):
        with self.assertRaisesRegex(ValueError, 'unexpected_industry_code'):
            reconcile_30392_candidate({'industry_code': '30399', 'industry_classification_version': '10'})

    def test_cannot_overwrite_confirmed_identity(self):
        with self.assertRaisesRegex(ValueError, 'candidate_identity_must_be_unverified'):
            reconcile_30392_candidate({'industry_code': '30392',
                                        'industry_classification_version': '10',
                                        'corp_code': '00129642', 'next_stage_allowed': True})


if __name__ == '__main__':
    unittest.main()
