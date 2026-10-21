"""명시된 연결 변경과 비교 차단. 직접 만든 자료로 오류 처리를 시험한다."""
from copy import deepcopy
import unittest
from agent.finance.consolidation_checks import collect_disclosed_changes, blocking_changes
from agent.finance.document_extractor import DocumentExtractor
from agent.finance.numeric_validation import NumericValidationService
from agent.finance.validation_acceptance import assess_stage04
from test_validation_acceptance import fixture


class ConsolidationTests(unittest.TestCase):
    def evidence(self, company='New B', reason='당기 중 설립출자', heading='연결대상회사의 변동내용'):
        content=f'''<DOCUMENT><COMPANY-NAME AREGCIK="00129642">Synthetic</COMPANY-NAME>
<TU AUNIT="PERIODFROM" AUNITVALUE="20240101">2024-01-01</TU>
<TU AUNIT="PERIODTO" AUNITVALUE="20241231">2024-12-31</TU>
<SECTION-1><TITLE>I. 회사의 개요</TITLE><P>{heading}</P>
<TABLE><TR><TH>구 분</TH><TH>자회사</TH><TH>사 유</TH></TR>
<TR><TD ROWSPAN="2">신규연결</TD><TE>{company}</TE><TE>{reason}</TE></TR>
<TR><TE>-</TE><TE>-</TE></TR></TABLE></SECTION-1></DOCUMENT>'''.encode()
        service=NumericValidationService(None)
        table=DocumentExtractor(content).extract()['tables'][0]
        identity=service._document_identity(content)
        changes=collect_disclosed_changes({'id':'source','metadata':{'receipt_number':'20250314001195'}},
            content,identity,[('fragment',table)],service._raw_check)
        return changes,content,identity,table

    def inputs(self,scope='CFS',current='2025',previous='2024'):
        return {role:{'scope':scope,'period':{'kind':'duration','start':year+'-01-01','end':year+'-12-31'}}
                for role,year in [('current',current),('previous',previous)]}

    def test_named_change_has_original_cells_and_document_period(self):
        changes,content,identity,table=self.evidence()
        self.assertEqual(1,len(changes));d=changes[0]['data']
        self.assertEqual('pass',changes[0]['status']);self.assertEqual('New B',d['company_name'])
        self.assertIsNone(d['exact_change_date'])
        self.assertTrue(NumericValidationService._raw_check(content,d['cells']['company']['location'],'New B'))
        self.assertEqual('2024-12-31',d['period']['end'])

    def test_dash_missing_reason_wrong_heading_or_unproven_raw_is_not_no_change(self):
        for args in ({'company':'-'},{'reason':'-'},{'heading':'다른 현황'},{'reason':'과거 설립'},{'company':'해당없음'},{'reason':'당기 중 설립 추진 예정'}):
            self.assertEqual([],self.evidence(**args)[0])
        changes,content,identity,table=self.evidence()
        table['rows'][1]['cells'][1]['location']['raw_span_sha256']='wrong'
        self.assertEqual([],collect_disclosed_changes({'id':'s','metadata':{'receipt_number':'r'}},content,
            identity,[('fragment',table)],NumericValidationService._raw_check))

    def test_change_blocks_overlapping_cfs_flow_but_not_ofs_or_later_periods(self):
        changes,*_=self.evidence()
        self.assertEqual(1,len(blocking_changes(self.inputs(),changes)))
        self.assertEqual([],blocking_changes(self.inputs(scope='OFS'),changes))
        self.assertEqual([],blocking_changes(self.inputs(current='2026',previous='2025'),changes))
        fake=deepcopy(changes);fake[0]['status']='not_checked'
        self.assertEqual([],blocking_changes(self.inputs(),fake))

    def test_actual_calculation_keeps_inputs_but_withholds_usable_difference(self):
        changes,*_=self.evidence();facts=[];validations=[]
        for role,value in [('current','180'),('previous','100')]:
            context=self.inputs()[role]
            facts.append({'id':role,'source':{'id':role},'data':{'account':'revenue','original_account':'매출액',
                          'currency':'KRW',**context}})
            validations.append({'id':'v-'+role,'fact_id':role,'status':'pass',
                                'data':{'amount_comparison':{'match':'exact','original_normalized':value}}})
        calculations=NumericValidationService(None)._calculations(facts,validations,changes)
        comparison=next(c for c in calculations if 'V11' in c['formula_version'])
        self.assertEqual('not_checked',comparison['status'])
        self.assertEqual('checked_change_detected',comparison['data']['scope_review']['status'])
        self.assertNotIn('result',comparison['data'])
        self.assertEqual('80',comparison['data']['unverified_arithmetic_result'])
        self.assertEqual('180',comparison['data']['inputs']['current']['value'])
        batch,reference=fixture();batch['validations']+=changes;batch['calculations'].append(comparison)
        accepted=assess_stage04(batch,reference)
        self.assertEqual(0,accepted['exit_code'])
        forged=deepcopy(batch);forged['calculations'][-1]['data']['scope_review']['validation_ids']=['nonexistent']
        self.assertEqual(1,assess_stage04(forged,reference)['exit_code'])
        without_evidence=deepcopy(batch);without_evidence['validations']=without_evidence['validations'][:-1]
        self.assertEqual(1,assess_stage04(without_evidence,reference)['exit_code'])
