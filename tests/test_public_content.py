"""원문 인용의 범위·중복·무결성 및 잘못된 HTML charset 회귀."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from agent.finance.document_extractor import DocumentExtractor
from agent.finance.public_content import collect_public_content,check_public_quote
from agent.research_models import SourceInput
from agent.research_store import ResearchStore,IntegrityFailure
from tests.test_research_storage import research_input


class PublicContentTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=ResearchStore(Path(self.tmp.name)/'db.sqlite',Path(self.tmp.name)/'sources')
        research=self.store.create_research(research_input());self.run=research['runs'][0]['id'];self.basis=research['runs'][0]['basis_id']

    def source(self,*,scope='S',duplicate=False):
        rows=''.join('<TR><TE>'+label+'</TE><TE>'+value+'</TE></TR>' for label,value in [
            ('매출채권의 감소(증가) 조정','(120)'),('재고자산의 감소(증가)','(30)'),('매입채무의 증가(감소)','(20)')])
        table='<TABLE BORDER="1">'+rows+(rows if duplicate else '')+'</TABLE>'
        raw=('<DOCUMENT><SECTION-1><TITLE>III. 재무에 관한 사항</TITLE><SECTION-2><TITLE>5. 재무제표 주석</TITLE>'
            '<TABLE-GROUP ACLASS="{XBRL}NT_'+scope+'_27"><TITLE>27. 현금흐름표</TITLE><TABLE BORDER="0"><TR><TE>당반기</TE><TE>(단위 : 원)</TE></TR></TABLE>'+table+
            '</TABLE-GROUP></SECTION-2></SECTION-1></DOCUMENT>').encode()
        return self.store.save_source(self.run,SourceInput(kind='synthetic',title='합성 별도 주석',publisher='합성 시험',
            receipt_number='20260813000874',retrieved_at='2026-10-21',rights_status='synthetic',
            reporting_period={'operation':'document_member'},media_type='application/xml'),raw)

    def test_selected_rows_have_raw_context_and_exact_quote(self):
        source=self.source();findings=collect_public_content(self.store,[source]);item=findings[0]
        self.assertEqual('verified_excerpt',item['status']);self.assertEqual(3,len(item['quotes']))
        for quote in item['quotes']:self.assertEqual(source['id'],check_public_quote(self.store,quote,self.basis)['id'])
        self.assertTrue(all(x['status']=='withheld' for x in findings[1:]))

    def test_wrong_scope_and_ambiguous_rows_are_withheld(self):
        for changes in ({'scope':'C'},{'duplicate':True}):
            item=collect_public_content(self.store,[self.source(**changes)])[0]
            self.assertEqual('withheld',item['status']);self.assertEqual([],item['quotes'])

    def test_prior_half_year_with_same_labels_is_not_selected(self):
        source=self.source();raw=self.store.source_content(source['id'])
        prior=raw.replace('당반기'.encode(),'전반기'.encode()).replace(b'(120)',b'(999)')
        other=self.store.save_source(self.run,SourceInput(kind='synthetic',title='합성 전반기',publisher='합성 시험',
            receipt_number='20260813000874',retrieved_at='2026-10-21',rights_status='synthetic',
            reporting_period={'operation':'document_member'},media_type='application/xml'),prior)
        item=collect_public_content(self.store,[source,other])[0]
        self.assertEqual('verified_excerpt',item['status']);self.assertNotIn('(999)',' '.join(q['quote'] for q in item['quotes']))

    def test_forged_text_other_row_and_raw_tamper_are_rejected(self):
        source=self.source();quote=collect_public_content(self.store,[source])[0]['quotes'][0]
        for key,value in [('quote','가짜 설명'),('row',999),('section_path',['다른 범위']),('context_locations',[]),('scope','CFS')]:
            bad=deepcopy(quote);bad[key]=value
            with self.assertRaises(IntegrityFailure):check_public_quote(self.store,bad,self.basis)
        with self.store.connection() as conn:key=conn.execute('SELECT storage_key FROM research_sources WHERE id=?',(source['id'],)).fetchone()[0]
        (self.store.source_root/key).write_bytes(b'tampered')
        with self.assertRaises(IntegrityFailure):check_public_quote(self.store,quote,self.basis)

    def test_utf8_html_with_stale_euckr_declaration_retains_original_table(self):
        raw='<html><head><meta charset="euc-kr"></head><body><table><tr><td>보증금액</td><td>1,200</td></tr></table></body></html>'.encode()
        result=DocumentExtractor(raw,media_type='text/html').extract()
        self.assertEqual(1,len(result['tables']));self.assertEqual('보증금액',result['tables'][0]['rows'][0]['cells'][0]['text'])
        self.assertTrue(result['tables'][0]['rows'][0]['cells'][0]['location']['raw_span_verified'])
