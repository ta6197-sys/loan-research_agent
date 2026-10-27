import json
from pathlib import Path
from unittest import TestCase


class Broad303IncrementPlanTests(TestCase):
    def test_fixed_increment_is_disjoint_and_bounded(self):
        root=Path(__file__).resolve().parents[1]
        initial=json.loads((root/'doc/finance/BROAD_303_COLLECTION_PLAN_20261027.json').read_text())
        increment=json.loads((root/'doc/finance/BROAD_303_INCREMENT_PLAN_20261027.json').read_text())
        initial_codes={row['corp_code'] for row in initial['targets']}
        increment_codes={row['corp_code'] for row in increment['targets']}
        self.assertEqual(12,len(increment_codes))
        self.assertFalse(initial_codes & increment_codes)
        self.assertEqual(initial['public_cutoff'],increment['public_cutoff'])
        self.assertEqual(60,increment['max_external_requests_per_company'])
        self.assertEqual(720,increment['max_total_external_requests'])
        self.assertEqual(0,increment['model_calls'])
