import unittest

from agent.finance.anomaly_detection import detect
from agent.finance.model_evaluation import evaluate, evaluate_synthetic
from agent.finance.peer_dataset import build_snapshot


FEATURES = ("operating_margin", "net_margin", "liabilities_to_assets", "operating_cashflow_to_revenue", "revenue_growth")


def record(company, year, *, outlier=False):
    value = "1000" if outlier else str(10 + int(company))
    return {"corp_code": company, "industry_code": "C29", "period_start": str(year) + "-01-01", "period_end": str(year) + "-12-31",
            "period_kind": "duration", "scope": "OFS", "public_cutoff": "2027-12-31", "source_receipt": str(year + 1) + "033000000001",
            "source_hash": "hash-" + company + str(year), "validation_batch_id": "batch-" + company + str(year),
            "correction_status": "current_verified", "account_definition_version": "map-1", "peer_group": "manufacturing-C29",
            "features": {feature: {"status": "supported", "value": value, "evidence_refs": ["fact-" + feature, "validation-" + feature]} for feature in FEATURES}}


class AnomalyDetectionTests(unittest.TestCase):
    def snapshot(self):
        return build_snapshot([record(str(company).zfill(3), year, outlier=company == 29 and year == 2026)
                               for company in range(30) for year in range(2022, 2027)])

    def test_readiness_failure_never_trains_model(self):
        result = detect(build_snapshot([record("001", 2026)]))
        self.assertEqual(("not_trained_insufficient_data", []), (result["status"], result["splits"]))

    def test_isolation_forest_uses_time_and_company_split_without_imputation(self):
        result = detect(self.snapshot())
        self.assertEqual("measured", result["status"])
        splits = {item["name"]: item for item in result["splits"]}
        self.assertEqual("measured", splits["time_ordered"]["status"])
        self.assertEqual("measured", splits["company_holdout"]["status"])
        self.assertEqual((120, 30), (splits["time_ordered"]["training_row_count"], splits["time_ordered"]["evaluation_row_count"]))
        self.assertTrue(all("feature_evidence" in item for item in splits["time_ordered"]["observations"]))
        self.assertEqual(20261023, result["model"]["random_state"])

    def test_unlabeled_evaluation_and_synthetic_metrics_are_explicit(self):
        result = evaluate(detect(self.snapshot()))
        self.assertEqual("unlabeled_no_operational_accuracy", result["label_status"])
        synthetic = evaluate_synthetic(["a", "b"], ["a", "c"], ["a", "b", "c", "d"])
        self.assertEqual(("synthetic_evaluation_only", .5, .5), (synthetic["status"], synthetic["precision"], synthetic["recall"]))
