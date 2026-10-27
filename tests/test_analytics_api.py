import asyncio
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi import HTTPException
from fastapi.testclient import TestClient

from agent.research_api import router, _run_verified_analysis_payload
from agent.finance.broad_303_read_model import read_company


class AnalyticsApiContractTests(unittest.TestCase):
    def test_broad_303_read_only_snapshot_endpoint_and_integrity(self):
        app=FastAPI();app.include_router(router)
        with TestClient(app) as client:
            response=client.get('/cohorts/broad-303/companies/00157539/analysis?scope=OFS')
            self.assertEqual(200,response.status_code)
            payload=response.json()
            self.assertEqual('exploratory_observed_303_family',payload['cohort_id'])
            self.assertEqual('unconfirmed',payload['classification_revision'])
            self.assertEqual('not_trained_insufficient_data',payload['ml_training_status'])
            self.assertGreater(payload['company_period_count'],0)
            self.assertTrue(all(row['status'] in {'measured','insufficient_peer_data'}
                                for row in payload['peer_comparisons']))
            self.assertEqual(404,client.get('/cohorts/broad-303/companies/00000000/analysis').status_code)
            self.assertEqual(422,client.get('/cohorts/broad-303/companies/bad/analysis').status_code)
            self.assertEqual(422,client.get('/cohorts/broad-303/companies/00157539/analysis?scope=bad').status_code)
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from agent.finance import broad_303_read_model
        with TemporaryDirectory() as directory:
            changed=Path(directory)/'snapshot.json'
            changed.write_text(broad_303_read_model.SNAPSHOTS['OFS'].read_text().replace('"30399"','"30398"',1))
            with patch.dict(broad_303_read_model.SNAPSHOTS,{'OFS':changed}):
                with self.assertRaisesRegex(ValueError,'exploratory_snapshot_integrity_failed'):
                    read_company('00157539')

    def test_unresolved_correction_history_blocks_run_preview_before_metrics(self):
        store = Mock()
        store.get_run.return_value = {'status': 'finished', 'trigger': {'kind': 'general_company',
            'collection_result': {'next_phase': 'numeric_and_xbrl_validation',
                'fiscal_period': {'status': 'supported', 'month': 12},
                'target_reports': [{'year': 2023, 'report_code': '11011'},
                                   {'year': 2024, 'report_code': '11011'},
                                   {'year': 2025, 'report_code': '11012'}]}}}
        batches = [{'id': 'numeric', 'policy_version': 'numeric-validation-1.30'},
                   {'id': 'mapping', 'policy_version': 'xbrl-mapping-1.7'}]
        numeric = {'validations': [{'id': 'V1'}], 'summary': {'disclosure_history_audit': {'status': 'not_checked'}}}
        mapping = {'validations': [{'id': 'X1'}]}
        with patch('agent.research_api.NumericValidationService') as service:
            service.return_value.history.return_value = batches
            service.return_value.batch.side_effect = [numeric, mapping]
            with self.assertRaises(HTTPException) as failure:
                _run_verified_analysis_payload(store, 'run')
        self.assertEqual(409, failure.exception.status_code)
        store.get_evidence.assert_not_called()

    def test_metric_contract_keeps_required_identifiers_and_empty_values_have_reason(self):
        from tests import test_research_stage05 as fixtures
        case = fixtures.Stage05WorkflowTests(); case.setUp()
        try:
            report = asyncio.run(case.execute())
            app = FastAPI(); app.state.research_store = case.store; app.include_router(router)
            with TestClient(app) as client:
                payload = client.get('/reports/' + report['id'] + '/financial-analysis').json()
            self.assertEqual({'analysis_id', 'analysis_version', 'report_id', 'validation_batch_id', 'status'}, set(payload) & {'analysis_id', 'analysis_version', 'report_id', 'validation_batch_id', 'status'})
            for metric in payload['financial_metrics']:
                self.assertTrue({'metric_id', 'value', 'period', 'scope', 'input_fact_ids', 'input_validation_ids', 'source_ids', 'method_version', 'limitations', 'reason_code'} <= set(metric))
                if metric['value'] is None:
                    self.assertIsNotNone(metric['reason_code'])
            with TestClient(app) as client:
                preview = client.get('/runs/' + report['run_id'] + '/verified-analysis')
            self.assertEqual(409, preview.status_code)
        finally:
            case.doCleanups()
