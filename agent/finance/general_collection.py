"""일반 기업의 OpenDART 초기 수집.

정기 보고서 목록과 원문을 확보하는 단계이며, 수집 성공을 금융 사실 검증이나 보고서
게시로 바꾸지 않는다.
"""
import asyncio
import json
import os
import re
from calendar import monthrange
from datetime import date
from pathlib import PurePosixPath

from lxml import etree

from agent.research_store import Conflict, now, safe_payload
from .dart_service import DartService
from .dart_protocol import DartFailure
from .extraction_service import ExtractionService
from .document_extractor import ExtractionFailure
from .general_company import create_collection_retry, create_refresh_run, disclosures_begin


REPORT_CODES = (("사업보고서", "11011"), ("반기보고서", "11012"),
                ("분기보고서", "11013"), ("3분기보고서", "11014"))


def declared_taxonomy_references(members, read_content):
    """XBRL 인스턴스가 지시한 엔티티 스키마의 직접 DART 참조만 선택한다."""
    names={item['name']:item for item in members}
    parser=etree.XMLParser(resolve_entities=False,no_network=True)
    instance=[]
    for item in members:
        if not item['name'].lower().endswith(('.xbrl','.xml')):continue
        raw=read_content(item['source_id'])
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():continue
        try:root=etree.fromstring(raw,parser)
        except etree.XMLSyntaxError:continue
        if root.tag=='{http://www.xbrl.org/2003/instance}xbrl':instance.append((item,root))
    if len(instance)!=1:return []
    item,root=instance[0]
    refs=root.findall('{http://www.xbrl.org/2003/linkbase}schemaRef')
    if len(refs)!=1:return []
    relative=refs[0].get('{http://www.w3.org/1999/xlink}href','')
    schema_name=str(PurePosixPath(item['name']).parent / relative)
    schema=names.get(schema_name)
    if not schema:return []
    raw=read_content(schema['source_id'])
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():return []
    try:doc=etree.fromstring(raw,parser)
    except etree.XMLSyntaxError:return []
    if doc.tag!='{http://www.w3.org/2001/XMLSchema}schema':return []
    values=[]
    for node in doc.findall('{http://www.w3.org/2001/XMLSchema}import'):
        uri=node.get('schemaLocation','')
        if uri.startswith(('http://dart.fss.or.kr/Resource/Taxonomy/ifrs/',
                           'https://dart.fss.or.kr/Resource/Taxonomy/ifrs/')):
            values.append((schema['source_id'],uri))
    return sorted(set(values))


def report_code(report_name):
    text = re.sub(r"^(?:\s*\[[^\]]+\])+\s*", "", str(report_name or ""))
    for label, code in REPORT_CODES:
        if text.startswith(label):
            return code
    return None


def report_year(report_name, receipt):
    """정기 보고서의 비교 기준연도는 접수연도가 아니라 보고서명에 표시된 기간이다."""
    match = re.search(r"\((20\d{2})\.\d{2}\)", str(report_name or ""))
    return int(match.group(1)) if match else int(receipt[:4])


def report_month(report_name):
    match = re.search(r"\(20\d{2}\.(\d{2})\)", str(report_name or ""))
    return int(match.group(1)) if match and 1 <= int(match.group(1)) <= 12 else None


def fiscal_month_status(identity, disclosures, cutoff):
    """기업개황 결산월을 같은 회사의 최근 사업보고서 표기와 대조한다."""
    raw = str(identity.get('company', {}).get('acc_mt') or '')
    if not re.fullmatch(r'0[1-9]|1[0-2]', raw):
        return {'status': 'unverified', 'reason': 'company_fiscal_month_missing_or_invalid', 'month': None}
    month = int(raw)
    months = {report_month(item.get('report_nm')) for item in disclosures
              if report_code(item.get('report_nm')) == '11011'
              and re.fullmatch(r'\d{14}', str(item.get('rcept_no', '')))
              and str(item['rcept_no'])[:8] <= cutoff.replace('-', '')
              and report_month(item.get('report_nm')) is not None}
    if months and months != {month}:
        return {'status': 'conflict', 'reason': 'company_and_annual_report_fiscal_month_conflict',
                'month': None, 'company_month': month, 'annual_report_months': sorted(months)}
    return {'status': 'supported' if months else 'identity_only', 'reason': None,
            'month': month, 'annual_report_months': sorted(months)}


def _latest_period_year(cutoff_date, month):
    end = date(cutoff_date.year, month, monthrange(cutoff_date.year, month)[1])
    return cutoff_date.year if end <= cutoff_date else cutoff_date.year - 1


def comparison_targets(cutoff, fiscal_end_month=12):
    """결산월별 최근 종료 연간 3개와 반기 2개를 API 조회 후보로 계산한다."""
    cutoff_date=date.fromisoformat(str(cutoff))
    if not 1 <= fiscal_end_month <= 12: raise ValueError('invalid_fiscal_end_month')
    latest_annual_year = _latest_period_year(cutoff_date, fiscal_end_month)
    half_month = (fiscal_end_month + 5) % 12 + 1
    latest_half_year = _latest_period_year(cutoff_date, half_month)
    return [(year, '11011') for year in range(latest_annual_year-2, latest_annual_year+1)] + [
        (latest_half_year-1, '11012'), (latest_half_year, '11012')]


def select_regular_reports(items, cutoff, fiscal_end_month=12):
    """비교 창의 3개 연간·직전/현재 반기만 고른다. 정정본은 접수일이 가장 늦은 것을 쓴다."""
    selected = {};targets=set(comparison_targets(cutoff, fiscal_end_month))
    half_month = (fiscal_end_month + 5) % 12 + 1
    for item in items:
        code = report_code(item.get("report_nm"))
        receipt = str(item.get("rcept_no", ""))
        if code not in {"11011", "11012"} or not re.fullmatch(r"\d{14}", receipt):
            continue
        if report_month(item.get('report_nm')) != (fiscal_end_month if code == '11011' else half_month):
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


def planned_reports(disclosures, cutoff, fiscal_end_month=12):
    """공시 목록 누락이 있어도 합의한 비교 기간의 재무 API 조회 계획은 유지한다."""
    discovered = {(item["year"], item["report_code"]): item
                  for item in select_regular_reports(disclosures, cutoff, fiscal_end_month)}
    required = comparison_targets(cutoff, fiscal_end_month)
    return [discovered.get((year, code), {"year": year, "report_code": code,
                                           "rcept_no": None, "report_nm": None, "rcept_dt": None})
            for year, code in required]


def changed_regular_reports(previous_receipts, disclosures, cutoff, fiscal_end_month=12):
    """같은 비교 창에서 새 접수번호(정정본 포함)만 반환한다."""
    known={str(value) for value in previous_receipts if value}
    return [item for item in planned_reports(disclosures, cutoff, fiscal_end_month)
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

    def _last_full_collection(self, run_id):
        """변경 없음 실행을 건너뛰고 마지막 수집 원문 실행을 찾는다."""
        seen=set()
        while run_id and run_id not in seen:
            seen.add(run_id)
            run=self.store.get_run(run_id)
            result=run['trigger'].get('collection_result',{})
            if result.get('target_reports') and result.get('next_phase') in {
                    'numeric_and_xbrl_validation','non_calendar_numeric_mapping_pending'}:
                return run
            run_id=run.get('previous_run_id')
        return None

    async def _all_disclosures(self, run_id, corp_code, cutoff):
        """DART 페이지 전체를 읽되, 각 호출은 기존 실행 예산을 그대로 소비한다."""
        begin = disclosures_begin(date.fromisoformat(cutoff))
        first=await self.dart.disclosures(run_id,corp_code,begin,cutoff,page_no=1,page_count=100,last_report="N")
        pages=[first]
        try:total_pages=int(first.get('total_page') or 1)
        except (TypeError,ValueError):raise ValueError('invalid_disclosure_pagination') from None
        if not 1<=total_pages<=1000:raise ValueError('invalid_disclosure_pagination')
        for page_no in range(2,total_pages+1):
            pages.append(await self.dart.disclosures(run_id,corp_code,begin,cutoff,
                                                      page_no=page_no,page_count=100,last_report="N"))
        return {'items':[item for page in pages for item in page['items']],
                'source_ids':[page['source_id'] for page in pages],
                'page_count':len(pages),'complete':len(pages)==total_pages}

    async def run(self, run_id):
        basis, company = self._begin(run_id)
        corp_code = company.get("corp_code")
        try:
            identity = await self.dart.company(run_id, corp_code)
            disclosed = await self._all_disclosures(run_id,corp_code,basis["public_cutoff"])
            fiscal = fiscal_month_status(identity, disclosed['items'], basis['public_cutoff'])
            if fiscal['month'] is None:
                result = {'identity_source_id':identity['source_id'],
                          'disclosures_source_ids':disclosed['source_ids'],
                          'disclosures_page_count':disclosed['page_count'],
                          'fiscal_period':fiscal, 'target_reports':[], 'financial_requests':0,
                          'archive_count':0, 'extracted_source_count':0,
                          'financial_verification':'not_performed',
                          'next_phase':'fiscal_month_review_required'}
                self._finish(run_id,result)
                return result
            targets = planned_reports(disclosed["items"],basis["public_cutoff"],fiscal['month'])
            financial_sources, archives, extracted, taxonomy_references = [], [], [], []
            for target in targets:
                receipts = {target["rcept_no"]} if target["rcept_no"] else set()
                for scope in ("OFS", "CFS"):
                    try:
                        outcome = await self.dart.financials(run_id, corp_code, target["year"], target["report_code"], scope)
                    except DartFailure as error:
                        if error.kind != 'no_data': raise
                        outcome = {'status':'no_data','reason':'official_financial_api_no_data',
                                   'usable':False,'financial_verification':'not_performed'}
                    financial_sources.append({"target": target, "scope": scope, "result": outcome})
                    if outcome.get("status") == "ok":
                        receipts.add(outcome["receipt_number"])
                for receipt in sorted(receipt for receipt in receipts if receipt):
                    try:
                        document = await self.dart.document(run_id, receipt)
                    except DartFailure as error:
                        if error.kind != 'no_data': raise
                        document = {'source_id':None,'members':[],'status':'no_data'}
                    try:
                        xbrl = await self.dart.xbrl(run_id, receipt, target["report_code"])
                    except DartFailure as error:
                        if error.kind != 'no_data': raise
                        xbrl = {'source_id':None,'members':[],'status':'no_data'}
                    archives.append({"target": target, "receipt_number": receipt,
                                     "document_source_id": document["source_id"], "xbrl_source_id": xbrl["source_id"],
                                     "document_status":document.get('status','ok'),
                                     "xbrl_status":xbrl.get('status','ok')})
                    if xbrl.get('members'):
                        for referring_source_id,uri in declared_taxonomy_references(
                                xbrl['members'],self.store.source_content):
                            try:
                                outcome=await self.dart.taxonomy_reference(run_id,uri,referring_source_id)
                                taxonomy_references.append({'status':'ok','source_id':outcome['source_id'],
                                    'declared_uri':uri,'referring_source_id':referring_source_id})
                            except DartFailure as error:
                                taxonomy_references.append({'status':'withheld','reason':error.kind,
                                    'declared_uri':uri,'referring_source_id':referring_source_id})
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
                      'fiscal_period':fiscal,
                      "target_reports": targets, "financial_requests": len(financial_sources),
                      "archive_count": sum(bool(item['document_source_id'] and item['xbrl_source_id'])
                                           for item in archives),
                      "unavailable_archive_parts": [item for item in archives if not
                          (item['document_source_id'] and item['xbrl_source_id'])],
                      "extracted_source_count": len(extracted),
                      "taxonomy_reference_count":sum(x['status']=='ok' for x in taxonomy_references),
                      "taxonomy_reference_withheld":sum(x['status']!='ok' for x in taxonomy_references),
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

    async def refresh(self, research_id, cutoff):
        """기존 조사를 열 때 최신·정정 공시만 먼저 확인한다.

        변동이 없으면 기존 게시본과 원문을 그대로 사용한다. 변동이 있으면 새 실행을
        만들고 전체 수집으로 이어져 이전 판의 근거를 바꾸지 않는다.
        """
        receipt=create_refresh_run(self.store, research_id, cutoff)
        if receipt['status']=='in_progress':
            return receipt
        run_id=receipt['run_id'];basis,company=self._begin(run_id)
        try:
            identity=await self.dart.company(run_id,company['corp_code'])
            disclosed=await self._all_disclosures(run_id,company['corp_code'],basis['public_cutoff'])
            fiscal=fiscal_month_status(identity,disclosed['items'],basis['public_cutoff'])
            if fiscal['month'] is None:
                result={'identity_source_id':identity['source_id'],'fiscal_period':fiscal,
                        'checked_disclosures_source_ids':disclosed['source_ids'],
                        'next_phase':'fiscal_month_review_required'}
                self._finish(run_id,result)
                return {**receipt,'status':'review_required','collection_result':result}
            previous_full=self._last_full_collection(receipt['previous_run_id'])
            previous_fiscal=(previous_full or {}).get('trigger',{}).get('collection_result',{}).get('fiscal_period')
            if previous_fiscal and previous_fiscal.get('month') not in (None,fiscal['month']):
                result={'identity_source_id':identity['source_id'],'fiscal_period':fiscal,
                        'previous_fiscal_period':previous_fiscal,
                        'checked_disclosures_source_ids':disclosed['source_ids'],
                        'next_phase':'fiscal_month_change_review_required'}
                self._finish(run_id,result)
                return {**receipt,'status':'review_required','collection_result':result}
            with self.store.connection() as conn:
                old_receipts={receipt_number for row in conn.execute(
                    'SELECT metadata_json FROM research_sources WHERE run_id=?',((previous_full or {}).get('id'),))
                    for receipt_number in [json.loads(row['metadata_json']).get('receipt_number')]
                    if receipt_number}
            changes=changed_regular_reports(old_receipts,disclosed['items'],basis['public_cutoff'],fiscal['month'])
            if not changes:
                result={'freshness':'no_new_or_corrected_regular_report','checked_disclosures_source_ids':disclosed['source_ids'],
                        'identity_source_id':identity['source_id'],'fiscal_period':fiscal,
                        'checked_report_count':len(planned_reports(disclosed['items'],basis['public_cutoff'],fiscal['month'])),
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
            if result.get('next_phase')!='numeric_and_xbrl_validation':
                return {**receipt,'status':'changed_validation_pending','changes':changes,'collection_result':result}
            from .numeric_validation import NumericValidationService
            from .xbrl_mapping import XbrlMappingService
            numeric=await asyncio.to_thread(NumericValidationService(self.store).validate_run,run_id)
            mapping=await asyncio.to_thread(XbrlMappingService(self.store).validate_run,run_id)
            return {**receipt,'status':'changed','changes':changes,'collection_result':result,
                    'numeric_batch_id':numeric['id'],'mapping_batch_id':mapping['id']}
        except Exception:
            with self.store.connection(write=True) as conn:
                run,basis_row=self.store._context(conn,run_id)
                trigger=json.loads(run['trigger_json'])
                if (run['status']=='finished' and
                        trigger.get('collection_result',{}).get('next_phase')=='numeric_and_xbrl_validation'):
                    trigger['phase']='validation_failed'
                    conn.execute("UPDATE research_runs SET trigger_json=? WHERE id=?",
                                 (safe_payload(trigger),run_id))
                    self.store._event(conn,basis_row['research_id'],basis_row['id'],run_id,
                                      'general_validation_failed',{'at':now()})
                else:
                    conn.execute("UPDATE research_runs SET status='failed' WHERE id=? AND status='running'",(run_id,))
                    self.store._event(conn,basis_row['research_id'],basis_row['id'],run_id,
                                      'general_freshness_check_failed',{'at':now()})
            raise
