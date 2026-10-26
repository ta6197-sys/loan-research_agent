import unittest

from agent.finance.anomaly_detection import detect
from agent.finance.anomaly_detection import _fit_score
import numpy as np
from agent.finance.model_evaluation import evaluate, evaluate_synthetic
from agent.finance.peer_dataset import build_snapshot


FEATURES = ("operating_margin", "net_margin", "liabilities_to_assets", "operating_cashflow_to_revenue", "revenue_growth")


def record(company, year, *, outlier=False):
    value = "1000" if outlier else str(10 + int(company))
    return {"corp_code": company, "industry_code": "C29", "period_start": str(year) + "-01-01", "period_end": str(year) + "-12-31",
            "period_kind": "duration", "scope": "OFS", "public_cutoff": "2027-12-31", "source_receipt": str(year + 1) + "033000000001",
            "source_hash": "hash-" + company + str(year), "validation_batch_id": "batch-" + company + str(year),
            "correction_status": "current_verified", "account_definition_version": "map-1", "peer_group": "manufacturing-C29",
            "data_origin": "synthetic_test_only", "verification_gates": {"status": "synthetic_test_only"},
            "features": {feature: {"status": "supported", "value": value, "evidence_refs": ["fact-" + feature, "validation-" + feature]} for feature in FEATURES}}


class AnomalyDetectionTests(unittest.TestCase):
    def test_threshold_is_frozen_from_training_scores(self):
        train = [({}, [float(index)]) for index in range(40)]
        _, _, threshold, training_scores = _fit_score(train, [({}, [10.])], ['x'])
        _, _, changed_threshold, _ = _fit_score(train, [({}, [1000000.])], ['x'])
        self.assertEqual(threshold, changed_threshold)
        self.assertEqual(threshold, float(np.quantile(training_scores, .95)))

    def snapshot(self):
        return build_snapshot([record(str(company).zfill(3), year, outlier=company == 29 and year == 2026)
                               for company in range(30) for year in range(2022, 2027)])

    def test_readiness_failure_never_trains_model(self):
        result = detect(build_snapshot([record("001", 2026)]))
        self.assertEqual(("not_trained_insufficient_data", []), (result["status"], result["splits"]))

    def test_later_published_correction_is_excluded_from_time_training(self):
        snapshot = self.snapshot()
        snapshot['rows'][0]['source_public_at'] = '2027-08-01'
        result = detect(snapshot)
        split = next(item for item in result['splits'] if item['name'] == 'time_ordered')
        self.assertEqual(119, split['training_row_count'])
        self.assertTrue(any(item['reason'] == 'published_after_training_cutoff' for item in split['excluded_rows']))

    def test_isolation_forest_uses_time_and_company_split_without_imputation(self):
        result = detect(self.snapshot())
        self.assertEqual("measured", result["status"])
        self.assertEqual('synthetic_test_only', result['data_origin'])
        self.assertTrue(any('합성 시험 자료' in item for item in result['limitations']))
        splits = {item["name"]: item for item in result["splits"]}
        self.assertEqual("measured", splits["time_ordered"]["status"])
        self.assertEqual("measured", splits["company_holdout"]["status"])
        self.assertEqual((120, 30), (splits["time_ordered"]["training_row_count"], splits["time_ordered"]["evaluation_row_count"]))
        self.assertTrue(all("feature_evidence" in item for item in splits["time_ordered"]["observations"]))
        self.assertEqual(20261023, result["model"]["random_state"])
        time = splits['time_ordered']
        self.assertEqual('training_scores', time['threshold_source'])
        self.assertFalse(set(time['training_row_ids']) & set(time['evaluation_row_ids']))
        for item in time['sensitivity']:
            self.assertEqual(item['candidate_count'], len(item['candidate_row_ids']))
            self.assertTrue(set(item['candidate_row_ids']) <= set(time['evaluation_row_ids']))
        self.assertEqual(result, detect(self.snapshot()))

    def test_unlabeled_evaluation_and_synthetic_metrics_are_explicit(self):
        result = evaluate(detect(self.snapshot()))
        self.assertEqual("unlabeled_no_operational_accuracy", result["label_status"])
        synthetic = evaluate_synthetic(["a", "b"], ["a", "c"], ["a", "b", "c", "d"])
        self.assertEqual(("synthetic_evaluation_only", .5, .5), (synthetic["status"], synthetic["precision"], synthetic["recall"]))
