"""수용 시나리오별 실제 회귀 증거와 미구현 범위를 구분해 기록한다."""
from datetime import datetime,timezone
import io
import json
from pathlib import Path
import sys
import unittest
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from agent.finance.stage04_completion import code_fingerprint
from agent.research_store import safe_payload

# 테스트 함수 이름을 실행 결과의 고유 ID에 연결한다. 전체 시나리오 수용 판정은 아님.
SCENARIOS=[
 ('최초 조사','partial',['test_company_input_creates_new_research_and_queue_atomically','test_borrowing_composition_hold_never_becomes_display_value'],'본문 기본 항목의 의미상 충족과 일반 기업은 미완료'),
 ('반기 3개월/누적','component',['test_quarter_is_not_mixed_with_cumulative_and_missing_does_not_become_zero'],'합성 열 선택 검사'),
 ('연결 만기표','partial',['test_comparison_policy_disallows_annual_interim_scope_composition_mix'],'범위 혼합 차단; 일반 만기표 해석 전체는 미평가'),
 ('잠정/후속 차이','partial',['test_cutoff_and_supersession_never_force_pass'],'시점/정정 선택 검사; 차이의 원인 설명은 미평가'),
 ('기준일 후 정정','component',['test_api_returned_future_correction_is_retained_but_not_usable'],'합성 DART 응답'),
 ('공시 없음/일시 오류/키','component',['test_authentication_no_data_and_limit_do_not_retry','test_temporary_failures_retry_with_all_attempts_counted'],'실제 외부 장애를 유발하지 않은 모의 전송'),
 ('API/모델 한도','component',['test_exhausted_budget_prevents_network_after_restart','test_exhausted_model_budget_publishes_verified_data_and_explicit_model_gap'],'한도 차단/미작성 표시'),
 ('근거 없는 원인','partial',['test_forged_model_fact_cannot_be_published'],'허용 사실 복사만 허용; 일반 원인 해석 모델은 미구현'),
 ('첨부 실행 지시','component',['test_http_multipart_one_receipt_and_attachment_instruction_is_untrusted'],'합성 첨부 지시를 미검증 입력으로 보존; 포괄적 방어 인증 아님'),
 ('PDF 텍스트 없는 페이지','partial',['test_blank_pdf_encryption_and_page_limit_are_not_full_read'],'빈/암호 PDF를 읽기 완료로 표시하지 않음; OCR 미구현'),
 ('XLSX 수식/숨김/링크','component',['test_xlsx_hidden_cells_cached_formula_and_missing_cache_are_distinct','test_xlsx_traversal_entities_and_macros_are_rejected'],'저장 캐시만 읽고 수식/링크 미실행; 캐시 최신성 미확인'),
 ('충돌/누락 우선 심화','partial',['test_borrowing_composition_hold_never_becomes_display_value'],'수치 보류는 검사; 자율 쟁점 심화 우선순위 효과는 미입증'),
 ('답변 없이 종료','component',['test_initial_and_corrected_versions_preserve_contents_and_readback'],'미확인/질문 포함 초안'),
 ('답변 후 재조사','component',['test_answer_creates_new_version_keeps_old_amounts_and_unknowns'],'합성 답변; 독립 금융 확인 아님'),
 ('메모 저장/반영','component',['test_memo_is_stored_only_unless_explicitly_reflected'],'합성 입력'),
 ('회사/목적/기준 변경','partial',['test_cross_research_stale_basis_and_unknown_issue_rejected','test_foreign_company_cutoff_and_excess_budget_rejected_before_creation'],'잘못된 혼합 거부; 일반 새 기준 자동 재조사는 미구현'),
 ('브라우저 단절/재접속','partial',['test_api_can_read_both_published_versions_without_model_call'],'게시본 재조회; 활성 브라우저 단절 전체 시뮬레이션은 미실행'),
 ('서버 장애/복구','component',['test_restart_recovers_interrupted_without_new_budget'],'합성 런타임; 실제 프로세스 시험은7단계 별도 증거'),
 ('사용자 중단/재시작','component',['test_explicit_stop_is_not_resumed_on_restart_or_new_input'],'같은 조사 대기 포함'),
 ('진행 중 계획 수정','partial',['test_running_inputs_frozen_next_input_gets_next_version','test_new_plan_is_queued_idempotent_and_keeps_previous_execution'],'목적/우선 항목을 새 기준으로 대기; 회사/기준일 변경은 미지원'),
 ('수정 계획 후 중단','component',['test_stop_blocks_late_publication_pending_and_future_auto_execution','test_same_plan_does_not_create_new_budget_and_stopped_plan_waits'],'현재/대기 실행 중단과 명시 재개 전 계획 대기'),
 ('분할 첨부','component',['test_multiple_files_one_input_one_run_and_old_report_preserved','test_partial_asset_save_waits_and_retry_does_not_reset_budget'],'한 묶음 여러 파일의 접수/부분 저장 복구; 별도 메시지는 별도 입력'),
 ('후속/복구 예산','component',['test_stop_resume_preserves_input_calls_and_old_reports','test_duplicate_coalescing_and_conflicting_key'],'합성 실행'),
 ('모델 장애/제한 결과','component',['test_hard_deadline_cancels_stalled_model_and_keeps_unpublished_limit','test_exhausted_model_budget_publishes_verified_data_and_explicit_model_gap'],'미검증 모델 문구 게시 없음'),
 ('게시 직후 장애','component',['test_saved_draft_is_reused_after_publish_interruption'],'저장 초안/완료 결과 재사용; 실제 프로세스 증거는7단계'),
 ('외부 요청 중 장애','partial',['test_actual_kill_after_file_before_db_completes_once','test_exhausted_budget_prevents_network_after_restart'],'로컬 파일/사용량 검사; 실제 DART 전송 중 강제 종료는 미실행'),
 ('한도 후 반복 재시작','component',['test_model_attempt_limit_survives_repeated_interruptions','test_two_consecutive_failed_recoveries_are_durable_and_blocked'],'기존 예산/실패 보존'),
 ('이전 작업자 늦은 응답','component',['test_resume_from_other_server_fences_previous_owner_late_result'],'실행 소유권 검사'),
 ('여러 조사 대기열','partial',['test_one_owner_and_direct_execution_cannot_bypass_worker_lock'],'단일 소유자 검사; 여러 조사 삭제 격리는 미구현'),
 ('삭제 후 재시작','partial',['test_archive_restart_restore_preserves_files_reports_and_pause'],'삭제 대신 복원 가능한 보관함; 실제 삭제/보관기간 자동화는 미구현'),
 ('PDF/Markdown ZIP','component',['test_pdf_and_zip_share_version_and_keep_korean_unknown_and_exact_numbers','test_export_corruption_is_rejected_and_failed_export_does_not_change_report'],'합성/실자료 출력; 실제 시각 검토는8단계'),
 ('후속 텍스트 인젝션','component',['test_prompt_injection_correction_never_replaces_verified_amount'],'직접 만든 합성 입력; 포괄적 방어 인증 아님'),
 ('평가기 오판 방지','component',['test_safety_hold_is_not_public_coverage_and_promotion_is_rejected','test_wrong_values_scope_dates_units_duplicates_and_omissions_are_failures'],'합성 평가 입력'),
 ('공개 인용의 범위/원문','component',['test_prior_half_year_with_same_labels_is_not_selected','test_forged_text_other_row_and_raw_tamper_are_rejected'],'별도·당반기·정확한 원문 행/머리글 검사; 전체 의미 검토 아님'),
 ('보관 HTML 인코딩','component',['test_utf8_html_with_stale_euckr_declaration_retains_original_table'],'UTF-8 바이트와 예전 선언 충돌의 합성 회귀')]


class RecordedResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):super().__init__(*args,**kwargs);self.records={}
    def addSuccess(self,test):super().addSuccess(test);self.records[test.id()]='pass'
    def addFailure(self,test,error):super().addFailure(test,error);self.records[test.id()]='fail'
    def addError(self,test,error):super().addError(test,error);self.records[test.id()]='error'
    def addSkip(self,test,reason):super().addSkip(test,reason);self.records[test.id()]='skipped'


def main():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:8]
    target=ROOT/'data/research/evaluation_stage09_failures'/stamp;target.mkdir(parents=True)
    output=io.StringIO();suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    result=unittest.TextTestRunner(stream=output,resultclass=RecordedResult).run(suite)
    (target/'tests.log').write_text(output.getvalue());rows=[]
    for title,scope,names,limitation in SCENARIOS:
        linked={};missing=[]
        for name in names:
            matches=[(i,s) for i,s in result.records.items() if i.endswith('.'+name)]
            if len(matches)!=1:missing.append(name)
            else:linked[matches[0][0]]=matches[0][1]
        rows.append({'scenario':title,'coverage':scope,'test_results':[{'test_id':i,'status':s} for i,s in linked.items()],'missing_test_names':missing,
            'test_status':'not_run' if not names else 'pass' if not missing and all(s=='pass' for s in linked.values()) else 'fail',
            'full_scenario_acceptance':'not_demonstrated','limitation':limitation})
    summary={'status':'recorded','code_fingerprint':code_fingerprint(ROOT),'tests_run':result.testsRun,
        'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),
        'scenarios':rows,'test_results':[{'test_id':i,'status':s} for i,s in result.records.items()],'scope':'synthetic regression; not independent financial evaluation',
        'external_financial_requests':0,'real_model_calls':0}
    (target/'result.json').write_text(safe_payload(summary))
    print(safe_payload({'result_file':str((target/'result.json').relative_to(ROOT)),
        'tests_run':result.testsRun,'failed_scenario_links':[r['scenario'] for r in rows if r['test_status']=='fail'],
        'not_implemented_count':sum(r['coverage']=='not_implemented' for r in rows)}),flush=True)
    return 0 if result.wasSuccessful() and all(r['test_status']!='fail' for r in rows) else 1

if __name__=='__main__':sys.exit(main())
