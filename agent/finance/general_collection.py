"""일반 기업의 OpenDART 초기 수집.

정기 보고서 목록과 원문을 확보하는 단계이며, 수집 성공을 금융 사실 검증이나 보고서
게시로 바꾸지 않는다.
"""
import asyncio
import json
import os
import re
from datetime import date

from agent.research_store import Conflict, now, safe_payload
from .dart_service import DartService
from .extraction_service import ExtractionService
from .document_extractor import ExtractionFailure
from .general_company import create_collection_retry


REPORT_CODES = (("사업보고서", "11011"), ("반기보고서", "11012"),
                ("분기보고서", "11013"), ("3분기보고서", "11014"))


def report_code(report_name):
    text = str(report_name or "")
    for label, code in REPORT_CODES:
        if text.startswith(label):
            return code
    return None


def report_year(report_name, receipt):
    """정기 보고서의 비교 기준연도는 접수연도가 아니라 보고서명에 표시된 기간이다."""
    match = re.search(r"\((20\d{2})\.\d{2}\)", str(report_name or ""))
    return int(match.group(1)) if match else int(receipt[:4])


def comparison_targets(cutoff):
    """기준일에서 최근 완결 3개 연간과 이용 가능한 최근 반기 2개를 계산한다."""
    cutoff_date=date.fromisoformat(str(cutoff))
    annual_years=tuple(range(cutoff_date.year-3,cutoff_date.year))
    latest_half_year=cutoff_date.year if cutoff_date.month>=8 else cutoff_date.year-1
    return [(year,'11011') for year in annual_years]+[(latest_half_year-1,'11012'),(latest_half_year,'11012')]


def select_regular_reports(items, cutoff):
    """비교 창의 3개 연간·직전/현재 반기만 고른다. 정정본은 접수일이 가장 늦은 것을 쓴다."""
    selected = {};targets=set(comparison_targets(cutoff))
    for item in items:
        code = report_code(item.get("report_nm"))
        receipt = str(item.get("rcept_no", ""))
        if code not in {"11011", "11012"} or not re.fullmatch(r"\d{14}", receipt):
            continue
        if receipt[:8] > cutoff.replace("-", ""):
            continue
        year = report_year(item.get("report_nm"), receipt)
        if (year,code) not in targets:
            continue
        key = (year, code)
        previous = selected.get(key)
        if previous is None or receipt > previous["rcept_no"]:
            selected[key] = {"year": year, "report_code": code, "rcept_no": receipt,
                             "report_nm": item.get("report_nm", ""), "rcept_dt": item.get("rcept_dt", "")}
    return [selected[key] for key in sorted(selected)]


def planned_reports(disclosures, cutoff):
    """공시 목록 누락이 있어도 합의한 비교 기간의 재무 API 조회 계획은 유지한다."""
    discovered = {(item["year"], item["report_code"]): item
                  for item in select_regular_reports(disclosures, cutoff)}
    required = comparison_targets(cutoff)
    return [discovered.get((year, code), {"year": year, "report_code": code,
                                           "rcept_no": None, "report_nm": None, "rcept_dt": None})
            for year, code in required]


def changed_regular_reports(previous_receipts, disclosures, cutoff):
    """같은 비교 창에서 새 접수번호(정정본 포함)만 반환한다."""
    known={str(value) for value in previous_receipts if value}
    return [item for item in planned_reports(disclosures, cutoff)
            if item.get('rcept_no') and item['rcept_no'] not in known]


class GeneralCollectionService:
    def __init__(self, store, api_key=None, *, dart_service=None):
        self.store = store
        self.dart = dart_service or DartService(store, api_key if api_key is not None else os.environ.get("OPENDART_API_KEY", ""))
        self.extractor = ExtractionService(store)

    def _begin(self, run_id):
        with self.store.connection(write=True) as conn:
            run, basis = self.store._context(conn, run_id)
            trigger = json.loads(run["trigger_json"])
            if trigger.get("kind") != "general_company":
                raise Conflict("일반 기업 초기 수집 실행이 아닙니다.")
            if run["status"] not in {"queued", "interrupted", "failed"}:
                raise Conflict("수집을 시작할 수 없는 실행 상태입니다.")
            conn.execute("UPDATE research_runs SET status='running' WHERE id=?", (run_id,))
            self.store._event(conn, basis["research_id"], basis["id"], run_id,
                              "general_collection_started", {"phase": "identity_and_disclosures"})
            return json.loads(basis["data_json"]), json.loads(self.store._one(
                conn, "research_researches", basis["research_id"])["company_json"])

    def _finish(self, run_id, result):
        with self.store.connection(write=True) as conn:
            run, basis = self.store._context(conn, run_id)
            trigger = json.loads(run["trigger_json"])
            trigger["phase"] = "collection_complete"
            trigger["collection_result"] = result
            conn.execute("UPDATE research_runs SET status='finished',trigger_json=? WHERE id=?",
                         (safe_payload(trigger), run_id))
            self.store._event(conn, basis["research_id"], basis["id"], run_id,
                              "general_collection_finished", result)

    async def _all_disclosures(self, run_id, corp_code, cutoff):
        """DART 페이지 전체를 읽되, 각 호출은 기존 실행 예산을 그대로 소비한다."""
        first=await self.dart.disclosures(run_id,corp_code,"2023-01-01",cutoff,page_no=1,page_count=100,last_report="Y")
        pages=[first]
        try:total_pages=int(first.get('total_page') or 1)
        except (TypeError,ValueError):raise ValueError('invalid_disclosure_pagination') from None
        if not 1<=total_pages<=1000:raise ValueError('invalid_disclosure_pagination')
        for page_no in range(2,total_pages+1):
            pages.append(await self.dart.disclosures(run_id,corp_code,"2023-01-01",cutoff,
                                                      page_no=page_no,page_count=100,last_report="Y"))
        return {'items':[item for page in pages for item in page['items']],
                'source_ids':[page['source_id'] for page in pages],
                'page_count':len(pages),'complete':len(pages)==total_pages}

    async def run(self, run_id):
        basis, company = self._begin(run_id)
        corp_code = company.get("corp_code")
        try:
            identity = await self.dart.company(run_id, corp_code)
            disclosed = await self._all_disclosures(run_id,corp_code,basis["public_cutoff"])
            targets = planned_reports(disclosed["items"],basis["public_cutoff"])
            financial_sources, archives, extracted = [], [], []
            for target in targets:
                receipts = {target["rcept_no"]} if target["rcept_no"] else set()
                for scope in ("OFS", "CFS"):
                    outcome = await self.dart.financials(run_id, corp_code, target["year"], target["report_code"], scope)
                    financial_sources.append({"target": target, "scope": scope, "result": outcome})
                    if outcome.get("status") == "ok":
                        receipts.add(outcome["receipt_number"])
                for receipt in sorted(receipt for receipt in receipts if receipt):
                    document = await self.dart.document(run_id, receipt)
                    xbrl = await self.dart.xbrl(run_id, receipt, target["report_code"])
                    archives.append({"target": target, "receipt_number": receipt,
                                     "document_source_id": document["source_id"], "xbrl_source_id": xbrl["source_id"]})
                    for member in document.get("members", []):
                        try:
                            extracted.append(await asyncio.to_thread(self.extractor.extract_source, member["source_id"]))
                        except ExtractionFailure as error:
                            # 표/셀 안전 한도를 초과한 원문은 수집 성공을 무효화하지 않는다.
                            # 이후 원문 범위 분할 추출이 필요하다는 제한으로 남긴다.
                            extracted.append({"source_id": member["source_id"], "status": "extraction_withheld",
                                              "reason": str(error)})
            result = {"identity_source_id": identity["source_id"], "disclosures_source_ids": disclosed["source_ids"],
                      "disclosures_page_count":disclosed['page_count'],
                      "target_reports": targets, "financial_requests": len(financial_sources),
                      "archive_count": len(archives), "extracted_source_count": len(extracted),
                      "financial_verification": "not_performed",
                      "next_phase": "numeric_and_xbrl_validation"}
            self._finish(run_id, result)
            return result
        except Exception:
            with self.store.connection(write=True) as conn:
                run, basis_row = self.store._context(conn, run_id)
                conn.execute("UPDATE research_runs SET status='failed' WHERE id=? AND status='running'", (run_id,))
                self.store._event(conn, basis_row["research_id"], basis_row["id"], run_id,
                                  "general_collection_failed", {"phase": "collection", "at": now()})
            raise

    async def refresh(self, research_id):
        """기존 조사를 열 때 최신·정정 공시만 먼저 확인한다.

        변동이 없으면 기존 게시본과 원문을 그대로 사용한다. 변동이 있으면 새 실행을
        만들고 전체 수집으로 이어져 이전 판의 근거를 바꾸지 않는다.
        """
        receipt=create_collection_retry(self.store, research_id)
        run_id=receipt['run_id'];basis,company=self._begin(run_id)
        try:
            disclosed=await self._all_disclosures(run_id,company['corp_code'],basis['public_cutoff'])
            previous_run=receipt['previous_run_id']
            with self.store.connection() as conn:
                old_receipts={receipt_number for row in conn.execute(
                    'SELECT metadata_json FROM research_sources WHERE run_id=?',(previous_run,))
                    for receipt_number in [json.loads(row['metadata_json']).get('receipt_number')]
                    if receipt_number}
            changes=changed_regular_reports(old_receipts,disclosed['items'],basis['public_cutoff'])
            if not changes:
                result={'freshness':'no_new_or_corrected_regular_report','checked_disclosures_source_ids':disclosed['source_ids'],
                        'checked_report_count':len(planned_reports(disclosed['items'],basis['public_cutoff'])),
                        'next_phase':'reuse_existing_published_or_verified_result'}
                with self.store.connection(write=True) as conn:
                    run,basis_row=self.store._context(conn,run_id);trigger=json.loads(run['trigger_json'])
                    trigger.update(phase='freshness_checked_no_change',collection_result=result)
                    conn.execute("UPDATE research_runs SET status='finished',trigger_json=? WHERE id=?",(safe_payload(trigger),run_id))
                    self.store._event(conn,basis_row['research_id'],basis_row['id'],run_id,'general_freshness_checked',result)
                return {**receipt,'status':'unchanged','changes':[],'checked_disclosures_source_ids':disclosed['source_ids']}
            with self.store.connection(write=True) as conn:
                run,basis_row=self.store._context(conn,run_id);trigger=json.loads(run['trigger_json'])
                trigger['phase']='freshness_change_detected';trigger['detected_report_changes']=changes
                conn.execute("UPDATE research_runs SET status='queued',trigger_json=? WHERE id=?",(safe_payload(trigger),run_id))
                self.store._event(conn,basis_row['research_id'],basis_row['id'],run_id,'general_freshness_change_detected',
                                  {'changes':changes,'checked_disclosures_source_ids':disclosed['source_ids']})
            result=await self.run(run_id)
            return {**receipt,'status':'changed','changes':changes,'collection_result':result}
        except Exception:
            with self.store.connection(write=True) as conn:
                run,basis_row=self.store._context(conn,run_id)
                conn.execute("UPDATE research_runs SET status='failed' WHERE id=? AND status='running'",(run_id,))
                self.store._event(conn,basis_row['research_id'],basis_row['id'],run_id,'general_freshness_check_failed',{'at':now()})
            raise
