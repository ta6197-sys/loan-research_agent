import unittest

from sh.audit_30392_api_matchability import evaluate


class ApiMatchabilityTests(unittest.TestCase):
    def test_audit_receipt_is_not_a_regular_report_comparator(self):
        disclosures = {"records": [{"corp_code": "12345678", "reports": [
            {"receipt_number": "20260401000001", "kind": "audit_report"}]}]}
        candidates = {"records": [{"corp_code": "12345678",
                                   "receipt_number": "20260401000001", "candidates": [{}]}]}
        row = evaluate(disclosures, candidates)[0]
        self.assertEqual(row["regular_report_count_in_window"], 0)
        self.assertEqual(row["xbrl_same_receipt"], "not_addressable_by_documented_contract")
        self.assertFalse(row["cohort_eligible"])

    def test_unknown_receipt_is_rejected(self):
        disclosures = {"records": [{"corp_code": "12345678", "reports": []}]}
        candidates = {"records": [{"corp_code": "12345678",
                                   "receipt_number": "20260401000001", "candidates": []}]}
        with self.assertRaisesRegex(ValueError, "candidate_receipt_not_audit_report"):
            evaluate(disclosures, candidates)


if __name__ == "__main__":
    unittest.main()
