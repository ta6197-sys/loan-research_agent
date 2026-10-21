"""같은 게시본의 화면·PDF·ZIP, 원문 위조·버전 혼합·외부 전송 경계를 확인한다."""
from copy import deepcopy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile

from pypdf import PdfReader
from agent.finance.report_view import build_view,compare_views,text,public_url
from agent.finance.report_exports import ReportExports,markdown,markdown_zip,tables
from agent.research_store import IntegrityFailure,Conflict
from tests import test_research_stage05 as fixtures


class OutputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        fixtures.Stage05WorkflowTests.setUp(self)
        self.context.update(company={'name':'합성 시험기업','corp_code':'00129642','stock_code':'041650'},
            purpose='합성 출력 시험',public_cutoff='2026-10-20')
        self.context['financial_rows'][0].update(end='2026-06-30',label='현금및현금성자산',currency='KRW',unit='원',
            period={'kind':'instant','as_of':'2026-06-30'})
        self.context['unknowns']=[{'evidence_id':'U1','state':'withheld','text':'구성 미확인'}]
        self.report=await fixtures.Stage05WorkflowTests.execute(self)

    async def execute(self,*args,**kwargs):return await fixtures.Stage05WorkflowTests.execute(self,*args,**kwargs)

    async def test_view_preserves_exact_value_and_maps_verified_raw_location(self):
        view=build_view(self.store,self.report['id'])
        self.assertEqual('100',view['financial_rows'][0]['value'])
        self.assertEqual('100',view['evidence'][0]['raw_value'])
        self.assertEqual('verified',view['evidence'][0]['use_status'])
        self.assertEqual(self.report['publication']['content_hash'],view['report_hash'])
        self.assertNotIn('storage_key',json.dumps(view));self.assertNotIn('owner_id',json.dumps(view))

    async def test_old_source_metadata_without_optional_restrictions_remains_readable(self):
        original=self.store.get_evidence
        def legacy(fid):
            item=deepcopy(original(fid));item['source']['metadata'].pop('usage_restrictions',None);return item
        with patch.object(self.store,'get_evidence',side_effect=legacy):
            self.assertEqual([],build_view(self.store,self.report['id'])['evidence'][0]['usage_restrictions'])

    async def test_pdf_and_zip_share_version_and_keep_korean_unknown_and_exact_numbers(self):
        service=ReportExports(self.store)
        pdf=service.create(self.report['id'],'pdf');zipmeta=service.create(self.report['id'],'markdown_zip')
        self.assertEqual(pdf['view_hash'],zipmeta['view_hash']);self.assertEqual(pdf['report_hash'],zipmeta['report_hash'])
        _,data=service.download(pdf['id']);reader=PdfReader(io.BytesIO(data));content=''.join(p.extract_text() for p in reader.pages)
        self.assertIn('합성 시험기업',content);self.assertIn('미확인',content);self.assertIn('100',content)
        _,data=service.download(zipmeta['id'])
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            self.assertIn('report.md',archive.namelist());self.assertIn('evidence.json',archive.namelist())
            manifest=json.loads(archive.read('manifest.json'));self.assertFalse(manifest['raw_sources_included'])
            self.assertFalse(manifest['user_attachments_included'])
            self.assertEqual(self.report['id'],manifest['report_id'])
            self.assertNotIn(str(self.root),archive.read('report.md').decode())
        self.assertEqual(pdf,service.create(self.report['id'],'pdf'))

    async def test_markdown_escapes_instructions_and_preserves_large_decimal(self):
        view=build_view(self.store,self.report['id']);view['purpose']='![secret](https://example.invalid/x) <script> /Users/private/key.txt'
        view['financial_rows'][0]['value']='9007199254740993.123456789'
        result=markdown(view)
        self.assertIn('9,007,199,254,740,993.123456789',result)
        self.assertNotIn('/Users/private/key.txt',result);self.assertNotIn('![secret]',result);self.assertNotIn('<script>',result)
        self.assertEqual(markdown_zip(view),markdown_zip(view))

    async def test_raw_source_tamper_blocks_view_and_output(self):
        fid=self.context['financial_rows'][0]['fact_ids'][0];source=self.store.get_evidence(fid)['source']['id']
        with self.store.connection() as conn:key=conn.execute('SELECT storage_key FROM research_sources WHERE id=?',(source,)).fetchone()[0]
        (self.store.source_root/key).write_bytes(b'tampered')
        with self.assertRaises(IntegrityFailure):build_view(self.store,self.report['id'])
        with self.assertRaises(IntegrityFailure):ReportExports(self.store).create(self.report['id'],'pdf')

    async def test_version_comparison_requires_same_research_and_keeps_unknown_status(self):
        first=build_view(self.store,self.report['id']);second=deepcopy(first);second['version']=2
        second['unknowns'][0]['status']='answered_unverified';second['user_observations']=[{'text':'합성 답변'}]
        result=compare_views(first,second);self.assertEqual([],result['financial_changes']);self.assertEqual(1,result['added_user_input_count'])
        self.assertEqual('answered_unverified',result['issue_changes'][0]['after'])
        second['research_id']='other'
        with self.assertRaises(Conflict):compare_views(first,second)

    async def test_export_corruption_is_rejected_and_failed_export_does_not_change_report(self):
        before=deepcopy(self.store.get_report(self.report['id']));service=ReportExports(self.store)
        with patch('agent.finance.report_exports.pdf',side_effect=OSError('synthetic output failure')):
            with self.assertRaises(OSError):service.create(self.report['id'],'pdf')
        self.assertEqual(before,self.store.get_report(self.report['id']))
        meta=service.create(self.report['id'],'markdown_zip');(self.store.source_root/'_exports'/meta['id']/'report.zip').write_bytes(b'broken')
        with self.assertRaises(IntegrityFailure):service.download(meta['id'])
        with self.assertRaises(ValueError):service.download('../outside')

    async def test_proxy_rejects_arbitrary_path_and_official_link_credentials(self):
        import web_demo_server
        from fastapi.testclient import TestClient
        client=TestClient(web_demo_server.app,base_url='http://127.0.0.1')
        try:
            self.assertEqual(403,client.get('/api/run_shell').status_code)
            self.assertEqual(403,client.post('/api/reports/anything/exports',json={}).status_code)
            self.assertEqual(403,client.post('/api/researches/start',headers={'origin':'https://external.invalid'},json={}).status_code)
        finally:client.close()
        self.assertIsNone(public_url('https://user:password@example.invalid'))
        self.assertIsNone(public_url('https://example.invalid/?api_key=secret'))
        self.assertIsNone(public_url('file:///Users/private/file'))
        self.assertEqual('[로컬 경로 생략]',text('/Users/private/path'))
