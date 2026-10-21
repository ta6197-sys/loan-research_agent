"""코드 검증 완료와 실제 금융 자료 확인 상태의 분리를 검사한다."""
from copy import deepcopy
import unittest

from agent.finance.stage04_completion import CHECKS,FAULTS,assess_implementation


class CompletionTests(unittest.TestCase):
    def fixture(self):
        financial={'exit_code':0,'failures':[],'financial_validation_status':'incomplete',
                   'required_complete':False,'additional_complete':False,'stage4_complete':False}
        record={'code_fingerprint':'a'*64,'checks':{name:{'status':'pass'} for name in CHECKS}}
        record['checks']['whole_suite'].update(tests_run=200,failures=0,errors=0,skipped=0)
        record['checks']['fault_injection']['cases']={name:1 for name in FAULTS}
        record['checks']['restart_readback']['processes']=[100,101]
        record['checks']['preservation'].update(original_file_count=10,changed=0,deleted=0)
        return financial,record

    def test_implementation_can_complete_without_promoting_financial_unknowns(self):
        financial,record=self.fixture();before=deepcopy(financial)
        result=assess_implementation(financial,record,'a'*64)
        self.assertTrue(result['stage4_implementation_complete']);self.assertTrue(result['ready_for_stage5'])
        self.assertFalse(result['financial_scope_complete']);self.assertFalse(result['financial_required_complete'])
        self.assertEqual('incomplete',result['financial_validation_status']);self.assertEqual(before,financial)

    def test_missing_failed_stale_or_empty_verification_cannot_finish_implementation(self):
        for mutation in (*CHECKS,'stale','empty_suite','fault','restart','preservation','regression'):
            financial,record=self.fixture()
            if mutation in CHECKS:record['checks'].pop(mutation)
            elif mutation=='stale':record['code_fingerprint']='b'*64
            elif mutation=='empty_suite':record['checks']['whole_suite']['tests_run']=0
            elif mutation=='fault':record['checks']['fault_injection']['cases'][FAULTS[0]]=0
            elif mutation=='restart':record['checks']['restart_readback']['processes']=[100,100]
            elif mutation=='preservation':record['checks']['preservation']['changed']=1
            else:financial['exit_code']=1;financial['failures']=[{'reason':'fake_binding'}]
            self.assertFalse(assess_implementation(financial,record,'a'*64)['ready_for_stage5'],mutation)
