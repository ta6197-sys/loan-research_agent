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

    def test_v2_regression_records_source_checks_and_v12_successor_gap(self):
        root=Path(__file__).resolve().parents[1]
        folder=root/'doc/finance'
        audit=json.loads((folder/'BROAD_303_EXPANDED_V2_AUDIT_20261027.json').read_text())
        regression=json.loads((folder/'BROAD_303_V2_DATA_REGRESSION_20261027.json').read_text())
        self.assertEqual((9,74,97,692,600),
            tuple(regression[key] for key in ('new_company_count','new_company_period_rows',
                'checked_unique_sources','checked_unique_facts','checked_feature_references')))
        self.assertEqual(0,regression['external_requests'])
        self.assertEqual(0,regression['model_calls'])
        for scope in ('OFS','CFS'):
            snapshot=json.loads((folder/f'BROAD_303_EXPANDED_V2_{scope}_20261027.json').read_text())
            self.assertEqual(snapshot['snapshot_id'],regression['snapshot_ids'][scope])
            self.assertEqual(snapshot['snapshot_id'],audit['scopes'][scope]['snapshot_id'])
            self.assertTrue(all('borrowing_to_assets' not in row['features'] for row in snapshot['rows']))
        self.assertEqual({('00102353','OFS','2023-12-31'),('00102353','OFS','2025-06-30')},
            {(row['corp_code'],row['scope'],row['period_end'])
             for row in regression['v12_supported_in_current_read_model']})
        self.assertTrue(all(row['input_fact_ids'] and row['input_validation_ids'] and row['source_ids']
                            for row in regression['v12_supported_in_current_read_model']))
