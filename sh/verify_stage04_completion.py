"""실제 개발 검증을 실행해 4단계 구현 완료와 금융 미확인을 별도 기록한다.

외부 수집·모델 없이 전체 회귀, 새 분석 복사본, 원문 재독해, 고장 주입,
MCP/서로 다른 HTTP 서버 재조회, 기존 자료 보존을 확인한다.
"""
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ['OPENDART_API_KEY']=''
os.environ['LANGCHAIN_TRACING_V2']='false';os.environ['LANGSMITH_TRACING']='false'
os.environ['APP_ALLOW_EXTERNAL_MODEL']='false';os.environ['APP_ALLOW_EXTERNAL_TRACE']='false'

from agent.finance.stage04_completion import FAULTS,assess_implementation,code_fingerprint
from agent.finance.validation_acceptance import assess_stage04
from agent.research_store import ResearchStore,safe_payload
from sh.validate_stage04_xbrl_integrated import ORIGINAL,INPUTS


def preserved_files():
    files={ROOT/'.env'}
    for root in (ORIGINAL,*INPUTS,ROOT/'doc/finance/cases'):
        if root.exists():files.update(p for p in root.rglob('*') if p.is_file() and not p.is_symlink())
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files) if p.exists()}


def main():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    destination=ROOT/'data/research/validation_stage04_completion'/stamp;destination.mkdir(parents=True)
    before=preserved_files();fingerprint=code_fingerprint(ROOT)
    checks={};log=io.StringIO()
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    def test_ids(group):
        for test in group:
            if isinstance(test,unittest.TestSuite):yield from test_ids(test)
            else:yield test.id()
    executed_ids=set(test_ids(suite))
    tests=unittest.TextTestRunner(stream=log,verbosity=1).run(suite)
    (destination/'tests.log').write_text(log.getvalue())
    checks['whole_suite']={'status':'pass' if tests.wasSuccessful() else 'fail','tests_run':tests.testsRun,
        'failures':len(tests.failures),'errors':len(tests.errors),'skipped':len(tests.skipped)}
    if not tests.wasSuccessful():raise ValueError('whole_suite_failed')
    print(json.dumps({'whole_suite':checks['whole_suite']},ensure_ascii=False),flush=True)
    completed=subprocess.run([sys.executable,str(ROOT/'sh/validate_stage04_xbrl_integrated.py')],
        cwd=ROOT,capture_output=True,text=True,timeout=180)
    (destination/'integration.log').write_text(completed.stdout+completed.stderr)
    if completed.returncode:raise ValueError('new_copy_integration_failed')
    summary=json.loads(completed.stdout.strip().splitlines()[-1]);target=ROOT/summary['result_file'];target=target.parent
    financial=json.loads((target/'acceptance.json').read_text())
    batch=json.loads((target/'numeric_batch.json').read_text());reference=json.loads((target/'reference_comparison.json').read_text())
    store=ResearchStore(target/'session.db',target/'sources')
    checks['real_data_integration']={'status':'pass','result_file':str((target/'result.json').relative_to(ROOT)),
        'numeric_batch_id':batch['id'],'comparison_count':financial['required_comparison_coverage']['observed_count']}
    replay=assess_stage04(batch,reference,optional_balance_dates=('2023-06-30',),store=store)
    assert replay['exit_code']==0
    checks['original_replay']={'status':'pass','native_detail_count':sum('source_observation' in v['data'] for v in batch['validations'])}
    cases={}
    for fault in FAULTS:
        bad=deepcopy(batch)
        if fault=='missing_comparison':
            # 추가 기간이 아닌 필수 비교를 제거해야 누락 차단을 확인할 수 있다.
            by_fact={v['fact_id']:v['data'] for v in bad['validations'] if v.get('fact_id')}
            c=next(c for c in bad['calculations'] if c['formula_version'].endswith('V11_change_previous_year_end')
                   and by_fact.get(c['data'].get('inputs',{}).get('current',{}).get('fact_id'),{}).get('period',{}).get('as_of')=='2025-12-31')
            bad['calculations'].remove(c)
        elif fault in ('forced_composition_pass','fake_review_id'):
            c=next(c for c in bad['calculations'] if c['data'].get('deferred_reason')=='account_composition_review_required')
            if fault=='forced_composition_pass':c['status']='pass';c['data']['result']='0'
            else:c['data']['account_composition_review_ids']=['missing']
        else:
            detail=next(v['data'] for v in bad['validations'] if v['data'].get('xbrl_profile_pool')
                        and any(c.get('xbrl_binding') for c in v['data'].get('components',[])))
            component=next(c for c in detail['components'] if c.get('xbrl_binding'))
            if fault=='binding_hash':component['xbrl_binding']['location']['content_hash']='f'*64
            elif fault=='binding_location':component['xbrl_binding']['location']['xpath']='/*/missing'
            elif fault=='binding_member':component['xbrl_binding']['member_definition']['schema_id']='entity00129642_Forged'
            elif fault=='augmentation':detail['resolved_unresolved_items']=[{'original_index':999,'original_item':{}}]
            else:detail['source_observation']['components']=[]
        rejected=assess_stage04(bad,reference,optional_balance_dates=('2023-06-30',),store=store)
        cases[fault]=rejected['exit_code']
        assert rejected['exit_code']==1
    checks['fault_injection']={'status':'pass','cases':cases}
    print(json.dumps({'integration':'pass','fault_injection':cases},ensure_ascii=False),flush=True)
    runtime=subprocess.run([sys.executable,str(ROOT/'sh/verify_xbrl_runtime.py'),str(target)],
        cwd=ROOT,capture_output=True,text=True,timeout=120)
    (destination/'runtime.log').write_text(runtime.stdout+runtime.stderr)
    if runtime.returncode:raise ValueError('runtime_readback_failed')
    runtime_result=json.loads((target/'runtime_read_check.json').read_text())
    checks['restart_readback']={'status':'pass','processes':[p['pid'] for p in runtime_result['http_processes']],
        'artifact':str((target/'runtime_read_check.json').relative_to(ROOT))}
    with store.connection() as conn:
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    deleted=[p for p in before if not (ROOT/p).exists()]
    changed=[p for p,h in before.items() if (ROOT/p).exists() and hashlib.sha256((ROOT/p).read_bytes()).hexdigest()!=h]
    checks['preservation']={'status':'pass' if not changed and not deleted else 'fail',
        'original_file_count':len(before),'changed':len(changed),'deleted':len(deleted)}
    # 실제 실행한 보안 시험 파일이 전체 회귀에 들어갔는지 확인한다.
    modules=('test_finance_security.','test_student_package.','test_dart_protocol.')
    security_included=all(any(name.removeprefix('tests.').startswith(module) for name in executed_ids) for module in modules)
    checks['security_boundaries']={'status':'pass' if security_included else 'fail',
        'scope':'local_request_tool_allowlist_package_tests_in_whole_suite'}
    record={'code_fingerprint':fingerprint,'checks':checks,'external_requests':0,'model_calls':0}
    result={'verification':record,'implementation':assess_implementation(financial,record,code_fingerprint(ROOT)),
        'financial_acceptance':financial,'analysis_directory':str(target.relative_to(ROOT))}
    (destination/'verification.json').write_text(safe_payload(result))
    print(json.dumps({'verification_file':str((destination/'verification.json').relative_to(ROOT)),
        'tests':checks['whole_suite'],'implementation':result['implementation'],'preservation':checks['preservation']},ensure_ascii=False),flush=True)
    return 0 if result['implementation']['stage4_implementation_complete'] else 1


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as error:
        print(json.dumps({'status':'completion_verification_stopped','error_type':type(error).__name__}),flush=True)
        sys.exit(1)
