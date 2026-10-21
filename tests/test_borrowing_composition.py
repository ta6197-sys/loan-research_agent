"""차입 3계정·리스 분리·보증 가산 금지를 합성 자료로 검증한다."""
import unittest
from agent.finance.document_extractor import DocumentExtractor
from agent.finance.note_checks import reconcile_note_amounts
from agent.finance.borrowing_composition import reconcile_composition, verify_three_caption_subtotal, scaled
from agent.finance.numeric_validation import NumericValidationService
import test_evidence_hardening as evidence_tests
from test_note_context import COVER


class BorrowingCompositionTests(unittest.TestCase):
    def results(self,grand_total='155',lease_label='리스부채'):
        rows=''.join(f'<TR><TE>{label}</TE><TE>{value}</TE></TR>' for label,value in
             [('단기차입금','100'),('유동성장기부채','20'),('장기차입금','30'),(lease_label,'5'),('합계',grand_total)])
        content=('<DOCUMENT>'+COVER+'<TABLE-GROUP ACLASS="{XBRL}NT_S_X"><P>(단위 : 원)</P>'
                 '<TABLE><TR><TH>구분</TH><TH>당반기말</TH></TR>'+rows+'</TABLE></TABLE-GROUP></DOCUMENT>').encode()
        tables=[('table',DocumentExtractor(content).extract()['tables'][0])]
        _,facts,validations,_=evidence_tests.NoteTests().fixture()
        service=NumericValidationService(None);identity=service._document_identity(content)
        source={'id':'source'}
        notes=reconcile_note_amounts(source,content,identity,tables,facts,validations,service._raw_check)
        return reconcile_composition(source,content,identity,tables,facts,validations,notes,service._raw_check)

    def test_same_column_reconciles_borrowing_subtotal_and_separate_lease(self):
        result=self.results();self.assertEqual(1,len(result));d=result[0]['data']
        self.assertEqual('150',d['subtotal']['amount']);self.assertFalse(d['subtotal']['lease_included'])
        self.assertFalse(d['subtotal']['guarantee_included']);self.assertIsNone(d['guarantee_policy']['amount'])
        self.assertEqual('not_checked',d['guarantee_policy']['amount_status'])
        self.assertEqual('155',d['lease_exclusion_proof']['financing_liabilities_total']['value'])

    def test_bad_total_or_undiscounted_total_name_does_not_pass(self):
        self.assertEqual([],self.results(grand_total='156'))
        self.assertEqual([],self.results(lease_label='총리스부채'))

    def test_guarantee_or_lease_cannot_be_injected_as_a_fourth_component(self):
        amounts={'short_term_borrowings':'100','current_long_term_borrowings':'20','long_term_borrowings':'30'}
        for extra in ('guarantee','lease'):
            with self.assertRaises(ValueError):verify_three_caption_subtotal({**amounts,extra:'5'},{'evidence':True})
        with self.assertRaises(ValueError):verify_three_caption_subtotal(amounts,None)

    def test_large_unit_conversion_does_not_use_default_decimal_precision(self):
        raw='1234567890123456789012345678901234567890'
        self.assertEqual(raw+'00000000',scaled(raw,'억원'))
