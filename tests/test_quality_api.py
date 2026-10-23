import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.test_research_stage05 import Stage05WorkflowTests
from tests.test_research_storage import research_input


class QualityApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Stage05WorkflowTests.setUp(self)
        self.report = await Stage05WorkflowTests.execute(self)
        with self.store.connection(write=True) as conn:
            conn.execute('INSERT INTO research_validations VALUES (?,?,?,?,?,?,?)',
                         ('e' * 32, 'numeric', None, 'V07_source_coverage', 'not_checked',
                          '{"reason":"matching_original_not_available"}', '2026-10-21'))

    async def test_report_quality_uses_its_published_batch_and_review_is_metadata_only(self):
        import server
        with TestClient(server.app, base_url='http://127.0.0.1:8100') as client:
            with patch.object(server.app.state, 'research_store', self.store, create=True):
                response = client.get('/reports/' + self.report['id'] + '/quality')
                self.assertEqual(200, response.status_code, response.text)
                quality = response.json()
                self.assertEqual('numeric', quality['validation_batch_id'])
                self.assertEqual('quality-report-1.0', quality['schema_version'])
                exception = quality['exceptions'][0]
                saved = client.post('/researches/' + self.research + '/quality-exceptions/' + exception['exception_id'] + '/reviews', json={
                    'batch_id': 'numeric', 'action': 'note_added', 'note': '원문 확보 요청 예정'})
                self.assertEqual(201, saved.status_code)
                self.assertEqual('local_demo_operator', saved.json()['actor_type'])
                reread = client.get('/reports/' + self.report['id'] + '/quality').json()
                self.assertEqual('note_added', reread['exceptions'][0]['reviews'][0]['action'])
                self.assertEqual('published', client.get('/reports/' + self.report['id']).json()['status'])

    async def test_review_rejects_unknown_exception_and_cross_research_batch(self):
        import server
        with TestClient(server.app, base_url='http://127.0.0.1:8100') as client:
            with patch.object(server.app.state, 'research_store', self.store, create=True):
                response = client.post('/researches/' + self.research + '/quality-exceptions/' + '0' * 64 + '/reviews', json={
                    'batch_id': 'numeric', 'action': 'note_added', 'note': 'nope'})
                self.assertEqual(404, response.status_code)
                other = self.store.create_research(research_input('other-quality-research'))
                response = client.post('/researches/' + other['id'] + '/quality-exceptions/' + '0' * 64 + '/reviews', json={
                    'batch_id': 'numeric', 'action': 'note_added', 'note': 'nope'})
                self.assertEqual(409, response.status_code)
