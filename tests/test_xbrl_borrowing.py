"""동일 공시 XBRL의 0·상세 대응과 원문 보존/오연결 거부를 합성 자료로 검사한다."""
from copy import deepcopy
import unittest

from tests.test_verification_levels import fixture, ACCOUNTS
from agent.finance.borrowing_details import assess_detail
from agent.finance.composition_review import composition_reviews
from agent.finance.validation_acceptance import _detail_valid
from agent.finance.xbrl_borrowing import (attach_evidence, binding_valid, eligible,
    native_profile_vector, valid_augmentation)

XI='http://www.xbrl.org/2003/instance'
NS='https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full'
ISSUER='http://dart.fss.or.kr/taxonomy/2025-12-31/entity00129642'
RECEIPT='20260318001187'


def proof(source='native'):
    return {'source_id':source,'content_hash':'a'*64,'xpath':'/*/*[1]'}


def candidate(name,amount,member):
    expanded='{'+ISSUER+'}'+member
    axis='{'+NS+'}BorrowingsByNameAxis'
    return {'concept':{'schema_id':'ifrs-full_ShorttermBorrowings',
                'expanded_name':'{'+NS+'}ShorttermBorrowings','namespace':NS,
                'period_type':'instant','abstract':False,'type':'{'+XI+'}monetaryItemType',
                'declaration':proof('schema')},
        'context':{'valid':True,'entity':'00129642','scope':'OFS',
            'period':{'kind':'instant','as_of':'2025-12-31'},'location':proof(),
            'dimensions':{axis:expanded,'{'+NS+'}ConsolidatedAndSeparateFinancialStatementsAxis':'{'+NS+'}SeparateMember'}},
        'unit':{'currency':'KRW','location':proof()},'location':proof(),
        'member_definition':{'schema_id':'entity00129642_'+member,'expanded_name':expanded,
            'namespace':ISSUER,'declaration':proof('schema')},
        'member_labels':[{'language':'ko','text':name+' [구성요소]','location':proof('label')}],
        'borrowing_name_axis':axis,'borrowing_member':expanded,'nil':False,
        'exact_value':amount,'raw_value':amount,'explicit_zero':amount=='0',
        'other_dimensions':{},'typed_dimension_count':0}


def native_fixture(missing_zero=True,alias=False):
    facts,validations,details=fixture(zero_component_in='b' if missing_zero else None)
    details=[d for d in details if d['data']['family']=='short']
    for detail in details:
        data=detail['data']
        if alias and data['source_id']=='b':data['components'][0]['name']='A은행 다른표시 대출'
        if missing_zero and data['source_id']=='b':
            zero=data['components'].pop()
            data['unresolved']=[{'name':zero['name'],'name_location':zero['name_location'],
                'locations':[zero['amount_location']],'reason':'missing_or_ambiguous_amount','raw_values':['-']}]
            data['checks']=assess_detail(data);detail['status']='not_checked'
    pools={d['data']['source_id']:[candidate(c['name'],c['amount'],chr(65+i)+'Member')
                                for i,c in enumerate(d['data']['components'])] for d in details}
    if missing_zero:pools['b'].append(candidate('C은행 종료대출','0','CMember'))
    # 각 공시의 별도 접수번호·라벨을 보존한다. 공시 사이 ID만으로 연결하지 않는다.
    sources={sid:{'id':sid,'metadata':{'receipt_number':RECEIPT+sid}} for sid in pools}
    reports={RECEIPT+sid:{'receipt':RECEIPT+sid,'borrowing_note_candidates':pool} for sid,pool in pools.items()}
    return facts,validations,details,sources,reports


class NativeBorrowingTests(unittest.TestCase):
    def test_explicit_zero_is_separate_evidence_and_preserves_original_dash(self):
        f,v,d,s,r=native_fixture();before=deepcopy(d)
        attach_evidence(d,r,s)
        b=d[1]['data']
        self.assertEqual(before[1]['data']['unresolved'],b['source_observation']['unresolved'])
        self.assertEqual(['-'],b['source_observation']['unresolved'][0]['raw_values'])
        self.assertEqual('0',b['components'][-1]['amount'])
        self.assertEqual('same_report_xbrl_explicit_zero',b['components'][-1]['amount_origin'])
        self.assertTrue(b['components'][-1]['original_document_value_still_missing'])
        self.assertTrue(valid_augmentation(b));self.assertEqual('pass',b['checks']['detail_total']['status'])
        self.assertIsNotNone(native_profile_vector(b))

    def test_other_receipt_company_scope_date_currency_axis_nil_and_unit_do_not_fill_zero(self):
        for mutation in ('receipt','company','scope','date','currency','axis','typed','nil','unit','negative'):
            f,v,d,s,r=native_fixture();p=r[RECEIPT+'b']['borrowing_note_candidates'][-1]
            if mutation=='receipt':r[RECEIPT+'b']['receipt']='other'
            elif mutation=='company':p['context']['entity']='00000001'
            elif mutation=='scope':p['context']['scope']='CFS'
            elif mutation=='date':p['context']['period']['as_of']='2024-12-31'
            elif mutation=='currency':p['unit']['currency']='USD'
            elif mutation=='axis':p['other_dimensions']={'MaturityAxis':'LaterMember'}
            elif mutation=='typed':p['typed_dimension_count']=1
            elif mutation=='nil':p['nil']=True;p['exact_value']=None
            elif mutation=='unit':p['unit']={}
            else:p['exact_value']='-1'
            attach_evidence(d,r,s)
            self.assertTrue(d[1]['data']['unresolved'],mutation)
            self.assertIsNone(native_profile_vector(d[1]['data']),mutation)

    def test_conflicting_or_nil_duplicate_cannot_be_hidden_behind_matching_zero(self):
        for nil in (False,True):
            f,v,d,s,r=native_fixture();pool=r[RECEIPT+'b']['borrowing_note_candidates']
            extra=deepcopy(pool[-1]);extra['exact_value']='1' if not nil else None;extra['nil']=nil;pool.append(extra)
            attach_evidence(d,r,s)
            self.assertTrue(d[1]['data']['unresolved'])
            self.assertIsNone(native_profile_vector(d[1]['data']))

    def test_missing_location_or_ambiguous_text_cannot_be_replaced_with_zero(self):
        for mutation in ('name_location','amount_location','ambiguous','multiple_cells'):
            f,v,d,s,r=native_fixture();item=d[1]['data']['unresolved'][0]
            if mutation=='name_location':item.pop('name_location')
            elif mutation=='amount_location':item.pop('locations')
            elif mutation=='ambiguous':item['raw_values']=['60 extra']
            else:item['raw_values']=['','-']
            attach_evidence(d,r,s)
            self.assertTrue(d[1]['data']['unresolved'],mutation)

    def test_individual_disclosure_alias_uses_complete_profile_without_claiming_bank_identity(self):
        f,v,d,s,r=native_fixture(missing_zero=False,alias=True)
        attach_evidence(d,r,s)
        review=next(x for x in composition_reviews(f,v,d) if x['data']['account']==ACCOUNTS[0])
        identity=review['data']['verification_levels']['dimensions']['composition_identity']
        self.assertEqual('fail',identity['all_disclosed_items']['status'])
        self.assertEqual('pass',identity['native_disclosure_balance_items']['status'])
        self.assertEqual('account_balance_totals_only',review['data']['use_status'])
        self.assertFalse(review['data']['verification_levels']['usage_scope']['lender_maturity_analysis'])

    def test_one_sided_binding_or_extra_positive_member_cannot_link_aliases(self):
        for mutation in ('one_side','extra','amount','same_id_other_name'):
            f,v,d,s,r=native_fixture(missing_zero=False,alias=True)
            if mutation=='one_side':r.pop(RECEIPT+'a')
            elif mutation=='extra':r[RECEIPT+'b']['borrowing_note_candidates'].append(candidate('미포함대출','1','ExtraMember'))
            elif mutation=='amount':r[RECEIPT+'b']['borrowing_note_candidates'][0]['exact_value']='41'
            else:r[RECEIPT+'b']['borrowing_note_candidates'][0]['member_labels'][0]['text']='다른대출'
            attach_evidence(d,r,s)
            review=next(x for x in composition_reviews(f,v,d) if x['data']['account']==ACCOUNTS[0])
            self.assertEqual('not_checked',review['status'],mutation)

    def test_forged_augmentation_and_hash_flags_require_original_replay(self):
        f,v,d,s,r=native_fixture();attach_evidence(d,r,s)
        by_fact={x['fact_id']:x for x in v};record=d[1]
        # 수용 경계는 원문 재독해가 없으면 플래그만으로 통과시키지 않는다.
        self.assertFalse(_detail_valid(record,by_fact))
        expected={record['id']:deepcopy(record['data'])}
        self.assertTrue(_detail_valid(record,by_fact,expected))
        for mutation in ('hash','member','zero','original','index','location'):
            bad=deepcopy(record);data=bad['data'];zero=data['components'][-1]
            if mutation=='hash':zero['xbrl_binding']['location']['content_hash']='b'*64
            elif mutation=='member':zero['xbrl_binding']['member_definition']['schema_id']='entity00129642_Forged'
            elif mutation=='zero':zero['xbrl_binding']['exact_value']='1'
            elif mutation=='original':data['source_observation']['unresolved']=[]
            elif mutation=='index':data['resolved_unresolved_items'][0]['original_index']=99
            else:zero['amount_location']['raw_span_sha256']='c'*64
            self.assertFalse(_detail_valid(bad,by_fact,expected),mutation)

    def test_native_profile_rejects_fake_namespace_and_nonfinite_amount(self):
        f,v,d,s,r=native_fixture();data=d[0]['data'];p=r[RECEIPT+'a']['borrowing_note_candidates'][0]
        for value in ('NaN','Infinity','-1'):
            bad=deepcopy(p);bad['exact_value']=value
            self.assertFalse(eligible(bad,data))
        bad=deepcopy(p);bad['borrowing_name_axis']='{urn:fake}BorrowingsByNameAxis'
        self.assertFalse(eligible(bad,data))
