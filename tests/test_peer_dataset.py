import unittest

from agent.finance.model_readiness import evaluate
from agent.finance.peer_dataset import build_snapshot, collection_expansion_plan


CORE = ("operating_margin", "net_margin", "liabilities_to_assets", "operating_cashflow_to_revenue", "revenue_growth")


def record(corp="001", end="2024-12-31", *, source_hash="hash", correction="current_verified", missing=()):
    features = {name: {"status": "supported", "value": "1.25", "evidence_refs": ["fact-" + name, "validation-" + name]}
                for name in CORE if name not in missing}
    return {"corp_code": corp, "industry_code": "C29", "period_start": end[:4] + "-01-01", "period_end": end,
            "period_kind": "duration", "scope": "OFS", "public_cutoff": "2026-10-23", "source_receipt": "20260330000001",
            "source_hash": source_hash, "validation_batch_id": "batch-" + corp + end, "source_public_at": "2026-03-30",
            "correction_status": correction, "account_definition_version": "account-map-1", "peer_group": "manufacturing-C29", "features": features,
            "data_origin": "synthetic_test_only", "verification_gates": {"status": "synthetic_test_only"}}


class PeerDatasetTests(unittest.TestCase):
    def test_withheld_features_are_missing_not_zero_and_correction_is_excluded(self):
        source = record(missing=("net_margin",)); source["features"]["net_margin"] = {"status": "withheld", "reason": "v11_missing"}
        future = record(corp="003"); future["source_public_at"] = "2027-01-01"
        snapshot = build_snapshot([source, record(corp="002", correction="unverified"), future])
        self.assertNotIn("net_margin", snapshot["rows"][0]["features"])
        self.assertEqual("v11_missing", snapshot["rows"][0]["missing_features"]["net_margin"])
        self.assertEqual(0, snapshot["feature_missingness"]["net_margin"]["available_count"])
        self.assertEqual("correction_relation_not_verified", snapshot["excluded_records"][0]["reason"])
        self.assertEqual("source_not_public_at_cutoff", snapshot["excluded_records"][1]["reason"])

    def test_ambiguous_duplicate_is_never_arbitrarily_selected(self):
        snapshot = build_snapshot([record(source_hash="a"), record(source_hash="b")])
        self.assertEqual(0, snapshot["row_count"])
        self.assertEqual("ambiguous_duplicate_company_period_scope", snapshot["excluded_records"][0]["reason"])

    def test_readiness_records_insufficiency_and_ready_snapshot_is_reproducible(self):
        small = build_snapshot([record()])
        self.assertEqual("not_trained_insufficient_data", evaluate(small)["training_status"])
        records = [record(str(company).zfill(3), str(year) + "-12-31", source_hash="hash-" + str(company) + "-" + str(year))
                   for company in range(30) for year in range(2022, 2027)]
        snapshot = build_snapshot(records)
        readiness = evaluate(snapshot)
        self.assertEqual((150, "ready_for_exploratory_training"), (snapshot["row_count"], readiness["training_status"]))
        self.assertEqual(snapshot["snapshot_id"], build_snapshot(list(reversed(records)))["snapshot_id"])

    def test_collection_plan_keeps_verification_as_a_required_gate(self):
        plan = collection_expansion_plan()
        self.assertIn("OpenDART 응답만으로 verified 처리 금지", plan["prohibitions"])
        self.assertEqual("required_no_auto_approval", plan["phases"][2]["status"])

    def test_actual_public_row_needs_all_four_gates(self):
        item = record(); item['data_origin'] = 'verified_public_disclosure'
        self.assertEqual('four_source_verification_gates_not_passed',
                         build_snapshot([item])['excluded_records'][0]['reason'])
        item['verification_gates'] = dict.fromkeys(
            ('original_api', 'actual_period', 'fiscal_month', 'independent_xbrl'), 'pass')
        self.assertEqual(1, build_snapshot([item])['row_count'])
        item['verification_gates']['independent_xbrl'] = 'withheld'
        self.assertEqual(0, build_snapshot([item])['row_count'])
