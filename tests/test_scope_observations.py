"""명단·지분·변동표와 비교 구간의 공백을 합성 자료로 검사한다."""
from copy import deepcopy
import unittest
from agent.finance.document_extractor import DocumentExtractor
from agent.finance.numeric_validation import NumericValidationService
from agent.finance.scope_observations import observe_scope, unchanged_scope_support


class ScopeObservationTests(unittest.TestCase):
    def observation(self,year=2025,half=False,company='B',change='-',percent='80.00%'):
        end=f'{year}-'+('06-30' if half else '12-31')
        content=f'''<DOCUMENT><COMPANY-NAME AREGCIK="00129642">Synthetic</COMPANY-NAME>
<TU AUNIT="PERIODFROM" AUNITVALUE="{year}0101">{year}-01-01</TU>
<TU AUNIT="PERIODTO" AUNITVALUE="{end.replace('-','')}">{end}</TU>
<P>연결대상회사의 변동내용</P>
<TABLE><TR><TH>구분</TH><TH>자회사</TH><TH>사유</TH></TR>
<TR><TD>신규연결</TD><TE>{change}</TE><TE>{'-' if change=='-' else '당기 중 설립출자'}</TE></TR>
<TR><TD>연결제외</TD><TE>-</TE><TE>-</TE></TR></TABLE>
<TABLE-GROUP ACLASS="{{XBRL}}NT_C_TEST"><TABLE>
<TR><TH ROWSPAN="2">종속기업명</TH><TH>당기말</TH><TH>전기말</TH></TR>
<TR><TH>소유지분율</TH><TH>소유지분율</TH></TR>
<TR><TE>A</TE><TE>100.00%</TE><TE>100.00%</TE></TR>
<TR><TE>{company}</TE><TE>{percent}</TE><TE>80.00%</TE></TR>
</TABLE></TABLE-GROUP></DOCUMENT>'''.encode()
        service=NumericValidationService(None)
        tables=[(str(i),t) for i,t in enumerate(DocumentExtractor(content).extract()['tables'])]
        result=observe_scope({'id':str(year)+end,'metadata':{'receipt_number':str(year+1)+'0315000001'}},content,
                service._document_identity(content),tables,[],[],service._raw_check)
        self.assertEqual(1,len(result));return result[0]

    def inputs(self):
        return {role:{'scope':'CFS','period':{'kind':'duration','start':str(year)+'-01-01','end':str(year)+'-06-30'}}
                for role,year in [('current',2026),('previous',2025)]}

    def test_membership_ownership_and_complete_interval_allow_bounded_comparison(self):
        records=[self.observation(2025,True),self.observation(2025),self.observation(2026,True)]
        proof=unchanged_scope_support(self.inputs(),records)
        self.assertIsNotNone(proof)
        self.assertEqual('2025-01-01',proof['coverage_start']);self.assertEqual('2026-06-30',proof['coverage_end'])
        self.assertEqual('checked_no_change_found_in_collected_disclosures',proof['status'])

    def test_same_endpoint_members_do_not_fill_a_missing_middle_period(self):
        records=[self.observation(2025,True),self.observation(2026,True)]
        self.assertIsNone(unchanged_scope_support(self.inputs(),records))

    def test_same_company_count_but_different_names_or_ownership_withholds(self):
        for options in ({'company':'C'},{'percent':'70.00%'}):
            records=[self.observation(2025,True),self.observation(2025),self.observation(2026,True,**options)]
            self.assertIsNone(unchanged_scope_support(self.inputs(),records))

    def test_named_interim_change_or_dash_without_membership_proof_is_not_absence(self):
        record=self.observation(2025,change='C')
        self.assertFalse(record['data']['absence_observed_within_reporting_period'])
        records=[self.observation(2025,True),record,self.observation(2026,True)]
        self.assertIsNone(unchanged_scope_support(self.inputs(),records))
        records=[self.observation(2025,True),self.observation(2025),self.observation(2026,True)]
        for v in records:v['data']['empty_change_table']=None
        self.assertIsNone(unchanged_scope_support(self.inputs(),records))

    def test_ofs_is_not_converted_to_consolidated_scope(self):
        records=[self.observation(2025,True),self.observation(2025),self.observation(2026,True)]
        inputs=deepcopy(self.inputs());inputs['current']['scope']='OFS'
        self.assertIsNone(unchanged_scope_support(inputs,records))
