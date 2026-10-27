import hashlib
import json
import unittest

from agent.finance.disclosure_availability import classify_candidate


def fixture(names):
    raw = json.dumps({"status": "000", "total_page": "1", "list": [
        {"rcept_no": f"2026033100000{index}", "report_nm": name}
        for index, name in enumerate(names)]}).encode()
    row = {"corp_code": "12345678", "corp_name": "시험 법인",
           "status": "no_annual_or_half_report",
           "source_sha256": hashlib.sha256(raw).hexdigest()}
    return row, raw


class DisclosureAvailabilityTests(unittest.TestCase):
    def test_audit_report_is_document_candidate_without_numeric_promotion(self):
        row, raw = fixture(["감사보고서 (2025.12)", "연결감사보고서 (2025.12)"])
        result = classify_candidate(row, raw, begin_date="2023-01-01", public_cutoff="2026-10-26")
        self.assertEqual("audit_report_document_candidate", result["status"])
        self.assertEqual(2, result["audit_report_count"])
        self.assertEqual("unverified", result["financial_statement_status"])
        self.assertFalse(result["cohort_eligible"])

    def test_regular_report_is_prioritized(self):
        row, raw = fixture(["[기재정정]분기보고서 (2026.03)", "감사보고서 (2025.12)"])
        result = classify_candidate(row, raw, begin_date="2023-01-01", public_cutoff="2026-10-26")
        self.assertEqual("regular_report_found", result["status"])
        self.assertEqual(1, result["regular_report_count"])

    def test_missing_or_changed_raw_never_becomes_eligible(self):
        row, raw = fixture(["감사보고서 (2025.12)"])
        self.assertEqual("review_required", classify_candidate(
            row, None, begin_date="2023-01-01", public_cutoff="2026-10-26")["status"])
        with self.assertRaisesRegex(ValueError, "source_hash_mismatch"):
            classify_candidate(row, raw + b" ", begin_date="2023-01-01", public_cutoff="2026-10-26")

    def test_no_data_is_distinct_from_missing_source(self):
        result = classify_candidate({"corp_code": "12345678", "status": "no_data"}, None,
                                    begin_date="2023-01-01", public_cutoff="2026-10-26")
        self.assertEqual("no_disclosure_in_window", result["status"])
        self.assertEqual(0, result["regular_report_count"])
        self.assertFalse(result["cohort_eligible"])


if __name__ == "__main__":
    unittest.main()
