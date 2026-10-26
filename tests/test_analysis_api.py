import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent.research_api import router


class AnalysisApiTests(unittest.TestCase):
    def setUp(self):
        # Batch service와 보고서 조회는 실제 저장소 fixture를 사용한다.
        from tests import test_research_stage05 as fixtures
        case = fixtures.Stage05WorkflowTests(); case.setUp(); self.case = case
        import asyncio
        self.report = asyncio.run(case.execute()); self.store = case.store
        self.app = FastAPI(); self.app.state.research_store = self.store; self.app.include_router(router)

    def test_read_only_financial_analysis_contract_with_explicit_ml_gap(self):
        with TestClient(self.app) as client:
            before = self.store.get_report(self.report['id'])
            result = client.get('/reports/' + self.report['id'] + '/financial-analysis')
            self.assertEqual(200, result.status_code, result.text)
            payload = result.json()
            self.assertEqual(('report-analysis-1.0', self.report['id'], 'not_trained_insufficient_data'),
                             (payload['analysis_version'], payload['report_id'], payload['ml_readiness']['training_status']))
            self.assertEqual('verified_peer_dataset_not_available', payload['statistical_analysis']['reason_code'])
            self.assertEqual('analysis-plan-1.0', payload['analysis_plan']['version'])
            self.assertTrue(all(step['status'] in {'candidate_for_followup', 'evidence_required'}
                                for step in payload['analysis_plan']['steps']))
            self.assertEqual(before, self.store.get_report(self.report['id']))

    def test_quality_analysis_has_contract_metadata_and_reproduces_batch(self):
        with TestClient(self.app) as client:
            batch_id = self.report['publication']['validation_batch_id']
            result = client.get('/validation-batches/' + batch_id + '/quality-analysis')
            self.assertEqual(200, result.status_code, result.text)
            payload = result.json()
            self.assertEqual(('quality:' + batch_id, 'quality-analytics-1.0', batch_id),
                             (payload['analysis_id'], payload['analysis_version'], payload['validation_batch_id']))
            self.assertIn('rule_distribution', payload)

    def test_local_proxy_allows_new_read_only_analysis_paths(self):
        import web_demo_server
        with TestClient(web_demo_server.app, base_url='http://127.0.0.1') as client:
            report = 'a' * 32; batch = 'b' * 32
            # 백엔드가 없는 테스트 환경에서는 502가 정상이며, 403은 allowlist 누락이다.
            self.assertNotEqual(403, client.get('/api/reports/' + report + '/financial-analysis').status_code)
            self.assertNotEqual(403, client.get('/api/validation-batches/' + batch + '/quality-analysis').status_code)
