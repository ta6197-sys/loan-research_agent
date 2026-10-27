from unittest import TestCase
import json
from pathlib import Path

from agent.finance.model_readiness import evaluate
from agent.finance.peer_dataset import build_snapshot
from agent.finance.analytics_dataset_service import AnalyticsDatasetService
from agent.finance.statistical_analysis import analyze


class VerifiedUnpublishedPeerTests(TestCase):
    def test_expanded_snapshot_audit_keeps_isolated_provenance_and_gates(self):
        root=Path(__file__).resolve().parents[1]
        audit=json.loads((root/'doc/finance/BROAD_303_EXPANDED_AUDIT_20261027.json').read_text())
        self.assertEqual(26,len(audit['company_reviews']))
        self.assertEqual(19,sum(row['source_store']=='isolated' and
                                row['eligible_observations']>0 for row in audit['company_reviews']))
        self.assertEqual(996,audit['external_requests_total'])
        self.assertEqual(0,audit['model_calls_total'])
        for scope,companies,rows,measured in (('OFS',21,86,40),('CFS',20,80,0)):
            summary=audit['scopes'][scope]
            snapshot=json.loads((root/f'doc/finance/{summary["snapshot_file"]}').read_text())
            self.assertEqual(summary['snapshot_id'],snapshot['snapshot_id'])
            self.assertEqual(rows,snapshot['row_count'])
            self.assertEqual(companies,summary['distinct_companies'])
            self.assertEqual(measured,summary['measured_peer_comparisons'])
            self.assertEqual('not_trained_insufficient_data',summary['training_status'])
            self.assertFalse(summary['industry_revision_gate']['pass'])
            self.assertTrue(all(row['industry_version']=='unconfirmed' and
                                row['scope']==scope and row['report_id'] is None and
                                row['verification_gates']['independent_xbrl']=='pass'
                                for row in snapshot['rows']))
            observations=AnalyticsDatasetService._observations(snapshot)
            comparisons=[item for code in sorted({r['corp_code'] for r in snapshot['rows']})
                         for item in analyze(code,observations,analysis_as_of=audit['public_cutoff'])['peer_comparisons']
                         if item['status']=='measured']
            self.assertEqual(measured,len(comparisons))
            self.assertTrue(all(item['comparison_definition']['metric']=='liabilities_to_assets'
                                for item in comparisons))

    def test_stored_public_snapshot_keeps_evidence_and_period_exclusion(self):
        root=Path(__file__).resolve().parents[1]
        audit=json.loads((root/'doc/finance/BROAD_303_VERIFIED_AUDIT_20261027.json').read_text())
        saeron=next(item for item in audit['run_audits'] if item['corp_code']=='00305668')
        self.assertTrue(any(item['period_end']=='2023-12-31' and
                            item['reason']=='industry_code_differs_from_stable_candidate'
                            for item in saeron['excluded']))
        for scope in ('OFS','CFS'):
            snapshot=json.loads((root/f'doc/finance/BROAD_303_VERIFIED_{scope}_20261027.json').read_text())
            self.assertEqual(9,snapshot['row_count'])
            self.assertEqual(2,len({row['corp_code'] for row in snapshot['rows']}))
            self.assertTrue(all(row['scope']==scope and row['report_id'] is None and
                                row['industry_version']=='unconfirmed' and
                                row['verification_gates']['independent_xbrl']=='pass' and
                                row['source_manifest'] and row['features'] and
                                row['accounting_standard_evidence']['excerpt_sha256']
                                for row in snapshot['rows']))

    def test_unconfirmed_industry_revision_blocks_ml_even_with_large_sample(self):
        rows=[]
        for company in range(30):
            for year in range(2020,2025):
                rows.append({'corp_code':f'{company:08d}','industry_code':'303',
                    'industry_system':'OpenDART_XBRL_observed_family','industry_version':'unconfirmed',
                    'period_start':f'{year}-01-01','period_end':f'{year}-12-31',
                    'period_kind':'duration','scope':'OFS','public_cutoff':'2026-10-27',
                    'source_receipt':f'{year+1}0331000001',
                    'source_hash':f'hash-{company}-{year}','source_public_at':f'{year+1}-03-31',
                    'validation_batch_id':f'batch-{company}-{year}',
                    'account_definition_version':'verified-peer-account-definitions-1.0',
                    'peer_group':'exploratory_observed_303_family','correction_status':'current_verified',
                    'data_origin':'verified_public_disclosure',
                    'verification_gates':{key:'pass' for key in
                        ('original_api','actual_period','fiscal_month','independent_xbrl')},
                    'features':{name:{'status':'supported','value':'1','evidence_refs':[{'fact_ids':['f']}]} for name in
                        ('operating_margin','net_margin','liabilities_to_assets',
                         'operating_cashflow_to_revenue','revenue_growth')}})
        snapshot=build_snapshot(rows)
        result=evaluate(snapshot)
        self.assertEqual(150,result['annual_row_count'])
        self.assertFalse(result['checks']['industry_revision']['pass'])
        self.assertEqual('not_trained_insufficient_data',result['training_status'])
