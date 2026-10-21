"""잔액 기준일·과거 주석 절 연결의 승인된 조건을 합성 원문으로 시험한다."""
import unittest
from agent.finance.document_extractor import DocumentExtractor
from agent.finance.note_checks import reconcile_note_amounts
from agent.finance.note_context import NoteContext
from agent.finance.numeric_validation import NumericValidationService
import test_evidence_hardening as evidence_tests

COVER='''<COMPANY-NAME AREGCIK="00129642">Synthetic</COMPANY-NAME>
<TU AUNIT="PERIODFROM" AUNITVALUE="20260101">2026-01-01</TU>
<TU AUNIT="PERIODTO" AUNITVALUE="20260630">2026-06-30</TU>'''


class NoteContextTests(unittest.TestCase):
    def test_current_period_balance_label_requires_verified_cover(self):
        content,facts,validations,_=evidence_tests.NoteTests().fixture(period='당기')
        content=content.replace(b'<DOCUMENT>',('<DOCUMENT>'+COVER).encode())
        tables=[('t',DocumentExtractor(content).extract()['tables'][1])]
        service=NumericValidationService(None);identity=service._document_identity(content)
        result=reconcile_note_amounts({'id':'source'},content,identity,tables,facts,validations,service._raw_check)[0]
        self.assertEqual('pass',result['status'])
        cell=result['data']['account_checks']['short_term_borrowings']['note_cells'][0]
        self.assertEqual('2026-06-30',cell['resolved_as_of'])
        self.assertEqual('balance_note_period_label_and_verified_cover',cell['date_proofs'][0]['basis'])
        identity['locations'].pop('end')
        self.assertEqual('not_checked',reconcile_note_amounts({'id':'source'},content,identity,tables,
                         facts,validations,service._raw_check)[0]['status'])

    def chapter(self, anchor_scope='OFS', paired_title='4. 재무제표', same_parent=True):
        statement=f'<SECTION-2><TITLE>{paired_title}</TITLE><TABLE-GROUP ACLASS="{{XBRL}}BS_S"><TABLE><TR><TE>anchor</TE></TR></TABLE></TABLE-GROUP></SECTION-2>'
        notes='<SECTION-2><TITLE>5. 재무제표 주석</TITLE><TABLE><TR><TE>note</TE></TR></TABLE></SECTION-2>'
        body=statement+notes if same_parent else statement+'</LIBRARY><LIBRARY>'+notes
        content=('<DOCUMENT>'+COVER+'<LIBRARY>'+body+'</LIBRARY></DOCUMENT>').encode()
        extracted=DocumentExtractor(content).extract();cell=extracted['tables'][0]['rows'][0]['cells'][0]
        anchor={'id':'a','source':{'id':'source'},'data':{'scope':anchor_scope},'fragment':{'data':{
            'location':cell['location'],'context':{'scope_basis':'{XBRL}BS_S'}}}}
        service=NumericValidationService(None)
        context=NoteContext({'id':'source'},content,service._document_identity(content),[anchor],{'a':{}},service._raw_check)
        return context,extracted['tables'][1]

    def test_old_note_scope_needs_paired_statement_and_validated_original_anchor(self):
        context,table=self.chapter();proof=context.scope(table)
        self.assertEqual('OFS',proof['scope']);self.assertEqual('a',proof['statement_fact_id'])
        self.assertEqual('paired_statement_and_note_chapters',proof['basis'])
        for args in ({'anchor_scope':'CFS'},{'paired_title':'2. 연결재무제표'},{'same_parent':False}):
            context,table=self.chapter(**args);self.assertIsNone(context.scope(table))
        context,table=self.chapter();context.anchors=[];self.assertIsNone(context.scope(table))

    def test_duration_labels_do_not_set_balance_dates_without_balance_table_permission(self):
        context,_=self.chapter()
        self.assertIsNone(context.date('당기'))
        self.assertIsNone(context.date('당기와 전기',allow_balance_label=True))
        self.assertEqual('2025-06-30',context.date('2) 전반기',allow_balance_label=True)['as_of'])
        self.assertEqual('2025-12-31',context.date('2) 전기',allow_balance_label=True)['as_of'])

    def test_prior_period_rollforward_end_is_not_current_report_end(self):
        rows=lambda values:''.join(f'<TR><TE>{name}</TE><TE>{value}</TE></TR>' for name,value in zip(
            ['단기차입금','유동성장기부채','장기차입금'],values))
        group=lambda label,values:f'<TABLE-GROUP ACLASS="{{XBRL}}NT_S_X"><P>{label}</P><TABLE BORDER="1"><TR><TH>구분</TH><TH>당기말</TH></TR>{rows(values)}</TABLE></TABLE-GROUP>'
        content=('<DOCUMENT>'+COVER+group('1) 당기',[100,20,30])+group('2) 전기',[80,10,15])+'</DOCUMENT>').encode()
        # 명시한 단위가 없는 표는 채택하지 않는다. 단위 선언을 넣은 뒤 실제 위치로 다시 추출한다.
        content=content.replace(b'<TABLE BORDER="1">','<P>(단위 : 원)</P><TABLE BORDER="1">'.encode())
        tables=[(str(i),t) for i,t in enumerate(DocumentExtractor(content).extract()['tables'])]
        _,facts,validations,_=evidence_tests.NoteTests().fixture()
        service=NumericValidationService(None)
        result=reconcile_note_amounts({'id':'source'},content,service._document_identity(content),tables,facts,
                                      validations,service._raw_check)[0]
        self.assertEqual('pass',result['status'])
        self.assertEqual(['100'],[c['value'] for c in result['data']['account_checks']['short_term_borrowings']['note_cells']])


    def test_fair_value_column_does_not_substitute_for_missing_book_value(self):
        rows=''.join(f'<TR><TE>{label}</TE><TE>-</TE><TE>{value}</TE></TR>' for label,value in
                     [('단기차입금','100'),('유동성장기부채','20'),('장기차입금','30')])
        content=('<DOCUMENT>'+COVER+'<TABLE-GROUP ACLASS="{XBRL}NT_S_X"><P>(단위 : 원)</P>'
                 '<TABLE><TR><TH ROWSPAN="2">구분</TH><TH COLSPAN="2">당반기말</TH></TR>'
                 '<TR><TH>장부금액</TH><TH>공정가치</TH></TR>'+rows+'</TABLE></TABLE-GROUP></DOCUMENT>').encode()
        tables=[('t',DocumentExtractor(content).extract()['tables'][0])]
        _,facts,validations,_=evidence_tests.NoteTests().fixture()
        service=NumericValidationService(None)
        result=reconcile_note_amounts({'id':'source'},content,service._document_identity(content),tables,facts,
                                      validations,service._raw_check)[0]
        self.assertEqual('not_checked',result['status'])
        self.assertEqual([],result['data']['account_checks']['short_term_borrowings']['note_cells'])
