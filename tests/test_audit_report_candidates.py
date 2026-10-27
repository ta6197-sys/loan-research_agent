import unittest

from agent.finance.audit_report_candidates import statement_period_candidates


LIBRARY = '''<DOCUMENT><BODY><SECTION-1><TITLE>(첨부)재 무 제 표</TITLE>
<LIBRARY><TABLE-GROUP><TITLE>재 무 상 태 표</TITLE>
<TABLE><TR><TD>2025년 12월 31일 현재</TD></TR></TABLE>
<TABLE><TR><TH>과 목</TH><TH>당기</TH></TR><TR><TD>자산총계</TD><TD>1,000</TD></TR></TABLE>
</TABLE-GROUP></LIBRARY></SECTION-1></BODY></DOCUMENT>'''.encode()


def adjacent():
    return '''<DOCUMENT><BODY><SECTION-1><TITLE>(첨부)재 무 제 표</TITLE>
<TABLE><TR><TD>포 괄 손 익 계 산 서 2025년 1월 1일부터 2025년 12월 31일까지</TD></TR></TABLE>
<TABLE><TR><TH>과 목</TH><TH>당기</TH></TR><TR><TD>매출액</TD><TD>100</TD></TR></TABLE>
</SECTION-1></BODY></DOCUMENT>'''.encode()


class AuditReportCandidateTests(unittest.TestCase):
    def test_library_table_group_keeps_candidate_only(self):
        result = statement_period_candidates(LIBRARY)
        self.assertEqual(len(result["candidates"]), 1)
        row = result["candidates"][0]
        self.assertEqual((row["statement"], row["period_dates_candidate"]), ("BS", ["2025-12-31"]))
        self.assertTrue(row["title_location"]["raw_span_verified"])
        self.assertTrue(row["data_location"]["raw_span_verified"])
        self.assertEqual((row["status"], row["scope"], result["financial_verification"]),
                         ("candidate_only", "unknown", "not_performed"))

    def test_adjacent_tables_derive_only_dates_not_columns(self):
        row = statement_period_candidates(adjacent())["candidates"][0]
        self.assertEqual(row["statement"], "CIS")
        self.assertEqual(row["period_dates_candidate"], ["2025-01-01", "2025-12-31"])
        self.assertIn("period_column_mapping_unconfirmed", row["limitations"])

    def test_no_attachment_or_missing_account_heading_is_excluded(self):
        self.assertEqual(statement_period_candidates(adjacent().replace(
            "(첨부)재 무 제 표".encode(), "주석".encode()))["candidates"], [])
        self.assertEqual(statement_period_candidates(adjacent().replace(
            "과 목".encode(), "기타".encode()))["candidates"], [])

    def test_result_does_not_promote_amount_or_unknown_scope(self):
        result = statement_period_candidates(adjacent())
        row = result["candidates"][0]
        self.assertEqual(row["scope"], "unknown")
        self.assertNotIn("value", row)
        self.assertNotIn("account", row)
        self.assertIn("100", adjacent().decode())
        self.assertNotIn("100", str(row))


if __name__ == "__main__":
    unittest.main()
