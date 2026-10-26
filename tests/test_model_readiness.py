import unittest

from agent.finance.model_readiness import evaluate
from agent.finance.peer_dataset import build_snapshot
from tests.test_peer_dataset import record


class ModelReadinessTests(unittest.TestCase):
    def test_interim_rows_do_not_inflate_company_year_gate(self):
        rows = self.records()
        for row in rows[:60]:
            row['period_end'] = row['period_start'][:4] + '-06-30'
        result = evaluate(build_snapshot(rows))
        self.assertEqual('not_trained_insufficient_data', result['training_status'])
        self.assertEqual(90, result['annual_row_count'])
        self.assertFalse(result['checks']['minimum_company_period_rows']['pass'])

    def records(self):
        return [record(str(company).zfill(3), str(year) + "-12-31", source_hash="h" + str(company) + str(year))
                for company in range(30) for year in range(2022, 2027)]

    def test_mixed_industry_blocks_training_even_when_row_count_passes(self):
        rows = self.records(); rows[-1]["industry_code"] = "C30"
        result = evaluate(build_snapshot(rows))
        self.assertEqual("not_trained_insufficient_data", result["training_status"])
        self.assertFalse(result["checks"]["comparability"]["pass"])

    def test_missing_core_feature_fails_availability_gate_without_zero_fill(self):
        rows = self.records()
        for row in rows[:30]:
            row["features"].pop("net_margin")
        result = evaluate(build_snapshot(rows))
        self.assertEqual("not_trained_insufficient_data", result["training_status"])
        self.assertFalse(result["checks"]["core_feature_availability"]["features"]["net_margin"]["pass"])

    def test_synthetic_origin_and_unlabeled_model_are_explicit(self):
        result = evaluate(build_snapshot(self.records()))
        self.assertEqual('synthetic_test_only', result['data_origin'])
        self.assertEqual('not_applicable_unsupervised', result['label_quality']['status'])
        rows = self.records()
        rows[-1]['data_origin'] = 'verified_public_disclosure'
        rows[-1]['verification_gates'] = dict.fromkeys(
            ('original_api', 'actual_period', 'fiscal_month', 'independent_xbrl'), 'pass')
        mixed = evaluate(build_snapshot(rows))
        self.assertFalse(mixed['checks']['data_origin']['pass'])
