"""목차 오탐·다른 문맥·변조 방지와 최소표/질문 근거의 보수적 상태."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from agent.finance.business_review import collect_business_review,check_business_quote,minimum_review
from agent.finance.extraction_service import ExtractionService
from agent.finance.research_stage05 import required_questions
from agent.research_store import ResearchStore,IntegrityFailure
from agent.research_models import SourceInput
from tests.test_research_storage import research_input


class BusinessReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=ResearchStore(Path(self.tmp.name)/'db.sqlite',Path(self.tmp.name)/'sources')
        r=self.store.create_research(research_input());self.run=r['runs'][0]['id'];self.basis=r['runs'][0]['basis_id']

    def source(self,chapter='II. 사업의 내용',duplicate=False):
        paragraph='<P>당사의 사업부문은 제동제품을 주문자생산 및 보수용 시장에 공급하는 부문과 설비 부문입니다.</P>'
        raw=('<DOCUMENT><SECTION-1><TITLE>'+chapter+'</TITLE><SECTION-2><TITLE>1. 사업의 개요</TITLE>'+paragraph+(paragraph if duplicate else '')+'</SECTION-2></SECTION-1></DOCUMENT>').encode()
        source=self.store.save_source(self.run,SourceInput(kind='synthetic',title='합성 사업 설명',publisher='합성',receipt_number='20260813000874',
            retrieved_at='2026-10-21',media_type='application/xml',reporting_period={'operation':'document_member'},rights_status='synthetic'),raw)
        ExtractionService(self.store).extract_source(source['id']);return source

    def test_business_paragraph_is_raw_checked_and_never_whole_business_complete(self):
        source=self.source();review=collect_business_review(self.store,[source]);item=review['items'][0]
        self.assertEqual('verified_excerpt',item['status']);self.assertEqual('partial',review['status'])
        self.assertEqual(source['id'],check_business_quote(self.store,item['quote'],self.basis,item['id'])['id'])

    def test_contents_page_and_duplicate_business_paragraph_are_withheld(self):
        for args in ({'chapter':'목차'},{'duplicate':True}):
            review=collect_business_review(self.store,[self.source(**args)])
            self.assertEqual('withheld',review['items'][0]['status']);self.assertIsNone(review['items'][0]['quote'])

    def test_forged_scope_and_section_do_not_pass_original_check(self):
        source=self.source();q=collect_business_review(self.store,[source])['items'][0]['quote']
        for key,value in [('scope','OFS'),('quote','가짜 문장'),('section_path',['다른 항목']),('context_locations',[])]:
            bad=deepcopy(q);bad[key]=value
            with self.assertRaises(IntegrityFailure):check_business_quote(self.store,bad,self.basis,'products')

    def test_minimum_review_keeps_absence_and_internal_information_separate(self):
        context={'financial_rows':[],'business_review':{'items':[]},'public_findings':[]}
        rows=minimum_review(context);self.assertEqual(7,len(rows))
        self.assertTrue(all(r['status']!='complete' for r in rows))
        self.assertEqual('waiting_internal_information',rows[-1]['status'])
        self.assertIn('0묶음',rows[3]['observed']);self.assertIn('0묶음',rows[5]['observed'])

    def test_questions_bind_only_verified_observations_and_describe_answer_effect(self):
        context={'public_cutoff':'2026-10-20','unknowns':[{'evidence_id':'U_working_capital'}],
            'model_evidence':[{'evidence_id':'F_receivables','state':'verified','text':'검증된 기타채권 포함 합계다.'},
                {'evidence_id':'F_inventory','state':'data','text':'미검증 입력이다.'}]}
        q=required_questions(context)[0]
        self.assertEqual(['F_receivables'],[b['id'] for b in q['observed_basis']]);self.assertTrue(q['answer_effect'])
        self.assertEqual(['U_working_capital'],q['evidence_ids'])
