"""개발 검증 기록으로 소프트웨어 완료를 판정한다. 금융 근거를 승격하지 않는다.

사용자 API나 모델 출력으로 받는 상태가 아니다. 오프라인 완료 검증 명령이 실제
시험·복사본 대조·고장 주입·재시작·보존 검사 후 작성한 개발 기록에 적용한다.
"""
import hashlib
from pathlib import Path


CHECKS=('whole_suite','real_data_integration','original_replay','fault_injection',
        'restart_readback','preservation','security_boundaries')
FAULTS=('missing_comparison','forced_composition_pass','fake_review_id','binding_hash',
        'binding_location','binding_member','augmentation','source_observation')


def code_fingerprint(root):
    root=Path(root)
    paths=[p for folder in ('agent','sh','tests') for p in (root/folder).rglob('*')
           if p.is_file() and p.suffix in {'.py','.sql','.sh'}]
    paths += [root/name for name in ('server.py','web_demo_server.py','mcp_server_finance.py',
                                    'requirements.txt','requirements.lock.txt')]
    paths += [root/name for name in ('finance.html','sh/build_finance_ui.cjs') if (root/name).is_file()]
    paths += [p for p in (root/'static').rglob('*') if p.is_file()]
    digest=hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(root)).encode());digest.update(b'\0')
        digest.update(path.read_bytes());digest.update(b'\0')
    return digest.hexdigest()


def assess_implementation(financial,record,current_fingerprint):
    checks=record.get('checks',{})
    missing=[name for name in CHECKS if checks.get(name,{}).get('status')!='pass']
    if record.get('code_fingerprint')!=current_fingerprint:missing.append('current_source_not_verified')
    suite=checks.get('whole_suite',{})
    if (type(suite.get('tests_run')) is not int or suite['tests_run']<=0
            or suite.get('failures')!=0 or suite.get('errors')!=0 or suite.get('skipped')!=0):
        missing.append('whole_suite_counts_invalid')
    faults=checks.get('fault_injection',{}).get('cases',{})
    if any(faults.get(name)!=1 for name in FAULTS):missing.append('required_fault_not_rejected')
    restart=checks.get('restart_readback',{}).get('processes',[])
    if len(restart)!=2 or len(set(restart))!=2:missing.append('independent_restart_missing')
    preserved=checks.get('preservation',{})
    if (not preserved.get('original_file_count') or preserved.get('changed')!=0
            or preserved.get('deleted')!=0):missing.append('original_preservation_not_confirmed')
    if financial.get('exit_code')!=0 or financial.get('failures'):missing.append('financial_regression_failed')
    complete=not missing
    return {'implementation_status':'complete' if complete else 'incomplete',
        'stage4_implementation_complete':complete,'ready_for_stage5':complete,
        'financial_validation_status':financial.get('financial_validation_status','not_checked'),
        'financial_required_complete':financial.get('required_complete',False),
        'financial_additional_complete':financial.get('additional_complete',False),
        'financial_scope_complete':financial.get('stage4_complete',False),
        'missing_implementation_checks':sorted(set(missing)),
        'limitation':'구현 검사 범위의 완료다. 금융 미확인·버전 대응·차입 보류를 해제하지 않으며 전체 XBRL 규격·독립 회계 검토를 인증하지 않는다.'}
