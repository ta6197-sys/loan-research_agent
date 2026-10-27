"""정정 이력·주석 금액·복구 표 위치의 보수적 판정. 직접 만든 자료만 사용한다."""
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

from agent.finance.disclosure_audit import audit_disclosure_history, analysis_receipt_policy, apply_relation_evidence
from agent.finance.document_extractor import DocumentExtractor
from agent.finance.extraction_service import candidates
from agent.finance.note_checks import reconcile_note_amounts
from agent.finance.numeric_validation import NumericValidationService,match_amount
from test_document_extraction import document


class HistoryTests(unittest.TestCase):
    def test_relation_evidence_rechecked_and_fails_closed(self):
        original='20260813000914';corrected='20260814002314'
        pair=[{'corp_code':'00305668','corp_name':'새론오토모티브','rcept_no':original,
               'rcept_dt':'20260813','report_nm':'반기보고서 (2026.06)'},
              {'corp_code':'00305668','corp_name':'새론오토모티브','rcept_no':corrected,
               'rcept_dt':'20260814','report_nm':'[기재정정]반기보고서 (2026.06)'}]
        listing=json.dumps({'list':pair}).encode()
        corrected_raw='<DOC><P>정정대상 공시서류의 최초제출일 : 2026년 8월 13일</P></DOC>'.encode()
        original_raw='<DOC><P>새론오토모티브</P><P>2026년 06월 30일</P></DOC>'.encode()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'data/research';root.mkdir(parents=True)
            archive=root/'audits/original.zip';archive.parent.mkdir()
            with zipfile.ZipFile(archive,'w') as z:z.writestr(original+'.xml',original_raw)
            sha=lambda raw:hashlib.sha256(raw).hexdigest()
            proof={'status':'relation_supported','original_receipt':original,'corrected_receipt':corrected,
                   'public_cutoff':'2026-10-27','company_code':'00305668',
                   'official_list_source_id':'listing','official_list_hash':sha(listing),
                   'corrected_source_id':'corrected','corrected_source_hash':sha(corrected_raw),
                   'official_list_pair':pair,'original_archive_path':'data/research/audits/original.zip',
                   'original_archive_hash':sha(archive.read_bytes()),'original_member_name':original+'.xml',
                   'original_member_hash':sha(original_raw),'checks':{'identity':True}}
            manifest=root/'audits/disclosure_relations'/(corrected+'.json');manifest.parent.mkdir()
            manifest.write_text(json.dumps(proof))
            class Store:
                source_root=root
                def get_source(self,identifier):
                    raw={'listing':listing,'corrected':corrected_raw}[identifier]
                    return {'id':identifier,'content_hash':sha(raw),'metadata':{'receipt_number':corrected}}
                def source_content(self,identifier):return {'listing':listing,'corrected':corrected_raw}[identifier]
            audit={'public_cutoff':'2026-10-27','evidence':[{'source_id':'listing'}],
                   'targets':[{'receipt_number':corrected,'status':'not_checked',
                               'reason':'correction_candidate_requires_original_relation_review',
                               'candidates':[original,corrected]}]}
            held={'status':'partial_withheld','allowed_receipts':[],'withheld_receipts':[corrected]}
            self.assertEqual([corrected],apply_relation_evidence(Store(),audit,held)['allowed_receipts'])
            archive.write_bytes(b'tampered')
            self.assertEqual([corrected],apply_relation_evidence(Store(),audit,held)['withheld_receipts'])
            archive.write_bytes(b'')
            audit['targets'][0]['candidates'].append('20260815000001')
            self.assertEqual([corrected],apply_relation_evidence(Store(),audit,held)['withheld_receipts'])

    def fixture(self,extra=None):
        rows=[{'corp_code':'00129642','rcept_no':'20260813000874','rcept_dt':'20260813',
               'report_nm':'반기보고서 (2026.06)','rm':''}]
        if extra:rows.append(extra)
        payload={'page_no':1,'total_page':1,'total_count':len(rows),'list':rows}
        class Store:
            def source_content(self,source_id):return json.dumps(payload).encode()
        sources=[{'id':'history','content_hash':'hash','metadata':{'reporting_period':{'operation':'disclosures',
                  'corp_code':'00129642','last_reprt_at':'N','bgn_de':'2023-01-01','end_de':'2026-10-20','page_no':1,'page_count':100}}}]
        return Store(),sources,{'corp_code':'00129642'},{'data':{'public_cutoff':'2026-10-20'}},payload

    def test_complete_history_only_confirms_its_cutoff_scope(self):
        store,sources,company,basis,payload=self.fixture()
        result=audit_disclosure_history(store,sources,company,basis,{'20260813000874'})
        self.assertEqual('pass',result['status']);self.assertEqual(1,result['row_count'])
        payload['total_page']=2
        self.assertEqual('not_checked',audit_disclosure_history(store,sources,company,basis,{'20260813000874'})['status'])

    def test_correction_candidate_is_not_linked_by_guessing(self):
        extra={'corp_code':'00129642','rcept_no':'20261019000001','rcept_dt':'20261019',
               'report_nm':'[기재정정]반기보고서 (2026.06)','rm':''}
        store,sources,company,basis,payload=self.fixture(extra)
        result=audit_disclosure_history(store,sources,company,basis,{'20260813000874'})
        self.assertEqual('not_checked',result['status'])
        self.assertEqual('correction_candidate_requires_original_relation_review',result['targets'][0]['reason'])

    def test_analysis_receipt_policy_holds_only_explicitly_unreviewed_correction(self):
        targets=[{'rcept_no':'annual'}, {'rcept_no':'corrected'}]
        audit={'status':'not_checked','reason':'checked_within_collected_official_history',
               'evidence':[{'source_id':'listing'}],
               'targets':[{'receipt_number':'annual','status':'pass'},
                          {'receipt_number':'corrected','status':'not_checked',
                           'reason':'correction_candidate_requires_original_relation_review'}]}
        result=analysis_receipt_policy(audit,targets)
        self.assertEqual(('partial_withheld',['annual'],['corrected']),
                         (result['status'],result['allowed_receipts'],result['withheld_receipts']))
        audit['targets'][1]['reason']='target_missing_from_complete_history'
        with self.assertRaises(ValueError):analysis_receipt_policy(audit,targets)
        audit['targets'][1]['reason']='correction_candidate_requires_original_relation_review'
        audit['evidence']=[]
        with self.assertRaises(ValueError):analysis_receipt_policy(audit,targets)

    def test_future_or_wrong_corporation_and_last_only_history_are_not_accepted(self):
        for mutation in ('future','company','last_only'):
            store,sources,company,basis,payload=self.fixture()
            if mutation=='future':payload['list'][0]['rcept_dt']='20261021'
            if mutation=='company':payload['list'][0]['corp_code']='99999999'
            if mutation=='last_only':sources[0]['metadata']['reporting_period']['last_reprt_at']='Y'
            self.assertEqual('not_checked',audit_disclosure_history(store,sources,company,basis,{'20260813000874'})['status'])


class NoteTests(unittest.TestCase):
    def fixture(self,gross='50',scope='S',header='차입금 명칭 합계',period='당반기말'):
        content=f'''<DOCUMENT><TABLE-GROUP ACLASS="{{XBRL}}NT_{scope}_D822405"><TITLE>차입금 주석</TITLE>
<TABLE BORDER="0"><TR><TE>{period}</TE></TR><TR><TE>(단위 : 원)</TE></TR></TABLE>
<TABLE BORDER="1"><TR><TH></TH><TH>{header}</TH></TR>
<TR><TE>유동 차입금(사채 포함)</TE><TE>100</TE></TR>
<TR><TE>장기차입금</TE><TE>{gross}</TE></TR>
<TR><TE>비유동차입금(사채 포함)의 유동성 대체 부분</TE><TE>(20)</TE></TR>
<TR><TE>비유동 차입금(사채 포함)의 비유동성 부분</TE><TE>30</TE></TR>
</TABLE></TABLE-GROUP></DOCUMENT>'''.encode()
        facts=[];validations=[]
        for account,value in [('short_term_borrowings','100'),('current_long_term_borrowings','20'),('long_term_borrowings','30')]:
            facts.append({'id':account,'source':{'id':'source'},'data':{'account':account,'scope':'OFS','currency':'KRW',
                         'period':{'kind':'instant','as_of':'2026-06-30'}}})
            validations.append({'fact_id':account,'status':'pass','data':{'amount_comparison':{'original_normalized':value}}})
        tables=[('table',DocumentExtractor(content).extract()['tables'][1])]
        return content,facts,validations,tables

    def run_note(self,**options):
        content,facts,validations,tables=self.fixture(**options)
        return reconcile_note_amounts({'id':'source'},content,{'end':'2026-06-30'},tables,facts,validations,
                                     NumericValidationService._raw_check)[0]

    def test_explicit_note_totals_and_reclassification_reconcile(self):
        result=self.run_note();self.assertEqual('pass',result['status'])
        self.assertEqual('pass',result['data']['reclassification_checks'][0]['status'])
        self.assertIn('전체 금융부채',result['data']['limitations'][0])

    def test_unproven_negative_reclassification_is_not_abs_value(self):
        result=self.run_note(gross='51');self.assertEqual('not_checked',result['status'])
        self.assertEqual('not_checked',result['data']['account_checks']['current_long_term_borrowings']['status'])

    def test_matching_numbers_in_wrong_scope_period_or_detail_column_do_not_pass(self):
        for options in ({'scope':'C'},{'period':'전기말'},{'header':'개별 은행 금액'}):
            self.assertEqual('not_checked',self.run_note(**options)['status'])


class ParserAndUnitTests(unittest.TestCase):
    def test_recovered_table_boundary_is_not_treated_as_content_match(self):
        content=document(value='100 & 200 &undefined;')
        result=DocumentExtractor(content).extract();table=result['tables'][1]
        self.assertTrue(table['location']['raw_span_verified'])
        self.assertFalse(table['location']['parser_text_matches_raw'])
        self.assertGreaterEqual(result['content_mismatch_table_count'],1)
        self.assertEqual('partial',result['coverage']);self.assertEqual([],candidates(table))

    def test_hundred_million_unit_is_exact_and_unknown_method_is_withheld(self):
        self.assertEqual('exact',match_amount('1','100000000','억원')['match'])
        self.assertEqual('not_checked',match_amount('1','100001000','억원')['status'])


    def test_explicit_hundred_million_truncation_declaration(self):
        from test_numeric_validation import covered_document
        content=covered_document().replace('(단위 : 원)'.encode(),'(단위 : 억원)'.encode())
        content=content.replace(b'<TABLE BORDER="1">','<P>억원 미만은 절사하였습니다.</P><TABLE BORDER="1">'.encode())
        table=DocumentExtractor(content).extract()['tables'][1]
        service=NumericValidationService(None)
        rule=service._declared_rounding(content,table)
        self.assertEqual('truncate',rule['method']);self.assertTrue(rule['evidence_confirmed'])
