"""공식 개념·금액·문맥 독립 판정과 오연결 거부를 합성 자료로 검사한다."""
from copy import deepcopy
import unittest
from lxml import etree
from agent.finance.xbrl_mapping import (XI, XS, VERSION, bind_record, compare_concepts,
                                      context, current_period, parse, qname, unit)


class XbrlMappingTests(unittest.TestCase):
    def observation(self):
        return {'concept':{'schema_id':'ifrs-full_Inventories','expanded_name':'{https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full}Inventories',
            'namespace':'https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full','type':'{'+XI+'}monetaryItemType','abstract':False,
            'substitution_group':'{'+XI+'}item','balance':'debit','period_type':'instant',
            'declaration':{'source_id':'s','content_hash':'a'*64,'xpath':'/*/*[1]'}},
            'context':{'valid':True,'primary_context':True,'entity':'00129642','scope':'OFS',
                'period':{'kind':'instant','as_of':'2025-12-31'}},
            'unit':{'currency':'KRW'},'raw_value':'9007199254740993.123456789',
            'exact_value':'9007199254740993.123456789','nil':False,'location':{'source_id':'i'}}

    def bound(self, observation=None, **row_update):
        fact=observation or self.observation()
        row={'account_id':'ifrs-full_Inventories','account_nm':'표시명은 별도 보존','currency':'KRW',**row_update}
        data=bind_record({'facts':[fact]},row,'00129642','OFS',{'kind':'instant','as_of':'2025-12-31'},
                         '9007199254740993.123456789',{'source_id':'api','json_pointer':'/list/0'})
        data['account']='inventory'
        return data

    def test_label_change_does_not_require_registered_name_pair(self):
        a=self.bound(account_nm='유동재고자산')
        b=self.bound(account_nm='공시에 사용된 다른 재고 표시명')
        self.assertEqual('pass',compare_concepts(a,b)['status'])
        self.assertEqual('not_checked',compare_concepts(a,b)['comparison_use_status'])
        self.assertEqual('pass',a['amount_status'])

    def test_different_total_and_current_concepts_are_not_equal(self):
        a=self.bound()
        b=self.bound()
        b['observations']=deepcopy(b['observations'])
        b['observations'][0]['concept']['schema_id']='ifrs-full_Assets'
        b['observations'][0]['concept']['expanded_name']='{https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full}Assets'
        self.assertEqual('fail',compare_concepts(a,b)['status'])

    def test_cross_version_same_local_name_is_not_automatically_equal(self):
        a=self.bound()
        b=self.bound()
        b['observations']=deepcopy(b['observations'])
        b['observations'][0]['concept']['expanded_name']='{http://xbrl.ifrs.org/taxonomy/2021-03-24/ifrs-full}Inventories'
        relation=compare_concepts(a,b)
        self.assertEqual('not_checked',relation['status'])
        self.assertEqual('namespace_version_differs_official_bridge_missing',relation['reason'])

    def test_exact_large_decimal_and_different_amount_keep_concept_independent(self):
        f=self.observation()
        f['exact_value']='9007199254740993.123456788'
        result=self.bound(f)
        self.assertEqual('pass',result['concept_status'])
        self.assertEqual('pass',result['context_status'])
        self.assertEqual('fail',result['amount_status'])

    def test_nil_is_unknown_even_if_another_duplicate_has_a_value(self):
        a=self.observation()
        b=deepcopy(a);b['exact_value']=None;b['nil']=True
        row={'account_id':'ifrs-full_Inventories','account_nm':'재고','currency':'KRW'}
        result=bind_record({'facts':[a,b]},row,'00129642','OFS',a['context']['period'],a['exact_value'],{})
        self.assertEqual('not_checked',result['amount_status'])

    def test_conflicting_duplicate_values_are_not_selected_by_equal_api_amount(self):
        a=self.observation()
        b=deepcopy(a);b['exact_value']='1'
        row={'account_id':'ifrs-full_Inventories','currency':'KRW'}
        result=bind_record({'facts':[a,b]},row,'00129642','OFS',a['context']['period'],a['exact_value'],{})
        self.assertEqual('fail',result['amount_status'])

    def test_other_company_scope_period_unit_and_detail_context_are_excluded(self):
        for mutation in ('company','scope','period','unit','detail'):
            f=self.observation()
            if mutation=='company':f['context']['entity']='00000001'
            if mutation=='scope':f['context']['scope']='CFS'
            if mutation=='period':f['context']['period']['as_of']='2024-12-31'
            if mutation=='unit':f['unit']['currency']='USD'
            if mutation=='detail':f['context']['primary_context']=False
            result=self.bound(f)
            self.assertEqual('pass',result['concept_status'],mutation)
            self.assertEqual('not_checked',result['context_status'],mutation)
            self.assertEqual('not_checked',result['amount_status'],mutation)

    def test_abstract_or_nonmonetary_concept_is_not_adopted(self):
        for field,value in [('abstract',True),('type','{'+XI+'}sharesItemType'),
                            ('substitution_group','{'+XI+'}tuple')]:
            f=self.observation();f['concept'][field]=value
            self.assertEqual('not_checked',self.bound(f)['concept_status'])

    def test_prefix_is_resolved_by_namespace_and_unbound_prefix_is_rejected(self):
        root=parse(b'<x xmlns:a="urn:first" xmlns:b="urn:second"/>')
        self.assertEqual('{urn:first}Assets',qname('a:Assets',root))
        self.assertNotEqual(qname('a:Assets',root),qname('b:Assets',root))
        with self.assertRaises(ValueError):qname('missing:Assets',root)

    def test_entity_expansion_and_malformed_xml_are_rejected_without_recovery(self):
        unsafe=b'<!DOCTYPE x [<!ENTITY y SYSTEM "file:///etc/passwd">]><x>&y;</x>'
        for raw in (unsafe,unsafe.decode().encode('utf-16'),b'<x>TK&Dist</x>'):
            with self.assertRaises((ValueError,etree.XMLSyntaxError)):parse(raw)

    def context_xml(self, extra=''):
        return parse(('''<xbrli:context xmlns:xbrli="'''+XI+'''" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
            xmlns:i="http://xbrl.ifrs.org/taxonomy/2021-03-24/ifrs-full">
            <xbrli:entity><xbrli:identifier scheme="http://dart.fss.or.kr/ifrs/CIK">00129642</xbrli:identifier>
            <xbrli:segment><xbrldi:explicitMember dimension="i:ConsolidatedAndSeparateFinancialStatementsAxis">i:SeparateMember</xbrldi:explicitMember>'''
            +extra+'''</xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant>
            </xbrli:period></xbrli:context>''').encode())

    def test_extra_bank_dimension_is_not_primary_total(self):
        self.assertTrue(context(self.context_xml())['primary_context'])
        node=self.context_xml('<xbrldi:explicitMember dimension="i:BorrowingsAxis">i:BankMember</xbrldi:explicitMember>')
        self.assertFalse(context(node)['primary_context'])

    def test_duplicate_dimensions_and_mixed_instant_duration_are_rejected(self):
        member='<xbrldi:explicitMember dimension="i:ConsolidatedAndSeparateFinancialStatementsAxis">i:ConsolidatedMember</xbrldi:explicitMember>'
        self.assertFalse(context(self.context_xml(member))['valid'])
        node=self.context_xml()
        period=node.find('{'+XI+'}period')
        etree.SubElement(period,'{'+XI+'}startDate').text='2025-01-01'
        etree.SubElement(period,'{'+XI+'}endDate').text='2025-12-31'
        self.assertFalse(context(node)['valid'])

    def test_unit_division_and_halfyear_period_are_explicit(self):
        node=parse(('<xbrli:unit xmlns:xbrli="'+XI+'" xmlns:c="http://www.xbrl.org/2003/iso4217"><xbrli:measure>c:KRW</xbrli:measure></xbrli:unit>').encode())
        self.assertEqual('KRW',unit(node))
        etree.SubElement(node,'{'+XI+'}divide')
        self.assertIsNone(unit(node))
        self.assertEqual({'kind':'duration','start':'2026-01-01','end':'2026-06-30'},current_period('2026','11012','CIS'))
        self.assertIsNone(current_period('2026','11013','CIS'))

    def test_issuer_extension_cannot_impersonate_official_concept_by_schema_id(self):
        f=self.observation()
        f['concept']['namespace']='http://dart.fss.or.kr/taxonomy/2025-12-31/entity00129642'
        f['concept']['expanded_name']='{'+f['concept']['namespace']+'}Inventories'
        result=self.bound(f)
        self.assertEqual('not_checked',result['concept_status'])
        self.assertEqual('unavailable',result['usage_scope'])

    def test_lookalike_scope_axis_from_nonstandard_namespace_is_extra_dimension(self):
        node=self.context_xml()
        segment=node.find('.//{'+XI+'}segment')
        fake=etree.fromstring(b'<xbrldi:explicitMember xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:fake="urn:issuer" dimension="fake:ConsolidatedAndSeparateFinancialStatementsAxis">fake:SeparateMember</xbrldi:explicitMember>')
        segment.append(fake)
        self.assertFalse(context(node)['primary_context'])

    def note_fixture(self, raw='0', nil=False, extra=False):
        from collections import defaultdict
        fact=self.observation()
        instance={'id':'native','content_hash':'a'*64}
        node=parse(('<n:Loan xmlns:n="urn:issuer" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" contextRef="ctx" unitRef="u"'
                    + (' xsi:nil="true"' if nil else '') + '>'+raw+'</n:Loan>').encode())
        axis='{https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full}BorrowingsByNameAxis'
        member='{urn:issuer}BankFacilityLoanMember'
        ctx=deepcopy(fact['context']);ctx['dimensions']={axis:member};ctx['typed_dimension_count']=0
        if extra:ctx['dimensions']['{urn:issuer}MaturityAxis']='{urn:issuer}LaterThanOneYearMember'
        definitions={axis:{'schema_id':'ifrs-full_BorrowingsByNameAxis'},member:{'schema_id':'issuer_BankFacilityLoanMember'}}
        return instance,node,fact['concept'],ctx,fact['unit'],definitions,defaultdict(list)

    def test_note_zero_is_explicit_candidate_and_never_changes_main_or_usage(self):
        from agent.finance.xbrl_mapping import borrowing_note_candidate
        candidate=borrowing_note_candidate(*self.note_fixture())
        self.assertTrue(candidate['explicit_zero'])
        self.assertEqual('0',candidate['exact_value'])
        self.assertEqual('discovery_only',candidate['status'])
        self.assertEqual('not_adopted',candidate['usage_scope'])
        self.assertTrue(candidate['location']['xpath'])

    def test_note_nil_or_non_numeric_text_is_not_zero(self):
        from agent.finance.xbrl_mapping import borrowing_note_candidate
        for fixture in (self.note_fixture('',True),self.note_fixture('–'),self.note_fixture('')):
            candidate=borrowing_note_candidate(*fixture)
            self.assertFalse(candidate['explicit_zero'])
            self.assertIsNone(candidate['exact_value'])
            self.assertEqual('not_adopted',candidate['usage_scope'])

    def test_note_maturity_dimension_is_preserved_without_permission_to_sum(self):
        from agent.finance.xbrl_mapping import borrowing_note_candidate
        candidate=borrowing_note_candidate(*self.note_fixture(extra=True))
        self.assertIn('{urn:issuer}MaturityAxis',candidate['other_dimensions'])
        self.assertEqual('not_adopted',candidate['usage_scope'])

    def test_unrelated_or_nonmonetary_note_is_not_loan_amount_candidate(self):
        from agent.finance.xbrl_mapping import borrowing_note_candidate
        fixture=list(self.note_fixture())
        fixture[2]=deepcopy(fixture[2]);fixture[2]['type']='{'+XI+'}sharesItemType'
        self.assertIsNone(borrowing_note_candidate(*fixture))
        fixture=list(self.note_fixture());fixture[3]['dimensions']={}
        self.assertIsNone(borrowing_note_candidate(*fixture))
