"""수용 판정은 실제 문서의 금융적 정확성과 구별하여 합성 결과로 검사한다."""
from copy import deepcopy
import unittest

from agent.finance.validation_acceptance import assess_stage04, PERIODS, SCOPES, FLOW_ACCOUNTS, MANDATORY_CHECKS
from agent.finance.extraction_service import ACCOUNT_RULES


def fixture():
    validations=[];reference=[];calculations=[]
    for period in PERIODS:
        year=period[:4];end=year+('-06-30' if period.endswith('H1') else '-12-31')
        for scope in SCOPES:
            for account in ACCOUNT_RULES:
                fid=f'{period}-{scope}-{account}'
                temporal=({'kind':'duration','start':year+'-01-01','end':end} if account in FLOW_ACCOUNTS
                          else {'kind':'instant','as_of':end})
                validations.append({'id':'v-'+fid,'fact_id':fid,'rule_id':'numeric_fact','status':'pass',
                    'data':{'account':account,'scope':scope,'period':temporal,
                            'checks':{rule:{'status':'pass'} for rule in MANDATORY_CHECKS}}})
                reference.append({'period':period,'scope':scope,'account':account,'status':'pass','fact_ids':[fid]})
    for end in ('2025-06-30','2025-12-31','2026-06-30'):
        for scope in SCOPES:
            for formula,role in [('V08_assets_equals_liabilities_plus_equity','expected'),
                                 ('V09_cf_end_equals_bs_cash','cash_end'),('V10_cash_rollforward','cash_begin')]:
                calculations.append({'id':end+scope+formula,'formula_version':'test:'+formula,'status':'pass',
                    'data':{'inputs':{role:{'scope':scope,'period':{'as_of':end} if role=='expected' else {'end':end}}}}})
    batch={'validations':validations,'calculations':calculations,'summary':{'rule_scope':{
        'V12':'not_checked','latest_correction_history':'not_checked','consolidation_scope_changes':'not_checked'}}}
    return batch,{'items':reference,'counts':{'pass':130}}


class AcceptanceTests(unittest.TestCase):
    def test_regression_pass_does_not_mean_financial_scope_complete(self):
        batch,reference=fixture();r=assess_stage04(batch,reference)
        self.assertEqual(0,r['exit_code']);self.assertEqual('pass',r['regression_status'])
        self.assertFalse(r['stage4_complete']);self.assertEqual('incomplete',r['financial_validation_status'])

    def test_reference_fail_missing_duplicate_and_false_summary_block(self):
        for mutation in ('failure','missing','duplicate','unverified','wrong_evidence'):
            with self.subTest(mutation=mutation):
                batch,reference=fixture()
                if mutation=='failure':reference['items'][0]['status']='fail'
                if mutation=='missing':reference['items'].pop()
                if mutation=='duplicate':reference['items'][-1]=deepcopy(reference['items'][0])
                if mutation=='unverified':reference['items'][0]['status']='not_checked'
                if mutation=='wrong_evidence':reference['items'][0]['fact_ids']=[reference['items'][1]['fact_ids'][0]]
                r=assess_stage04(batch,reference)
                self.assertEqual(1,r['exit_code']);self.assertEqual('fail',r['regression_status'])

    def test_numeric_failure_missing_check_and_borrowing_failure_block(self):
        for mutation in ('failure','missing','borrow_note'):
            batch,reference=fixture()
            if mutation=='failure':batch['validations'][0]['status']='fail'
            if mutation=='missing':batch['validations'][0]['data']['checks'].pop('V07')
            if mutation=='borrow_note':batch['validations'][0]['data']['checks']['V12']={'status':'fail'}
            self.assertEqual(1,assess_stage04(batch,reference)['exit_code'])

    def test_calculation_failure_unknown_gap_and_missing_family_block(self):
        for mutation in ('failure','unknown_gap','missing_family'):
            batch,reference=fixture()
            if mutation=='failure':batch['calculations'][0]['status']='fail'
            if mutation=='unknown_gap':batch['calculations'][0]['status']='not_checked'
            if mutation=='missing_family':batch['calculations']=[c for c in batch['calculations'] if 'V08' not in c['formula_version']]
            self.assertEqual(1,assess_stage04(batch,reference)['exit_code'])

    def test_only_named_known_gaps_remain_partial(self):
        batch,reference=fixture()
        extra=[{'id':'borrow','formula_version':'test:V12_three_borrowing_captions_subtotal','status':'not_checked',
                'data':{'deferred_reason':'borrowing_note_composition_required'}},
               {'id':'consolidation','formula_version':'test:V11_change_same_period','status':'not_checked',
                'data':{'deferred_reason':'consolidation_scope_review_required','inputs':{'current':{'scope':'CFS'}}}},
               {'id':'cash','formula_version':'test:V09_cf_end_equals_bs_cash','status':'not_checked',
                'data':{'reason':'missing_ambiguous_or_unverified_inputs','scope':'OFS','period':{'end':'2024-06-30'},
                        'missing_or_unverified_inputs':['cash_at_same_end_date']}}]
        batch['calculations']+=extra
        batch['validations'].append({'id':'missing_original','fact_id':None,'rule_id':'V07_source_coverage','status':'not_checked',
            'data':{'reason':'matching_original_not_available','receipt_number':'20240315000633'}})
        result=assess_stage04(batch,reference)
        self.assertEqual(0,result['exit_code']);self.assertIn('matching_original_missing',result['known_gaps'])
        batch['calculations'][-1]['data']['period']['end']='2026-06-30'
        self.assertEqual(1,assess_stage04(batch,reference)['exit_code'])


    def test_extra_optional_period_gap_must_be_declared_explicitly(self):
        batch,reference=fixture()
        batch['calculations'].append({'id':'extra_comparative','formula_version':'test:V09_cf_end_equals_bs_cash',
          'status':'not_checked','data':{'reason':'missing_ambiguous_or_unverified_inputs','scope':'OFS',
          'period':{'end':'2023-06-30'},'missing_or_unverified_inputs':['cash_at_same_end_date']}})
        self.assertEqual(1,assess_stage04(batch,reference)['exit_code'])
        self.assertEqual(0,assess_stage04(batch,reference,optional_balance_dates=('2023-06-30',))['exit_code'])


class CompletionSectionTests(unittest.TestCase):
    def cleared_fixture(self):
        batch, reference = fixture()
        batch['summary']['rule_scope'] = {k: 'checked' for k in batch['summary']['rule_scope']}
        return batch, reference

    def extra_cash(self, end='2023-06-30', status='not_checked'):
        return {'id': 'extra_cash', 'formula_version': 'test:V09_cf_end_equals_bs_cash',
                'status': status, 'data': {'scope': 'OFS', 'period': {'end': end},
                    'reason': 'missing_ambiguous_or_unverified_inputs',
                    'missing_or_unverified_inputs': ['cash_at_same_end_date']}}

    def test_optional_gap_keeps_required_complete_and_additional_incomplete(self):
        batch, reference = self.cleared_fixture()
        batch['calculations'].append(self.extra_cash())
        result = assess_stage04(batch, reference, optional_balance_dates=('2023-06-30',))
        self.assertTrue(result['required_complete'])
        self.assertEqual('필수 항목 완료', result['completion']['required']['label'])
        self.assertFalse(result['additional_complete']);self.assertFalse(result['stage4_complete'])
        self.assertEqual('추가 항목 미완료', result['completion']['additional']['label'])
        self.assertIn('cash_at_2023-06-30_missing', result['known_gaps'])
        self.assertEqual(0, result['exit_code'])

    def test_optional_failure_is_visible_and_regression_still_fails(self):
        batch, reference = self.cleared_fixture()
        batch['calculations'].append(self.extra_cash(status='fail'))
        result = assess_stage04(batch, reference)
        self.assertTrue(result['required_complete']);self.assertFalse(result['additional_complete'])
        self.assertEqual(1, result['exit_code'])
        self.assertEqual('calculation_not_passed', result['completion']['additional']['failures'][0]['reason'])

    def test_required_or_unclassified_gap_blocks_required_completion(self):
        for kind in ('current_period', 'global'):
            batch, reference = self.cleared_fixture()
            if kind == 'current_period':batch['calculations'].append(self.extra_cash('2026-06-30'))
            else:batch['summary']['rule_scope']['V12'] = 'not_checked'
            self.assertFalse(assess_stage04(batch, reference)['required_complete'])

    def test_bad_required_evidence_context_cannot_be_reclassified_optional(self):
        batch, reference = self.cleared_fixture()
        batch['validations'][0]['data']['period'] = {'kind': 'instant', 'as_of': '2021-12-31'}
        result = assess_stage04(batch, reference)
        self.assertFalse(result['required_complete'])
        self.assertTrue(result['completion']['required']['failures'])

    def test_no_additional_results_is_not_labelled_additional_complete(self):
        batch, reference = self.cleared_fixture()
        result = assess_stage04(batch, reference)
        self.assertTrue(result['required_complete']);self.assertIsNone(result['additional_complete'])
        self.assertEqual('추가 항목 없음', result['completion']['additional']['label'])


    def test_invalid_or_missing_scope_context_is_conservatively_required(self):
        for scope, period in [('OFS', {'end': '2023-99-99'}),
                              ('unknown', {'end': '2023-06-30'}),
                              ('OFS', {'start': '2023-07-01', 'end': '2023-06-30'})]:
            batch, reference = self.cleared_fixture()
            extra = self.extra_cash(status='fail')
            extra['data'].update(scope=scope, period=period)
            batch['calculations'].append(extra)
            self.assertFalse(assess_stage04(batch, reference)['required_complete'])


    def test_a_passing_borrowing_subtotal_without_composition_evidence_fails(self):
        batch, reference = self.cleared_fixture()
        batch['calculations'].append({'id':'forged_subtotal','formula_version':'test:V12_three_borrowing_captions_subtotal',
                                      'status':'pass','data':{'result':'150','composition_validation_id':'missing'}})
        result=assess_stage04(batch,reference)
        self.assertEqual(1,result['exit_code'])
        self.assertFalse(result['required_complete'])
