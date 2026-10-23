import unittest

from agent.finance.model_readiness import evaluate
from agent.finance.peer_dataset import build_snapshot
from tests.test_peer_dataset import record


class ModelReadinessTests(unittest.TestCase):
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
