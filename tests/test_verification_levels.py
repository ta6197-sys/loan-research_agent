"""독립 검증 축·상세 구성·계정 합계 사용과 은행별 배분의 경계를 검사한다."""
from copy import deepcopy
import unittest
from agent.finance.document_extractor import DocumentExtractor
from agent.finance.numeric_validation import NumericValidationService
from agent.finance.borrowing_details import collect_details, assess_detail, vector
from agent.finance.composition_review import composition_reviews
from agent.finance.verification_levels import usage_scope, confirmed_caption_link
from agent.finance.validation_acceptance import _review_valid,_detail_valid

COVER='<COMPANY-NAME AREGCIK="00129642">회사</COMPANY-NAME><TU AUNIT="PERIODFROM" AUNITVALUE="20250101">2025</TU><TU AUNIT="PERIODTO" AUNITVALUE="20251231">2025</TU>'
ACCOUNTS=('short_term_borrowings','current_long_term_borrowings','long_term_borrowings')


def fixture(second_amount='60',current='(30)',short_caption='유동 차입금(사채 포함)',zero_component_in=None):
    facts=[];validations=[];details=[]
    for source_id in ('a','b'):
        caption='유동 차입금' if source_id=='a' else short_caption
        second=second_amount if source_id=='b' else '60'
        extra=source_id==zero_component_in
        body=''.join('<TR><TE>'+label+'</TE><TE>'+a+'</TE><TE>'+b+'</TE>'+('<TE>'+('0' if a or b else '')+'</TE>' if extra else '')+'<TE>'+total+'</TE></TR>'
            for label,a,b,total in [(caption,'40',second,'100'),('장기차입금','40','60','100'),
                ('비유동차입금(사채 포함)의 유동성 대체 부분','','',current),
                ('비유동차입금(사채 포함)의 비유동성 부분','','','70')])
        raw=('<DOCUMENT>'+COVER+'<TABLE-GROUP ACLASS="{XBRL}NT_S_DETAIL"><P>당기</P><P>(단위 : 원)</P><TABLE><THEAD>'
             '<TR><TH ROWSPAN="2">구분</TH><TH COLSPAN="'+str(3 if extra else 2)+'">차입금명칭</TH><TH ROWSPAN="2">차입금명칭 합계</TH></TR>'
             '<TR><TH>A은행 대출</TH><TH>B은행 대출</TH>'+('<TH>C은행 종료대출</TH>' if extra else '')+'</TR></THEAD><TBODY>'+body+'</TBODY></TABLE></TABLE-GROUP></DOCUMENT>').encode()
        import hashlib
        source={'id':source_id,'content_hash':hashlib.sha256(raw).hexdigest()}
        for account,value in zip(ACCOUNTS,('100','30','70')):
            fid=source_id+account;name=caption if account==ACCOUNTS[0] else (account if source_id=='a' else account+'(사채 포함)')
            fact={'id':fid,'run_id':'run','company':{'corp_code':'00129642'},'source':source,
                'fragment':{'data':{'context':{'row_label_location':{}}}},
                'data':{'account':account,'scope':'OFS','currency':'KRW','period':{'kind':'instant','as_of':'2025-12-31'},'original_account':name}}
            facts.append(fact);validations.append({'id':'v'+fid,'fact_id':fid,'rule_id':'numeric_fact','status':'pass',
                'data':{**fact['data'],'source_id':source_id,'account_id':account,'amount_comparison':{'original_normalized':value}}})
        service=NumericValidationService(None);table=DocumentExtractor(raw).extract()['tables'][0]
        details+=collect_details(source,raw,service._document_identity(raw),[(source_id+'table',table)],facts,validations,service._raw_check)
    return facts,validations,details


class VerificationLevelTests(unittest.TestCase):
    def test_exact_detail_components_allow_account_total_but_not_lender_maturity(self):
        facts,validations,details=fixture();self.assertEqual(4,len(details))
        reviews=composition_reviews(facts,validations,details);self.assertEqual(3,len(reviews))
        for review in reviews:
            self.assertEqual('pass',review['status']);levels=review['data']['verification_levels']
            self.assertTrue(levels['usage_scope']['account_total_link']);self.assertFalse(levels['usage_scope']['lender_maturity_analysis'])
            expected='not_applicable' if review['data']['account']==ACCOUNTS[0] else 'not_checked'
            self.assertEqual(expected,levels['dimensions']['lender_allocation']['status'])

    def test_missing_dash_or_ambiguous_component_never_becomes_zero(self):
        for value in ('','-','60 extra'):
            f,v,d=fixture(second_amount=value);review=next(r for r in composition_reviews(f,v,d) if r['data']['account']==ACCOUNTS[0])
            self.assertEqual('not_checked',review['status']);self.assertTrue(any(x['data']['unresolved'] for x in d))
            self.assertEqual('not_checked',review['data']['verification_levels']['dimensions']['detail_total']['status'])

    def test_same_total_with_different_components_is_not_equivalent(self):
        f,v,d=fixture();short=next(x for x in d if x['data']['source_id']=='b' and x['data']['family']=='short')
        short['data']['components'][0]['amount']='45';short['data']['components'][1]['amount']='55'
        review=next(r for r in composition_reviews(f,v,d) if r['data']['account']==ACCOUNTS[0])
        self.assertEqual('not_checked',review['status']);self.assertEqual('fail',review['data']['verification_levels']['dimensions']['composition_identity']['status'])

    def test_failed_reclassification_does_not_erase_a_passing_detail_sum(self):
        f,v,d=fixture(current='(31)');review=next(r for r in composition_reviews(f,v,d) if r['data']['account']==ACCOUNTS[1])
        dimensions=review['data']['verification_levels']['dimensions']
        self.assertEqual('pass',dimensions['detail_total']['status']);self.assertEqual('fail',dimensions['reclassification']['status'])
        self.assertFalse(review['data']['verification_levels']['usage_scope']['account_total_link'])

    def test_changed_lender_name_or_missing_context_keeps_link_withheld(self):
        for mutation in ('name','scope','company','date','currency','run'):
            f,v,d=fixture();target=next(x for x in d if x['data']['source_id']=='b' and x['data']['family']=='short')['data']
            if mutation=='name':target['components'][0]['name']='다른은행 대출'
            elif mutation=='scope':target['scope']='CFS'
            elif mutation=='company':target['company_code']='other'
            elif mutation=='date':target['period']={'kind':'instant','as_of':'2024-12-31'}
            elif mutation=='currency':target['currency']='USD'
            else:target['run_id']='other'
            r=next(r for r in composition_reviews(f,v,d) if r['data']['account']==ACCOUNTS[0])
            self.assertEqual('not_checked',r['status'],mutation)

    def test_large_component_amounts_do_not_round_during_identity_check(self):
        a='1234567890123456789012345678901234567890';b=a[:-1]+'1'
        self.assertNotEqual(vector([{'name':'은행','amount':a}]),vector([{'name':'은행','amount':b}]))

    def test_forged_verification_or_permission_is_rejected(self):
        f,v,d=fixture();reviews=composition_reviews(f,v,d);bv={x['id']:x for x in v+d+reviews};bf={x['fact_id']:x for x in v}
        self.assertTrue(all(_detail_valid(x,bf) for x in d));self.assertTrue(all(_review_valid(x,bv,bf) for x in reviews))
        for kind in ('allocation','permission','detail_status','numeric_reference'):
            bad=deepcopy(reviews[1]);levels=bad['data']['verification_levels']
            if kind=='allocation':levels['dimensions']['lender_allocation']['status']='pass'
            elif kind=='permission':levels['usage_scope']['lender_maturity_analysis']=True
            elif kind=='numeric_reference':bad['data']['observations'][0]['numeric_validation_id']='missing'
            else:levels['dimensions']['detail_total']['status']='not_checked'
            self.assertFalse(_review_valid(bad,bv,bf),kind)

    def test_caption_link_requires_real_independent_evidence(self):
        f,v,d=fixture();reviews=composition_reviews(f,v,d);a='a'+ACCOUNTS[0];b='b'+ACCOUNTS[0]
        self.assertTrue(confirmed_caption_link(a,b,reviews,d));self.assertIsNone(confirmed_caption_link(a,b,reviews,[]))
        self.assertIsNone(confirmed_caption_link(a,'other',reviews,d))

    def test_invalid_or_missing_dimension_does_not_default_to_pass(self):
        f,v,d=fixture();dims=composition_reviews(f,v,d)[0]['data']['verification_levels']['dimensions']
        for mutation in ('missing','unknown_status'):
            bad=deepcopy(dims)
            if mutation=='missing':bad.pop('lender_allocation')
            else:bad['detail_total']['status']='partial'
            with self.assertRaises(ValueError):usage_scope(bad)


    def test_missing_gross_component_does_not_hide_verified_reclassification(self):
        f,v,d=fixture()
        target=next(x for x in d if x['data']['source_id']=='b' and x['data']['family']=='long')['data']
        target['components'].pop();target['unresolved']=[{'reason':'missing_amount'}]
        r=next(r for r in composition_reviews(f,v,d) if r['data']['account']==ACCOUNTS[1])
        axes=r['data']['verification_levels']['dimensions']
        self.assertEqual('not_checked',axes['detail_total']['status'])
        self.assertEqual('not_checked',axes['composition_identity']['status'])
        self.assertEqual('pass',axes['reclassification']['status'])
        self.assertFalse(r['data']['verification_levels']['usage_scope']['account_total_link'])

    def test_detail_sum_failure_does_not_hide_component_identity(self):
        f,v,d=fixture()
        for item in d:
            if item['data']['family']=='short':item['data']['displayed_total']='101'
        r=next(r for r in composition_reviews(f,v,d) if r['data']['account']==ACCOUNTS[0])
        axes=r['data']['verification_levels']['dimensions']
        self.assertEqual('fail',axes['detail_total']['status'])
        self.assertEqual('pass',axes['composition_identity']['status'])
        self.assertFalse(r['data']['verification_levels']['usage_scope']['account_total_link'])

    def test_cross_source_link_uses_confirmed_composition_for_each_caption_difference(self):
        from agent.finance.borrowing_composition import link_same_period_compositions
        f,v,d=fixture();reviews=composition_reviews(f,v,d)
        composition={'id':'original_comp','status':'pass','data':{'source_id':'a','scope':'OFS',
            'period':{'kind':'instant','as_of':'2025-12-31'},'account_fact_ids':{a:['a'+a] for a in ACCOUNTS}}}
        links=link_same_period_compositions(f,v,[composition],reviews,d)
        self.assertEqual(1,len(links))
        refs=links[0]['data']['supporting_links'][0]['accounts']
        self.assertTrue(all(refs[a].get('composition_review_id') for a in ACCOUNTS))
        self.assertEqual([],link_same_period_compositions(f,v,[composition],reviews,[]))


    def test_negative_component_does_not_become_a_verified_borrowing_total(self):
        f,v,d=fixture();target=d[0]['data']
        target['components'][0]['amount']='-40';target['components'][1]['amount']='140'
        self.assertEqual('not_checked',assess_detail(target)['detail_total']['status'])

    def test_legacy_lender_rowspan_is_required_for_a_continuation_row(self):
        from agent.finance.borrowing_details import vertical
        def table(span):
            raw=('<DOCUMENT><P>(단위: 원)</P><TABLE><TR><TH>차입처</TH><TH>내역</TH><TH>당기말</TH></TR>'
                '<TR><TE ROWSPAN="'+str(span)+'">A은행</TE><TE>대출1</TE><TE>40</TE></TR>'
                '<TR><TE>대출2</TE><TE>60</TE></TR><TR><TE COLSPAN="2">합계</TE><TE>100</TE></TR>'
                '</TABLE></DOCUMENT>').encode()
            return DocumentExtractor(raw).extract()['tables'][0]
        valid=vertical(table(2))[0];self.assertEqual(2,len(valid['components']));self.assertFalse(valid['unresolved'])
        # Without ROWSPAN the second row starts in column 1 and has no amount in the declared date column.
        invalid=vertical(table(1))[0];self.assertTrue(invalid['unresolved'])
