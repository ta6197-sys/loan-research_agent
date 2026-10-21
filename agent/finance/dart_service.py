"""금융 MCP에서 사용하는 DART 도구와 기존 조사 저장소 연결.

실제 수집을 수행하지만 수치/문장 검증과 보고서 생성은 하지 않는다.
"""
import asyncio
from datetime import date
from email.utils import parsedate_to_datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import time
from uuid import uuid4
import xml.etree.ElementTree as ET

from agent.research_models import SourceInput, Usage
from agent.research_store import Conflict, IntegrityFailure, ResearchStore, encode, now
from .dart_protocol import DartFailure, DartHTTP, xml_root, zip_members


class DartService:
    def __init__(self, store, api_key, *, transport=None, sleep=asyncio.sleep):
        self.store = store
        # 조정 가능한 전송/압축 안전 한도. 초과 파일을 부분 자료로 채택하지 않는다.
        self.max_response_bytes = int(os.environ.get('DART_MAX_RESPONSE_BYTES', 100 * 1024 * 1024))
        self.max_expanded_bytes = int(os.environ.get('DART_MAX_EXPANDED_BYTES', 256 * 1024 * 1024))
        self.max_members = int(os.environ.get('DART_MAX_ARCHIVE_MEMBERS', 100))
        self.http = DartHTTP(api_key, max_response_bytes=self.max_response_bytes, transport=transport)
        self.sleep = sleep
        self.catalogue_root = store.source_root / '_catalogue'
        self.catalogue = None
        self.catalogue_updated_at = None
        self.catalogue_error = None
        self.refresh_attempted = False
        self.lock = asyncio.Lock()
        self._deadline = None

    def _run_context(self, run_id, corp_code=None):
        run = self.store.get_run(run_id)
        with self.store.connection() as conn:
            basis = dict(self.store._one(conn, 'research_bases', run['basis_id']))
            research_id = basis['research_id']
        research = self.store.get_research(research_id)
        if run['status'] in {'stopping', 'stopped', 'finished', 'finished_with_gaps', 'failed'}:
            raise DartFailure('run_not_available')
        if corp_code is not None:
            selected = research['company'].get('corp_code')
            if not selected:
                raise DartFailure('identity_selection_required')
            if selected != corp_code:
                raise DartFailure('company_mismatch')
        return run, json.loads(basis['data_json']), research

    def _snapshot(self, run_id, stage, update, *, reserve=False, elapsed=0):
        run, _, _ = self._run_context(run_id)
        usage = dict(run['usage'])
        if reserve:
            usage['external_requests'] += 1
        usage['execution_seconds'] = min(usage['execution_seconds'] + elapsed,
                                         run['budget']['execution_seconds'])
        if usage['external_requests'] > run['budget']['external_requests']:
            raise DartFailure('budget_exhausted')
        if reserve and usage['execution_seconds'] >= run['budget']['execution_seconds']:
            raise DartFailure('budget_exhausted')
        state = dict(run['snapshots'][-1]['state']) if run['snapshots'] else {}
        state.update(update)
        try:
            self.store.save_snapshot(run_id, expected_revision=run['revision'], stage=stage,
                                     state=state, usage=Usage(**usage))
        except Conflict:
            raise DartFailure('state_conflict') from None

    async def _request(self, operation, params, run_id=None):
        async def reserve():
            if run_id:
                self._snapshot(run_id, 'dart_request_reserved', {
                    'last_dart_attempt': {'id': uuid4().hex, 'operation': operation,
                                          'params': params, 'state': 'reserved_outcome_unknown'},
                }, reserve=True)
            else:
                # 조사 생성 전 회사 검색의 공용 목록 갱신. 조사 실행 예산과 분리해 기록한다.
                self.catalogue_root.mkdir(parents=True, exist_ok=True)
                record = {'at': now(), 'operation': operation, 'state': 'reserved_outcome_unknown'}
                with (self.catalogue_root / 'requests.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(encode(record) + '\n')
                    stream.flush()
                    os.fsync(stream.fileno())
        for attempt in range(3):
            try:
                remaining = (self._deadline - time.monotonic()) if self._deadline else 60
                if remaining <= 0:
                    raise DartFailure('budget_exhausted')
                async with asyncio.timeout(min(60, remaining)):
                    result = await self.http.request(operation, params, before_request=reserve,
                                                     timeout=min(60, remaining))
                if run_id:
                    self._snapshot(run_id, 'dart_response_received', {
                        'last_dart_attempt': {'operation': operation, 'state': 'response_received'},
                    })
                return result
            except (TimeoutError, DartFailure) as failure:
                error = failure if isinstance(failure, DartFailure) else DartFailure('network_error', retryable=True)
                if run_id:
                    self._snapshot(run_id, 'dart_request_result', {'last_dart_error': error.public()})
                if not error.retryable or attempt == 2:
                    raise error from None
                delay = (2, 5)[attempt]
                if error.retry_after:
                    try:
                        delay = max(delay, float(error.retry_after))
                    except ValueError:
                        try:
                            from datetime import datetime, timezone
                            delay = max(delay, (parsedate_to_datetime(error.retry_after) - datetime.now(timezone.utc)).total_seconds())
                        except (ValueError, TypeError):
                            raise DartFailure('provider_wait_required') from None
                if self._deadline and time.monotonic() + delay >= self._deadline:
                    raise DartFailure('provider_wait_required') from None
                await self.sleep(delay)

    def _save(self, run_id, operation, params, response, receipt=None):
        url = ('https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + receipt if receipt else
               'https://opendart.fss.or.kr/api/' + {'company':'company.json','disclosures':'list.json',
                                                 'financials':'fnlttSinglAcntAll.json'}[operation])
        published = date.fromisoformat(receipt[:8]).isoformat() if receipt else None
        return self.store.save_source(run_id, SourceInput(
            kind='dart', title='OpenDART ' + operation, publisher='금융감독원 OpenDART',
            url=url, receipt_number=receipt, published_at=published, retrieved_at=now(),
            reporting_period={'operation':operation, **params}, media_type=response.media_type,
            rights_status='public_disclosure'), response.content)

    @staticmethod
    def _atomic_write(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temp = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if temp:
                temp.unlink(missing_ok=True)

    def _read_catalogue(self, raw):
        members = zip_members(raw, max_expanded_bytes=self.max_expanded_bytes, max_members=self.max_members)
        if len(members) != 1 or not members[0][0].lower().endswith('.xml'):
            raise DartFailure('parse_error')
        content = members[0][1]
        if b'\x00' in content or b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
            raise DartFailure('unsafe_document')
        rows = []
        try:
            for _, element in ET.iterparse(io.BytesIO(content), events=('end',)):
                if element.tag == 'list':
                    row = {key:(element.findtext(key) or '').strip() for key in (
                        'corp_code','corp_name','stock_code','modify_date')}
                    if (not re.fullmatch(r'\d{8}',row['corp_code']) or not row['corp_name']
                            or (row['stock_code'] and not re.fullmatch(r'[0-9A-Z]{6}',row['stock_code']))):
                        raise DartFailure('parse_error')
                    rows.append(row)
                    element.clear()
        except ET.ParseError:
            raise DartFailure('parse_error') from None
        if not rows:
            raise DartFailure('parse_error')
        return rows

    async def search_companies(self, query):
        if not query or len(query) > 200:
            raise DartFailure('invalid_request')
        if not self.refresh_attempted:
            self.refresh_attempted = True
            try:
                response = await self._request('corporations', {})
                rows = self._read_catalogue(response.content)
                stamp = now()
                content_hash = hashlib.sha256(response.content).hexdigest()
                self._atomic_write(self.catalogue_root / (content_hash + '.zip'), response.content)
                self._atomic_write(self.catalogue_root / 'current.json', encode({
                    'content_hash':content_hash, 'updated_at':stamp}).encode())
                self.catalogue, self.catalogue_updated_at = rows, stamp
            except DartFailure as error:
                self.catalogue_error = error.public()
                try:
                    current = json.loads((self.catalogue_root / 'current.json').read_text())
                    content_hash = current['content_hash']
                    if not re.fullmatch(r'[a-f0-9]{64}',content_hash):
                        raise ValueError
                    raw = (self.catalogue_root / (content_hash + '.zip')).read_bytes()
                    if hashlib.sha256(raw).hexdigest() != content_hash:
                        raise ValueError
                    self.catalogue = self._read_catalogue(raw)
                    self.catalogue_updated_at = current['updated_at']
                except (OSError, ValueError, KeyError, DartFailure):
                    raise error from None
        if self.catalogue is None:
            raise DartFailure('catalogue_unavailable')
        matches = [row for row in self.catalogue if query in row['corp_name']
                   or query in {row['corp_code'],row['stock_code']}]
        return {'status':'ok', 'items':matches, 'catalogue_updated_at':self.catalogue_updated_at,
                'stale':bool(self.catalogue_error), 'refresh_error':self.catalogue_error,
                'support_status':'business_and_disclosures_not_checked',
                'scope_note':'목록은 현재 법인 검색용이며 과거 기준일 당시 명칭을 보장하지 않습니다.'}

    async def company(self, run_id, corp_code):
        _, _, research = self._run_context(run_id, corp_code)
        params = {'corp_code':corp_code}
        response = await self._request('company',params,run_id)
        data = response.data
        if data.get('corp_code') != corp_code:
            raise DartFailure('company_mismatch')
        stock = research['company'].get('stock_code')
        if stock and data.get('stock_code') != stock:
            raise DartFailure('company_mismatch')
        source = self._save(run_id,'company',params,response)
        return {'status':'ok','source_id':source['id'], 'company':{
            key:data.get(key) for key in ('corp_code','corp_name','stock_code','corp_cls','hm_url','ir_url','induty_code')},
            'support_status':'business_not_checked', 'financial_verification':'not_performed'}

    async def disclosures(self, run_id, corp_code, begin, end, page_no=1, page_count=100, last_report='N'):
        _, basis, _ = self._run_context(run_id,corp_code)
        if end > basis['public_cutoff']:
            raise DartFailure('outside_public_cutoff')
        params = {'corp_code':corp_code,'bgn_de':begin,'end_de':end,'page_no':page_no,
                  'page_count':page_count,'last_reprt_at':last_report}
        response = await self._request('disclosures',params,run_id)
        data = response.data
        if not isinstance(data.get('list'),list):
            raise DartFailure('parse_error')
        source = self._save(run_id,'disclosures',params,response)
        for row in data['list']:
            if row.get('corp_code') != corp_code:
                raise DartFailure('company_mismatch')
            if (not re.fullmatch(r'\d{8}',str(row.get('rcept_dt','')))
                    or not re.fullmatch(r'\d{14}',str(row.get('rcept_no','')))):
                raise DartFailure('parse_error')
            if date.fromisoformat(row['rcept_dt']).isoformat() > basis['public_cutoff']:
                raise DartFailure('outside_public_cutoff')
        return {'status':'ok','source_id':source['id'],'items':data['list'],
                'page_no':data.get('page_no'),'total_page':data.get('total_page'),
                'total_count':data.get('total_count'), 'complete':int(data.get('total_page',1)) <= page_no,
                'financial_verification':'not_performed'}

    async def financials(self, run_id, corp_code, year, report_code, scope):
        _, basis, _ = self._run_context(run_id,corp_code)
        params = {'corp_code':corp_code,'bsns_year':str(year),'reprt_code':report_code,'fs_div':scope}
        response = await self._request('financials',params,run_id)
        rows = response.data.get('list')
        if not isinstance(rows,list) or not rows:
            raise DartFailure('parse_error')
        receipts = {row.get('rcept_no') for row in rows}
        if len(receipts) != 1:
            raise DartFailure('receipt_mismatch')
        receipt = next(iter(receipts))
        if not isinstance(receipt,str) or not re.fullmatch(r'\d{14}',receipt):
            raise DartFailure('parse_error')
        source = self._save(run_id,'financials',params,response,receipt)
        if date.fromisoformat(receipt[:8]).isoformat() > basis['public_cutoff']:
            return {'status':'outside_public_cutoff','source_id':source['id'],'usable':False,
                    'receipt_number':receipt,'financial_verification':'not_performed'}
        if any(row.get('corp_code') != corp_code or row.get('bsns_year') != str(year)
               or row.get('reprt_code') != report_code for row in rows):
            raise DartFailure('financial_scope_mismatch')
        return {'status':'ok','source_id':source['id'],'receipt_number':receipt,
                'row_count':len(rows),'requested_scope':scope,'usable':None,
                'financial_verification':'not_performed',
                'limitation':'API 응답 확보만 완료. 원문 열/기간/단위/범위 대조는 수행 전입니다.'}

    def _receipt_known(self, run_id, receipt):
        run, basis, research = self._run_context(run_id)
        with self.store.connection() as conn:
            rows = conn.execute('SELECT s.* FROM research_sources s JOIN research_runs r ON s.run_id=r.id WHERE r.basis_id=?',
                                (run['basis_id'],)).fetchall()
        for source in rows:
            meta = json.loads(source['metadata_json'])
            op = meta['reporting_period'].get('operation')
            if op not in {'disclosures','financials'}:
                continue
            data = json.loads(self.store.source_content(source['id']))
            for entry in data.get('list',[]):
                if (entry.get('rcept_no') == receipt
                        and entry.get('corp_code') == research['company'].get('corp_code')):
                    return date.fromisoformat(receipt[:8]).isoformat() <= basis['public_cutoff']
        return False

    async def document(self, run_id, receipt):
        if not re.fullmatch(r'\d{14}',receipt) or not self._receipt_known(run_id,receipt):
            raise DartFailure('receipt_not_in_research')
        params = {'rcept_no':receipt}
        response = await self._request('document',params,run_id)
        members = zip_members(response.content,max_expanded_bytes=self.max_expanded_bytes,
                              max_members=self.max_members)
        source = self._save(run_id,'document',params,response,receipt)
        manifest = []
        for name,content in members:
            media_type = 'text/html' if re.search(rb'<html|<!doctype\s+html',content[:2048],re.I) else 'application/xml'
            member = self.store.save_source(run_id,SourceInput(
                kind='dart',title='공시 원문 ' + name,publisher='금융감독원 OpenDART',
                url='https://dart.fss.or.kr/dsaf001/main.do?rcpNo=' + receipt,
                receipt_number=receipt,published_at=date.fromisoformat(receipt[:8]).isoformat(),retrieved_at=now(),
                reporting_period={'operation':'document_member','archive_source_id':source['id'],'member_name':name},
                media_type=media_type,rights_status='public_disclosure'),content)
            manifest.append({'source_id':member['id'],'name':name,'content_hash':member['content_hash'],
                             'byte_size':member['byte_size'],'media_type':media_type})
        return {'status':'ok','source_id':source['id'],'members':manifest,'financial_verification':'not_performed'}

    async def call(self, name, arguments):
        # 금융 서비스 내부에서도 직렬화한다. MCP 클라이언트 잠금에만 의존하지 않는다.
        async with self.lock:
            run_id = arguments.get('run_id')
            started = time.monotonic()
            self._deadline = started + 1800
            try:
                if run_id:
                    run, _, _ = self._run_context(run_id)
                    remaining = run['budget']['execution_seconds'] - run['usage']['execution_seconds']
                    if remaining <= run['budget']['writing_reserve_seconds']:
                        raise DartFailure('writing_reserve_reached')
                    self._deadline = started + remaining - run['budget']['writing_reserve_seconds']
                functions = {'search_companies':self.search_companies,'company':self.company,
                             'disclosures':self.disclosures,'financials':self.financials,'document':self.document}
                if name not in functions:
                    raise DartFailure('invalid_request')
                return await functions[name](**arguments)
            except DartFailure as error:
                return error.public()
            except (ValueError, TypeError, KeyError):
                return {'status':'invalid_response_or_input','retryable':False}
            finally:
                if run_id:
                    elapsed = int(time.monotonic() - started + 0.999)
                    try:
                        self._snapshot(run_id,'dart_tool_finished',{'last_dart_tool':name},elapsed=elapsed)
                    except (DartFailure, ValueError):
                        pass
                self._deadline = None
