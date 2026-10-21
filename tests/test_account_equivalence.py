"""승인된 정확한 별칭 조합과 합계/유동성/채권 구성 오연결 거부 시험."""
from copy import deepcopy
import unittest
from agent.finance.account_equivalence import verified_alias_pair,PAIRS


class AccountEquivalenceTests(unittest.TestCase):
    def pair(self,account='inventory'):
        names,code,classification=PAIRS[account]
        result=[]
        for i,name in enumerate(sorted(names)):
            result.append({'fact_id':str(i),'numeric_validation_id':'v'+str(i),'account':account,
                           'official_account_id':code,'original_account':name,'classification':classification,
                           'classification_evidence':{'text':classification,'location':{'raw_span_verified':True}},
                           'scope':'OFS','currency':'KRW','composition_check':{'status':'pass','composition_validation_id':'c'}})
        return result

    def test_only_the_four_exact_pairs_are_recognised(self):
        for account in PAIRS:
            pair=self.pair(account);self.assertIsNotNone(verified_alias_pair(*pair))
        pair=self.pair();pair[0]['original_account']='상품재고'
        self.assertIsNone(verified_alias_pair(*pair))

    def test_current_assets_cannot_match_total_assets_even_with_forged_common_code(self):
        pair=self.pair();pair[0]['original_account']='유동자산';pair[1]['original_account']='자산총계'
        self.assertIsNone(verified_alias_pair(*pair))
        pair=self.pair();pair[1]['official_account_id']='ifrs-full_Assets'
        self.assertIsNone(verified_alias_pair(*pair))

    def test_pure_trade_or_other_receivables_cannot_replace_combined_current_receivables(self):
        for name in ('매출채권','기타채권','비유동매출채권및기타채권','자산'):
            pair=self.pair('receivables');pair[0]['original_account']=name
            self.assertIsNone(verified_alias_pair(*pair))

    def test_noncurrent_classification_missing_evidence_and_scope_mix_are_rejected(self):
        for mutation in ('noncurrent','no_evidence','scope','currency','code'):
            pair=self.pair()
            if mutation=='noncurrent':pair[1]['classification']='비유동자산'
            if mutation=='no_evidence':pair[1]['classification_evidence']=None
            if mutation=='scope':pair[1]['scope']='CFS'
            if mutation=='currency':pair[1]['currency']='USD'
            if mutation=='code':pair[1]['official_account_id']='ifrs-full_CurrentAssets'
            self.assertIsNone(verified_alias_pair(*pair))

    def test_borrowing_requires_composition_for_both_periods(self):
        for account in ('short_term_borrowings','current_long_term_borrowings'):
            pair=self.pair(account);pair[1]['composition_check']={'status':'not_checked'}
            self.assertIsNone(verified_alias_pair(*pair))
