"""로컬 추출·위치 저장 시험. 금융 검증 통과를 주장하지 않는다."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

from fastapi.testclient import TestClient

from agent.finance.document_extractor import DocumentExtractor, ExtractionFailure, number
from agent.finance.extraction_service import ExtractionService, candidates
from agent.research_models import ResearchInput, SourceInput
from agent.research_store import ResearchStore, IntegrityFailure


def document(extra='',unit='원',value='197,724,953,870'):
    return f'''<DOCUMENT><SECTION-1><TITLE>III. 재무</TITLE>{extra}
<TABLE-GROUP ACLASS="{{XBRL}}IS_S1"><TITLE>4-2. 포괄손익계산서</TITLE>
<TABLE BORDER="0"><TR><TE>제 52 기 반기 2026.01.01 부터 2026.06.30 까지</TE></TR>
<TR><TE>제 51 기 반기 2025.01.01 부터 2025.06.30 까지</TE></TR>
<TR><TE>(단위 : {unit})</TE></TR></TABLE>
<TABLE BORDER="1"><TR><TH ROWSPAN="2"></TH><TH COLSPAN="2">제 52 기 반기</TH><TH COLSPAN="2">제 51 기 반기</TH></TR>
<TR><TH>3개월</TH><TH>누적</TH><TH>3개월</TH><TH>누적</TH></TR>
<TR><TE>매출액</TE><TE>106,461,983,869</TE><TE>{value}</TE><TE>96,257,111,900</TE><TE>191,738,976,251</TE></TR>
</TABLE></TABLE-GROUP></SECTION-1></DOCUMENT>'''.encode()


class DocumentParserTests(unittest.TestCase):
    def test_multilevel_headers_preserve_quarter_and_cumulative_positions(self):
        result=DocumentExtractor(document()).extract()
        table=result['tables'][1]
        cells=table['rows'][2]['cells']
        self.assertEqual([1,2,3,4,5],[cell['column'] for cell in cells])
        self.assertEqual('2026-04-01',cells[1]['period']['start'])
        self.assertEqual('three_months',cells[1]['period']['column_kind'])
        self.assertEqual('2026-01-01',cells[2]['period']['start'])
        self.assertEqual('cumulative',cells[2]['period']['column_kind'])
        self.assertEqual('197,724,953,870',cells[2]['text'])
        self.assertEqual('OFS',table['scope_hint'])
        self.assertEqual('원',table['unit'])
        self.assertTrue(all(cell['location']['raw_span_verified'] for cell in cells))
        self.assertEqual(4,len(candidates(table)))

    def test_strict_failure_recovers_with_warnings_without_changing_original(self):
        content=document('<P>R&D</P>');before=hashlib.sha256(content).hexdigest()
        result=DocumentExtractor(content).extract()
        self.assertTrue(result['recovered'])
        self.assertTrue(any(w['phase']=='strict' for w in result['warnings']))
        self.assertEqual(before,result['source_hash'])
        self.assertEqual('not_performed',result['financial_verification'])
        with self.assertRaises(ExtractionFailure):
            DocumentExtractor(content,recover=False)

    def test_unsafe_entities_and_table_expansion_limit_are_rejected(self):
        with self.assertRaises(ExtractionFailure):
            DocumentExtractor(b'<!DOCTYPE x [<!ENTITY y SYSTEM "file:///etc/passwd">]><x>&y;</x>')
        with self.assertRaises(ExtractionFailure):
            DocumentExtractor(document(),max_cells=1).extract()
        with self.assertRaises(ExtractionFailure):
            DocumentExtractor(b'<x><TABLE><TR><TD COLSPAN="-1">0</TD></TR></TABLE></x>').extract()

    def test_unknown_or_mixed_unit_is_not_assumed(self):
        for unit in ('','원, %','미상'):
            result=DocumentExtractor(document(unit=unit)).extract()
            self.assertIsNone(result['tables'][1]['unit'])
            self.assertEqual([],candidates(result['tables'][1]))

    def test_number_strings_preserve_precision_sign_and_missing_zero(self):
        self.assertEqual('9007199254740993.123456789',number('9,007,199,254,740,993.123456789'))
        self.assertEqual('-15775814210',number('(15,775,814,210)'))
        self.assertEqual('0',number('0'))
        for value in ('','-','—','1,23','수치 없음','NaN'):
            self.assertIsNone(number(value))

    def test_html_missing_end_tag_has_no_invented_verified_candidate(self):
        data=b'<html><body><h1>Test</h1><table><tr><td>broken<tr><td>next</td></tr></table></body></html>'
        result=DocumentExtractor(data,media_type='text/html').extract()
        broken=result['tables'][0]['rows'][0]['cells'][0]
        self.assertFalse(broken['location']['raw_span_verified'])
        self.assertTrue(result['recovered'])

    def test_receivables_candidate_requires_current_asset_section_evidence(self):
        content='''<DOCUMENT><SECTION-1><TITLE>재무</TITLE><TABLE-GROUP ACLASS="{XBRL}BS_S">
<TITLE>4-1. 재무상태표</TITLE><TABLE BORDER="0"><TR><TE>제 52 기 반기말 2026.06.30 현재</TE></TR>
<TR><TE>(단위 : 원)</TE></TR></TABLE><TABLE BORDER="1">
<TR><TH></TH><TH>제 52 기 반기말</TH></TR>
<TR><TE>비유동 자산</TE><TE>99</TE></TR>
<TR><TE>매출채권및기타채권</TE><TE>99</TE></TR>
<TR><TE>유동 자산</TE><TE>1</TE></TR>
<TR><TE>매출채권및기타채권</TE><TE>1</TE></TR>
</TABLE></TABLE-GROUP></SECTION-1></DOCUMENT>'''.encode()
        table=DocumentExtractor(content).extract()['tables'][1]
        values=[candidate[3] for candidate in candidates(table) if candidate[0]=='receivables']
        self.assertEqual(['1'],values)

    def test_recovered_period_without_confirmed_raw_location_is_not_adopted(self):
        content=document().replace('까지</TE>'.encode(),'까지& &undefined;</TE>'.encode(),1)
        result=DocumentExtractor(content).extract()
        self.assertTrue(result['recovered'])
        self.assertEqual([],candidates(result['tables'][1]))

    def test_non_month_end_quarter_start_is_not_guessed(self):
        content=document().replace(b'2026.06.30',b'2026.06.15')
        table=DocumentExtractor(content).extract()['tables'][1]
        cells=table['rows'][2]['cells']
        self.assertIsNone(cells[1]['period'])
        self.assertEqual('2026-01-01',cells[2]['period']['start'])


class ExtractionStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=ResearchStore(self.root/'session.db',self.root/'sources')
        research=self.store.create_research(ResearchInput(company={'name':'합성 기업'},purpose='추출 시험',
            public_cutoff='2026-10-20',comparison_window={},request_key='extraction-test'))
        self.run_id=research['runs'][0]['id']
        self.service=ExtractionService(self.store)

    def save_source(self,content=None,media='application/xml',operation='document_member'):
        return self.store.save_source(self.run_id,SourceInput(kind='synthetic',title='직접 만든 원문',
            publisher='시험',retrieved_at='2026-10-20',receipt_number='20260813000874',
            rights_status='synthetic',media_type=media,reporting_period={'operation':operation}),
            document() if content is None else content)

    def test_atomic_storage_candidates_references_and_idempotent_restart(self):
        source=self.save_source();summary=self.service.extract_source(source['id'])
        self.assertEqual(4,summary['candidate_count'])
        manifest=self.service.fragment(summary['manifest_id'])
        facts=[self.store.get_evidence(fid) for fid in manifest['data']['context']['candidate_ids']]
        self.assertTrue(all(fact['status']=='candidate' for fact in facts))
        selected=next(f for f in facts if f['data']['period'].get('column_kind')=='cumulative'
                      and f['data']['period']['start']=='2026-01-01')
        location=selected['fragment']['data']['location']
        self.assertEqual((3,3),(location['row'],location['column']))
        original=self.service.original(selected['fragment_id'])
        self.assertIn(b'197,724,953,870',original)
        self.assertEqual(hashlib.sha256(original).hexdigest(),location['raw_span_sha256'])
        reopened=ExtractionService(ResearchStore(self.store.db_path,self.store.source_root))
        with patch('agent.finance.extraction_service.DocumentExtractor',side_effect=AssertionError('must reuse stored index')):
            reused=reopened.extract_source(source['id'])
        self.assertTrue(reused['reused'])
        self.assertEqual(summary['manifest_id'],reused['manifest_id'])
        self.assertEqual(document(),self.store.source_content(source['id']))

    def test_transaction_failure_does_not_leave_partial_fragments_or_facts(self):
        source=self.save_source()
        with patch.object(self.store,'_event',side_effect=RuntimeError('synthetic storage failure')):
            with self.assertRaises(RuntimeError):self.service.extract_source(source['id'])
        with self.store.connection() as conn:
            self.assertEqual(0,conn.execute('SELECT COUNT(*) FROM research_fragments').fetchone()[0])
            self.assertEqual(0,conn.execute('SELECT COUNT(*) FROM research_facts').fetchone()[0])

    def test_search_returns_locations_and_index_scope(self):
        source=self.save_source();self.service.extract_source(source['id'])
        result=self.service.search(source['id'],'매출액')
        self.assertEqual('ok',result['status'])
        self.assertEqual('indexed_fragments_only',result['search_scope'])
        self.assertEqual(3,result['items'][0]['location']['row'])
        self.assertTrue(result['items'][0]['location']['raw_span_verified'])
        missing=self.service.search(source['id'],'없는 문구')
        self.assertEqual('no_match_in_index',missing['status'])

    def test_api_json_pointer_keeps_original_record_without_period_guessing(self):
        data={'status':'000','list':[{'account_nm':'현금','thstrm_amount':'9,007,199,254,740,993','rcept_no':'20260813000874'}]}
        source=self.save_source(json.dumps(data).encode(),'application/json','financials')
        summary=self.service.extract_source(source['id'])
        manifest=self.service.fragment(summary['manifest_id'])
        record=self.service.fragment(manifest['data']['context']['fragment_ids'][0])
        self.assertEqual('/list/0',record['data']['location']['json_pointer'])
        self.assertEqual(data['list'][0],record['data']['context']['record'])
        with self.store.connection() as conn:
            self.assertEqual(0,conn.execute('SELECT COUNT(*) FROM research_facts').fetchone()[0])

    def test_http_original_is_raw_bytes_and_not_executable_html(self):
        source=self.save_source();summary=self.service.extract_source(source['id'])
        manifest=self.service.fragment(summary['manifest_id'])
        fact=self.store.get_evidence(manifest['data']['context']['candidate_ids'][0])
        fid=fact['fragment_id']
        import server
        failed=AsyncMock();failed.start.side_effect=RuntimeError('no finance needed')
        with patch.dict(os.environ,{'APP_MODE':'research','APP_DB_PATH':str(self.store.db_path),
                                   'APP_SOURCE_ROOT':str(self.store.source_root)}), \
                patch.object(server,'create_finance_client',return_value=failed):
            with TestClient(server.app,base_url='http://127.0.0.1:8100') as client:
                result=client.get('/fragments/'+fid+'/original')
                self.assertEqual(200,result.status_code)
                self.assertEqual(self.service.original(fid),result.content)
                self.assertEqual('application/octet-stream',result.headers['content-type'])
                self.assertEqual('nosniff',result.headers['x-content-type-options'])

    def test_tampered_original_cannot_reuse_old_index(self):
        source=self.save_source();self.service.extract_source(source['id'])
        with self.store.connection() as conn:
            key=conn.execute('SELECT storage_key FROM research_sources WHERE id=?',(source['id'],)).fetchone()[0]
        (self.store.source_root/key).write_bytes(b'tampered')
        with self.assertRaises(IntegrityFailure):self.service.extract_source(source['id'])

    def test_search_uses_one_parser_index_without_mixing_preserved_versions(self):
        source=self.save_source();first=self.service.extract_source(source['id'])
        with patch.object(self.service,'_fingerprint',return_value='new-parser-fingerprint'):
            second=self.service.extract_source(source['id'])
            self.assertNotEqual(first['manifest_id'],second['manifest_id'])
            hits=self.service.search(source['id'],'매출액')
            self.assertEqual(1,hits['match_count'])
            self.assertEqual(second['manifest_id'],hits['manifest_id'])
        self.assertEqual(first['manifest_id'],self.service.search(source['id'],'매출액')['manifest_id'])
