"""계정명·공식 코드·회사·시점·금액이 모두 맞는 교차 공시 연결만 허용한다."""
from copy import deepcopy
import unittest
from agent.finance.borrowing_composition import link_same_period_compositions,ACCOUNTS


class CrossSourceCompositionTests(unittest.TestCase):
    def fixture(self):
        facts=[];validations=[]
        for source in ('original','comparative'):
            for i,account in enumerate(ACCOUNTS):
                fid=source+account
                facts.append({'id':fid,'run_id':'run','company':{'corp_code':'00129642'},'source':{'id':source},
                              'data':{'account':account,'scope':'OFS','currency':'KRW','original_account':account,
                                      'period':{'kind':'instant','as_of':'2023-12-31'}}})
                validations.append({'id':'v-'+fid,'fact_id':fid,'status':'pass','data':{
                    'account_id':account,'amount_comparison':{'original_normalized':str(i+1)}}})
        composition={'id':'composition','status':'pass','data':{'source_id':'original','scope':'OFS',
            'period':{'kind':'instant','as_of':'2023-12-31'},'account_fact_ids':{a:['original'+a] for a in ACCOUNTS}}}
        return facts,validations,[composition]

    def test_matching_evidence_links_all_three_accounts_and_preserves_source(self):
        facts,validations,compositions=self.fixture();before=deepcopy(facts)
        links=link_same_period_compositions(facts,validations,compositions)
        self.assertEqual(1,len(links));self.assertEqual('comparative',links[0]['data']['source_id'])
        self.assertEqual('composition',links[0]['data']['composition_validation_id']);self.assertEqual(before,facts)

    def test_equal_values_do_not_override_company_period_scope_code_name_or_currency(self):
        for mutation in ('company','run','period','scope','code','name','currency','amount'):
            facts,validations,compositions=self.fixture();target=facts[-1]
            if mutation=='company':target['company']['corp_code']='99999999'
            if mutation=='run':target['run_id']='other-run'
            if mutation=='period':target['data']['period']['as_of']='2024-12-31'
            if mutation=='scope':target['data']['scope']='CFS'
            if mutation=='code':validations[-1]['data']['account_id']='other'
            if mutation=='name':target['data']['original_account']='other'
            if mutation=='currency':target['data']['currency']='USD'
            if mutation=='amount':validations[-1]['data']['amount_comparison']['original_normalized']='99'
            self.assertEqual([],link_same_period_compositions(facts,validations,compositions),mutation)
