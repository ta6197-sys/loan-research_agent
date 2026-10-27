import json
from pathlib import Path
import unittest

from agent.finance.broad_303_candidates import build, classify


class Broad303CandidateTests(unittest.TestCase):
    def test_archived_audit_creates_candidate_only_manifest(self):
        root = Path(__file__).resolve().parents[1]
        raw = (root / 'doc/finance/COHORT_CANDIDATE_AUDIT_20261025.json').read_bytes()
        first = build(raw)
        self.assertEqual(first, build(raw))
        self.assertEqual(87, first['candidate_count'])
        self.assertEqual(117, len(first['records']))
        self.assertTrue(all(row['cohort_status'] == 'not_included' for row in first['records']))
        self.assertTrue(all(row['classification_revision_status'] == 'unconfirmed'
                            for row in first['records']))
        saved = json.loads((root / 'doc/finance/BROAD_303_CANDIDATES_20261027.json').read_text())
        self.assertEqual(first, saved)

    def test_changed_or_missing_industry_is_not_a_candidate(self):
        sample = {'corp_code':'00000001','corp_name':'합성','company_induty_code':'303',
                  'annual_2025':{'receipt':'annual','xbrl_codes':['30392']},
                  'half_2026':{'receipt':'half','xbrl_codes':['30399']}}
        self.assertEqual('industry_code_changed_review_required',classify(sample)['candidate_status'])
        sample['half_2026']['xbrl_codes'] = []
        self.assertEqual('xbrl_industry_unconfirmed',classify(sample)['candidate_status'])


if __name__ == '__main__': unittest.main()
