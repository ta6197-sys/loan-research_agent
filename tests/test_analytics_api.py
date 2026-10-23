import asyncio
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.research_api import router


class AnalyticsApiContractTests(unittest.TestCase):
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
        finally:
            case.doCleanups()
