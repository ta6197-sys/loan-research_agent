"""명시적 0원 항목과 결측값을 구별하고 잔액 구성의 제한된 사용을 검사한다."""
from copy import deepcopy
import unittest
from tests.test_verification_levels import fixture, ACCOUNTS
from agent.finance.composition_review import composition_reviews
from agent.finance.verification_levels import review_levels, usage_scope, confirmed_caption_link
from agent.finance.validation_acceptance import _review_valid


class BalanceIdentityTests(unittest.TestCase):
    def test_explicit_zero_preserved_while_only_balance_identity_passes(self):
        facts,validations,details=fixture(zero_component_in='a');before=deepcopy(details)
        reviews=composition_reviews(facts,validations,details)
        self.assertEqual(3,len(reviews))
        for review in reviews:
            self.assertEqual('pass',review['status']);self.assertEqual('account_balance_totals_only',review['data']['use_status'])
            levels=review['data']['verification_levels'];identity=levels['dimensions']['composition_identity']
            self.assertEqual('fail',identity['all_disclosed_items']['status'])
            self.assertEqual('pass',identity['positive_balance_items']['status'])
            self.assertTrue(identity['positive_balance_items']['zero_items_preserved'])
            self.assertTrue(all(c['amount']=='0' and c['name_location'] and c['amount_location'] for c in identity['positive_balance_items']['zero_items_preserved']))
            self.assertTrue(levels['usage_scope']['account_total_link'])
            self.assertFalse(levels['usage_scope']['all_disclosed_items_match'])
            self.assertFalse(levels['usage_scope']['lender_maturity_analysis'])
        self.assertEqual(before,details)

    def test_blank_dash_or_ambiguous_item_is_never_filtered_out(self):
        for value in ('','-','60 extra'):
            f,v,d=fixture(second_amount=value,zero_component_in='a')
            review=next(r for r in composition_reviews(f,v,d) if r['data']['account']==ACCOUNTS[0])
            self.assertEqual('not_checked',review['status'])
            self.assertEqual('not_checked',review['data']['verification_levels']['dimensions']['composition_identity']['positive_balance_items']['status'])
            self.assertFalse(review['data']['verification_levels']['usage_scope']['account_total_link'])

    def test_positive_name_or_amount_difference_cannot_be_hidden_by_zero_rule(self):
        for mutation in ('name','amount'):
            f,v,d=fixture(zero_component_in='a');data=next(x['data'] for x in d if x['data']['source_id']=='b' and x['data']['family']=='short')
            if mutation=='name':data['components'][0]['name']='다른은행 다른대출'
            else:data['components'][0]['amount']='45';data['components'][1]['amount']='55'
            review=next(r for r in composition_reviews(f,v,d) if r['data']['account']==ACCOUNTS[0])
            self.assertEqual('not_checked',review['status']);identity=review['data']['verification_levels']['dimensions']['composition_identity']
            self.assertEqual('fail',identity['positive_balance_items']['status'])

    def test_reclassification_remains_required_for_long_term_account(self):
        f,v,d=fixture(current='(31)',zero_component_in='a')
        review=next(r for r in composition_reviews(f,v,d) if r['data']['account']==ACCOUNTS[1]);axes=review['data']['verification_levels']['dimensions']
        self.assertEqual('pass',axes['composition_identity']['positive_balance_items']['status'])
        self.assertEqual('fail',axes['reclassification']['status']);self.assertEqual('not_checked',review['status'])

    def test_zero_only_difference_never_proves_full_contracts_or_maturity(self):
        f,v,d=fixture(zero_component_in='a');r=composition_reviews(f,v,d)[1]
        axes=deepcopy(r['data']['verification_levels']['dimensions']);axes['lender_allocation']['status']='pass'
        self.assertTrue(usage_scope(axes)['account_total_link']);self.assertFalse(usage_scope(axes)['lender_maturity_analysis'])

    def test_substatus_or_zero_evidence_forgery_is_rejected(self):
        f,v,d=fixture(zero_component_in='a');reviews=composition_reviews(f,v,d);bv={x['id']:x for x in v+d+reviews};bf={x['fact_id']:x for x in v}
        self.assertTrue(all(_review_valid(r,bv,bf) for r in reviews))
        for mutation in ('all_items','zero_evidence','permission'):
            r=deepcopy(reviews[0]);levels=r['data']['verification_levels'];identity=levels['dimensions']['composition_identity']
            if mutation=='all_items':identity['all_disclosed_items']['status']='pass'
            elif mutation=='zero_evidence':identity['positive_balance_items']['zero_items_preserved']=[]
            else:levels['usage_scope']['all_disclosed_items_match']=True
            self.assertFalse(_review_valid(r,bv,bf),mutation)

    def test_alias_link_requires_real_scoped_proof(self):
        f,v,d=fixture(zero_component_in='a');reviews=composition_reviews(f,v,d)
        self.assertTrue(confirmed_caption_link('a'+ACCOUNTS[0],'b'+ACCOUNTS[0],reviews,d))
        self.assertIsNone(confirmed_caption_link('a'+ACCOUNTS[0],'b'+ACCOUNTS[0],reviews,[]))

    def test_legacy_review_does_not_silently_gain_balance_permission(self):
        f,v,d=fixture(zero_component_in='a');r=deepcopy(composition_reviews(f,v,d)[0]);r['data']['version']='composition-review-1.1'
        legacy=review_levels(r,d)
        self.assertEqual('borrowing-verification-levels-1.1',legacy['version']);self.assertFalse(legacy['usage_scope']['account_total_link'])
        self.assertNotIn('positive_balance_items',legacy['dimensions']['composition_identity'])


    def test_account_calculation_keeps_scoped_usage_and_evidence(self):
        from agent.finance.composition_review import withhold_affected_calculations, calculation_composition_usage
        f,v,d=fixture(zero_component_in='a');reviews=composition_reviews(f,v,d)
        calculation={'id':'c','formula_version':'test:V11_change_previous_year_end','status':'pass',
            'data':{'result':'10','inputs':{'current':{'fact_id':'a'+ACCOUNTS[1]}}}}
        withhold_affected_calculations([calculation],reviews)
        usage=calculation['data']['composition_usage']
        self.assertEqual('pass',calculation['status']);self.assertEqual('10',calculation['data']['result'])
        self.assertEqual('account_balance_totals_only',usage['use_status'])
        self.assertFalse(usage['all_disclosed_items_match']);self.assertFalse(usage['per_lender_repayment_analysis_verified'])
        self.assertTrue(usage['validation_ids']);self.assertEqual(usage,calculation_composition_usage(calculation,reviews))
