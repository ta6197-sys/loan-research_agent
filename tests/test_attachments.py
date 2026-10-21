"""첨부 읽기 한계·비실행·묶음 접수/복구·미검증 후속 상태를 검증한다."""
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile
from pypdf import PdfWriter
from agent.finance.attachment_parser import parse_attachment,AttachmentLimit
from agent.finance.attachments import AttachmentService
from agent.finance.research_runtime import ResearchRuntime
from agent.research_store import Conflict,IntegrityFailure
from tests import test_research_followup as fixtures


def xlsx(extra=None):
    files={'xl/workbook.xml':'''<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="숨김" sheetId="1" state="veryHidden" r:id="rId1"/></sheets></workbook>''',
      'xl/_rels/workbook.xml.rels':'<Relationships><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
      'xl/worksheets/sheet1.xml':'''<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><cols><col min="1" max="1" hidden="1"/></cols><sheetData><row r="1" hidden="1"><c r="A1" t="inlineStr"><is><t>000123</t></is></c><c r="B1"><f>1+1</f><v>2</v></c><c r="C1"><f>9+9</f></c><c r="D1" t="str"><f>HYPERLINK("https://example.invalid")</f><v>cached text</v></c></row></sheetData></worksheet>''',
      'xl/externalLinks/externalLink1.xml':'<externalLink/>'}
    files.update(extra or {});out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:
        for name,value in files.items():z.writestr(name,value)
    return out.getvalue()


class AttachmentParserTests(unittest.TestCase):
    def test_csv_keeps_leading_zero_and_formula_as_unexecuted_text(self):
        result=parse_attachment('file.csv',b'code,value\n000123,=1+1\n')
        self.assertEqual('000123',result['locations'][2]['text']);self.assertEqual('=1+1',result['locations'][3]['text'])
        self.assertEqual('not_performed',result['financial_verification'])

    def test_xlsx_hidden_cells_cached_formula_and_missing_cache_are_distinct(self):
        result=parse_attachment('file.xlsx',xlsx());cells={c['cell']:c for c in result['locations']}
        self.assertEqual('000123',cells['A1']['text']);self.assertTrue(cells['A1']['hidden_column'])
        self.assertTrue(all(c['hidden_sheet'] and c['hidden_row'] for c in cells.values()))
        self.assertEqual('2',cells['B1']['formula_cached_value']);self.assertEqual('freshness_unverified',cells['B1']['cache_state'])
        self.assertEqual('missing',cells['C1']['cache_state']);self.assertEqual('partial',result['status'])
        self.assertIn('external_links_not_opened',result['warnings'])

    def test_xlsx_traversal_entities_and_macros_are_rejected(self):
        for extra in ({'../escape.xml':'bad'},{'xl/vbaProject.bin':'bad'},
            {'xl/workbook.xml':'<!DOCTYPE root [<!ENTITY x SYSTEM "file:///etc/passwd">]><root>&x;</root>'}):
            with self.assertRaises(ValueError):parse_attachment('file.xlsx',xlsx(extra))

    def test_blank_pdf_encryption_and_page_limit_are_not_full_read(self):
        writer=PdfWriter();writer.add_blank_page(width=200,height=200);out=io.BytesIO();writer.write(out)
        result=parse_attachment('file.pdf',out.getvalue());self.assertEqual([1],result['unreadable_pages']);self.assertFalse(result['complete_read'])
        writer.encrypt('synthetic-only');out=io.BytesIO();writer.write(out)
        self.assertEqual('encrypted',parse_attachment('file.pdf',out.getvalue())['status'])
        with patch('agent.finance.attachment_parser.MAX_PAGES',0):
            plain=PdfWriter();plain.add_blank_page(width=200,height=200);out=io.BytesIO();plain.write(out)
            with self.assertRaises(AttachmentLimit):parse_attachment('file.pdf',out.getvalue())


class AttachmentFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.FollowupTests.asyncSetUp(self);self.attachments=AttachmentService(self.store)

    def value(self,*args,**kwargs):return fixtures.FollowupTests.value(self,*args,**kwargs)

    def bundle(self):return self.attachments.stage(self.rid,self.first['id'],[('first.txt','합성 미검증 일정입니다.'.encode()),('second.csv',b'code,value\n000123,7')])

    async def test_multiple_files_one_input_one_run_and_old_report_preserved(self):
        bundle=self.bundle();self.assertEqual(2,len(bundle['files']))
        self.assertEqual(bundle,self.bundle());value=self.value(attachment_bundle_id=bundle['bundle_id'])
        receipt=self.service.receive(self.rid,value);again=self.service.receive(self.rid,value)
        self.assertEqual(receipt['run_id'],again['run_id'])
        await self.service.drain();latest=self.store.get_research(self.rid)['reports'][-1]
        self.assertEqual(2,latest['version']);self.assertEqual(self.first,self.store.get_report(self.first['id']))
        self.assertEqual('user_provided_unverified',latest['content']['context']['user_observations'][0]['status'])
        self.assertEqual(2,len(latest['content']['context']['user_observations'][0]['attachments']))
        self.assertEqual(1,self.store.get_run(receipt['run_id'])['usage']['model_calls'])
        with self.store.connection() as conn:
            self.assertEqual(2,conn.execute("SELECT count(*) FROM research_sources WHERE run_id=? AND json_extract(metadata_json,'$.reporting_period.operation')='followup_attachment'",(receipt['run_id'],)).fetchone()[0])

    async def test_partial_asset_save_waits_and_retry_does_not_reset_budget(self):
        bundle=self.bundle();value=self.value(attachment_bundle_id=bundle['bundle_id'])
        with patch.object(AttachmentService,'attach_to_run',side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):self.service.receive(self.rid,value)
            run=self.store.get_research(self.rid)['runs'][-1];self.assertTrue(run['trigger']['pending_attachment_input_ids'])
            runtime=ResearchRuntime(self.store,self.service,self.model);runtime.start()
            import asyncio
            await asyncio.sleep(.05);self.assertEqual(0,self.store.get_run(run['id'])['usage']['model_calls']);await runtime.close()
        receipt=self.service.receive(self.rid,value);self.assertEqual(run['id'],receipt['run_id'])
        self.assertFalse(self.store.get_run(run['id'])['trigger']['pending_attachment_input_ids'])
        await self.service.drain();self.assertEqual(1,self.store.get_run(run['id'])['usage']['model_calls'])

    async def test_foreign_bundle_tamper_limit_and_memo_do_not_start_investigation(self):
        bundle=self.bundle()
        with self.assertRaises(Conflict):self.attachments.get(bundle['bundle_id'],'other-research')
        memo=self.service.receive(self.rid,self.value(kind='memo',attachment_bundle_id=bundle['bundle_id']))
        self.assertIsNone(memo['run_id']);self.assertEqual(1,len(self.store.get_research(self.rid)['runs']))
        with self.assertRaises(ValueError):self.attachments.stage(self.rid,self.first['id'],[('../bad.txt',b'bad')])
        file=self.attachments.path(bundle['bundle_id'])/'0.txt';file.write_bytes(b'tampered')
        with self.assertRaises(IntegrityFailure):self.attachments.get(bundle['bundle_id'])
