import unittest

from agent.finance.model_evaluation import evaluate, evaluate_synthetic


class ModelEvaluationTests(unittest.TestCase):
    def test_different_evaluation_populations_are_not_compared(self):
        result = evaluate({'splits': [
            {'name': 'time', 'status': 'measured', 'evaluation_row_ids': ['a'], 'observations': []},
            {'name': 'company', 'status': 'measured', 'evaluation_row_ids': ['b'], 'observations': []}]})
        self.assertEqual('unmeasured', result['candidate_stability']['status'])
        self.assertIsNone(result['candidate_stability']['jaccard'])

    def test_no_trained_split_does_not_claim_operational_accuracy(self):
        result = evaluate({"dataset_snapshot_id": "snapshot", "splits": []})
        self.assertEqual(("not_evaluated_no_trained_split", "unlabeled_no_operational_accuracy"),
                         (result["status"], result["label_status"]))

    def test_synthetic_evaluation_is_labeled_and_never_claims_real_performance(self):
        result = evaluate_synthetic(["a"], ["a", "b"], ["a", "b", "c"])
        self.assertEqual("synthetic_evaluation_only", result["status"])
        self.assertIn("실제 금융", result["limitation"])
