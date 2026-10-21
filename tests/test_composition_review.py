"""구성 표현의 미확인은 숫자 일치와 별개이며 관련 사용을 차단한다."""
from copy import deepcopy
import unittest
from agent.finance.composition_review import composition_reviews,withhold_affected_calculations
from tests.test_validation_acceptance import fixture
from agent.finance.validation_acceptance import assess_stage04


class CompositionReviewTests(unittest.TestCase):
    def fixture(self):
        facts=[];validations=[]
        for i,name in enumerate(('단기차입금','단기차입금(사채 포함)')):
            fid=str(i)
            facts.append({'id':fid,'run_id':'r','company':{'corp_code':'00129642'},
                'source':{'id':'s'+fid,'content_hash':'hash','metadata':{'receipt_number':'receipt'+fid}},
                'fragment':{'data':{'context':{'row_label_location':{'line':i+1}}}},
                'data':{'account':'short_term_borrowings','scope':'OFS','currency':'KRW',
                        'original_account':name,'period':{'kind':'instant','as_of':'2023-12-31'}}})
            validations.append({'id':'v'+fid,'fact_id':fid,'rule_id':'numeric_fact','status':'pass',
                'data':{'account_id':'ifrs-full_ShorttermBorrowings','amount_comparison':{'original_normalized':'100'}}})
        return facts,validations

    def test_equal_numbers_do_not_clear_caption_composition_ambiguity(self):
        facts,validations=self.fixture();before=deepcopy((facts,validations))
        reviews=composition_reviews(facts,validations)
        self.assertEqual(1,len(reviews));self.assertEqual('not_checked',reviews[0]['status'])
        self.assertEqual({'0','1'},set(reviews[0]['data']['affected_fact_ids']))
        self.assertEqual(before,(facts,validations))

    def test_different_company_run_date_scope_account_or_currency_is_not_linked(self):
        for field in ('company','run','date','scope','account','currency'):
            facts,validations=self.fixture();f=facts[1]
            if field=='company':f['company']['corp_code']='other'
            elif field=='run':f['run_id']='other'
            elif field=='date':f['data']['period']['as_of']='2024-12-31'
            else:f['data'][field]={'scope':'CFS','account':'long_term_borrowings','currency':'USD'}[field]
            self.assertEqual([],composition_reviews(facts,validations),field)

    def test_same_captions_or_unverified_candidate_does_not_create_review(self):
        for mutation in ('same','unverified'):
            facts,validations=self.fixture()
            if mutation=='same':facts[1]['data']['original_account']='단기차입금'
            else:validations[1]['status']='fail'
            self.assertEqual([],composition_reviews(facts,validations))

    def test_only_affected_comparisons_and_subtotals_are_withheld(self):
        facts,validations=self.fixture();reviews=composition_reviews(facts,validations)
        calculations=[]
        for i,(formula,fid) in enumerate((('V11_change_previous_year_end','0'),
                ('V12_three_borrowing_captions_subtotal','1'),('V11_change_same_period','other'),
                ('V08_assets_equals_liabilities_plus_equity','0'))):
            calculations.append({'id':str(i),'formula_version':'test:'+formula,'status':'pass',
                'data':{'result':'10','inputs':{'current':{'fact_id':fid}}}})
        withhold_affected_calculations(calculations,reviews)
        for c in calculations[:2]:
            self.assertEqual('not_checked',c['status']);self.assertNotIn('result',c['data'])
            self.assertEqual('10',c['data']['unverified_arithmetic_result'])
        self.assertTrue(all(c['status']=='pass' for c in calculations[2:]))

    def acceptance_fixture(self):
        batch,reference=fixture()
        batch['summary']['rule_scope']={k:'checked' for k in batch['summary']['rule_scope']}
        fid='2023FY-OFS-short_term_borrowings'
        review={'id':'review','rule_id':'V12_account_composition_review','status':'not_checked','fact_id':None,
            'data':{'reason':'bond_inclusion_caption_scope_requires_component_confirmation',
                'scope':'OFS','period':{'kind':'instant','as_of':'2023-12-31'},'affected_fact_ids':[fid]}}
        batch['validations'].append(review)
        calc={'id':'affected','formula_version':'test:V11_change_previous_year_end','status':'pass',
              'data':{'result':'1','inputs':{'previous':{'fact_id':fid}}}}
        batch['calculations'].append(calc)
        withhold_affected_calculations([calc],[review])
        return batch,reference,calc

    def test_required_review_blocks_completion_without_failing_regression(self):
        batch,reference,_=self.acceptance_fixture();result=assess_stage04(batch,reference)
        self.assertEqual(0,result['exit_code']);self.assertFalse(result['required_complete'])
        self.assertIn('account_composition_requires_confirmation',result['known_gaps'])

    def test_forged_pass_result_or_review_id_fails_acceptance(self):
        for mutation in ('pass','result','id','reason'):
            batch,reference,calc=self.acceptance_fixture()
            if mutation=='pass':calc['status']='pass'
            if mutation=='result':calc['data']['result']='1'
            if mutation=='id':calc['data']['account_composition_review_ids']=['missing']
            if mutation=='reason':calc['data']['deferred_reason']='consolidation_scope_review_required'
            self.assertEqual(1,assess_stage04(batch,reference)['exit_code'],mutation)
