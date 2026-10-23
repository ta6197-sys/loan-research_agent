import unittest

from agent.finance.statistical_analysis import analyze, observations_from_financial_analytics


def observation(company, metric, value, *, end="2026-06-30", industry="manufacturing"):
    return {"company_id": company, "industry_group": industry, "metric": metric, "value": str(value),
            "scope": "OFS", "period_kind": "duration", "period_end": end, "currency": "KRW",
            "accounting_standard": "K-IFRS", "public_at": "2026-08-15", "verification_status": "supported"}


class StatisticalAnalysisTests(unittest.TestCase):
    def cohort(self):
        values = []
        for company in range(20):
            for end, value in (("2025-06-30", 10 + company), ("2026-06-30", 11 + company)):
                values.append(observation("c" + str(company), "operating_margin", value, end=end))
        values[-1]["value"] = "100"
        return values

    def test_small_peer_group_is_withheld_not_scored(self):
        result = analyze("c0", [observation("c0", "operating_margin", 10), observation("c1", "operating_margin", 11)])
        comparison = result["peer_comparisons"][0]
        self.assertEqual("insufficient_peer_data", comparison["status"])
        self.assertNotIn("robust_outlier", comparison)

    def test_peer_descriptive_rank_and_robust_z_have_explicit_gate(self):
        result = analyze("c19", self.cohort(), analysis_as_of="2026-12-31")
        comparison = next(item for item in result["peer_comparisons"] if item["comparison_definition"]["period_end"] == "2026-06-30")
        self.assertEqual(("measured", 20, 20), (comparison["status"], comparison["sample_size"], comparison["distinct_company_count"]))
        self.assertEqual("measured", comparison["robust_outlier"]["status"])
        self.assertTrue(comparison["robust_outlier"]["outside_threshold"])
        self.assertIn("iqr", comparison["descriptive"])

    def test_zero_mad_does_not_produce_a_robust_score(self):
        data = [observation("c" + str(index), "operating_margin", 10) for index in range(20)]
        comparison = analyze("c0", data)["peer_comparisons"][0]
        self.assertEqual(("unmeasured", "zero_mad"), (comparison["robust_outlier"]["status"], comparison["robust_outlier"]["reason"]))

    def test_trend_is_observed_only_and_incompatible_dates_are_withheld(self):
        data = [observation("target", "operating_margin", 10, end="2025-06-30"), observation("target", "operating_margin", 15, end="2026-09-30")]
        trend = analyze("target", data)["trend_analysis"][0]
        self.assertEqual("withheld", trend["period_changes"][0]["status"])
        self.assertEqual("withheld", trend["long_term_or_seasonal_inference"]["status"])

    def test_relationship_requires_matched_peer_sample_and_converter_excludes_withheld(self):
        analytics = {"results": [{"id": "x", "name": "operating_margin", "value": "10", "unit": "percent", "scope": "OFS", "period": {"kind": "duration", "end": "2026-06-30"}, "status": "supported"}, {"id": "y", "name": "net_margin", "value": "9", "status": "withheld"}]}
        converted = observations_from_financial_analytics("target", "manufacturing", analytics, accounting_standard="K-IFRS", currency="KRW", public_at="2026-08-15")
        self.assertEqual(1, len(converted))
        relation = analyze("target", converted)["relationship_analysis"][0]
        self.assertEqual("insufficient_peer_data", relation["status"])

    def test_spearman_is_exploratory_and_withholds_p_value(self):
        data = []
        for index in range(20):
            data.extend([observation("c" + str(index), "revenue_growth", index), observation("c" + str(index), "inventory_growth", index * 2)])
        relation = analyze("c0", data)["relationship_analysis"][0]
        self.assertEqual(("measured", "1", None, "withheld"),
                         (relation["status"], relation["coefficient"], relation["p_value"], relation["p_value_status"]))
