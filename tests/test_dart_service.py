"""금융 도구-저장소 연결의 합성 시험. 실제 금융 정확성 평가는 아니다."""
from contextlib import closing
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import httpx
from fastapi.testclient import TestClient

from agent.finance.dart_service import DartService
from agent.research_models import ResearchInput
from agent.research_store import ResearchStore


def zip_bytes(content, name='report.xml'):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name,content)
    return stream.getvalue()


class DartServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)
        self.store=ResearchStore(self.path/'session.db',self.path/'sources')
        self.research=self.store.create_research(ResearchInput(
            company={'name':'합성 제조기업','corp_code':'00129642','stock_code':'041650'},
            purpose='합성 자료 시험',public_cutoff='2026-10-20',comparison_window={'years':[2025,2026]},
            request_key='dart-test'))
        self.run_id=self.research['runs'][0]['id']
        self.requests=[]
        self.delays=[]
        async def sleep(delay):self.delays.append(delay)
        self.fake_sleep=sleep

    def make_service(self, handler):
        def transport(req):
            self.requests.append(req)
            return handler(req)
        return DartService(self.store,'synthetic-key',transport=httpx.MockTransport(transport),sleep=self.fake_sleep)

    def args(self, **extra):
        return {'run_id':self.run_id,'corp_code':'00129642',**extra}

    def financial(self, receipt='20260813000874'):
        return {'status':'000','list':[{'rcept_no':receipt,'corp_code':'00129642',
            'bsns_year':'2026','reprt_code':'11012','account_nm':'합성 현금',
            'thstrm_amount':'9007199254740993.123456789'}]}

    async def test_profile_and_original_zip_are_saved_with_reserved_usage(self):
        original=b'<REPORT><TABLE>synthetic only</TABLE></REPORT>'
        def handler(req):
            if req.url.path.endswith('company.json'):
                return httpx.Response(200,json={'status':'000','corp_code':'00129642','stock_code':'041650','corp_name':'합성 제조기업','acc_mt':'12'})
            if req.url.path.endswith('fnlttSinglAcntAll.json'):
                return httpx.Response(200,json=self.financial())
            return httpx.Response(200,content=zip_bytes(original))
        service=self.make_service(handler)
        company=await service.call('company',self.args())
        financial=await service.call('financials',self.args(year=2026,report_code='11012',scope='OFS'))
        document=await service.call('document',{'run_id':self.run_id,'receipt':'20260813000874'})
        self.assertEqual(['ok']*3,[result['status'] for result in (company,financial,document)])
        self.assertEqual('12',company['company']['acc_mt'])
        self.assertEqual(original,self.store.source_content(document['members'][0]['source_id']))
        self.assertEqual(3,self.store.get_run(self.run_id)['usage']['external_requests'])
        self.assertEqual('not_performed',financial['financial_verification'])
        raw=json.loads(self.store.source_content(financial['source_id']))
        self.assertEqual('9007199254740993.123456789',raw['list'][0]['thstrm_amount'])
        self.assertNotIn('crtfc_key',str(self.store.get_research(self.research['id'])))

    async def test_temporary_failures_retry_with_all_attempts_counted(self):
        def failure(req):raise httpx.ConnectError('synthetic-key',request=req)
        service=self.make_service(failure)
        result=await service.call('company',self.args())
        self.assertEqual('network_error',result['status'])
        self.assertEqual([2,5],self.delays)
        self.assertEqual(3,len(self.requests))
        self.assertEqual(3,self.store.get_run(self.run_id)['usage']['external_requests'])

    async def test_authentication_no_data_and_limit_do_not_retry(self):
        for code,status in [('010','authentication_error'),('013','no_data'),('020','rate_limited')]:
            with self.subTest(code=code):
                self.requests.clear()
                result=await self.make_service(lambda req:httpx.Response(200,json={'status':code})).call('company',self.args())
                self.assertEqual(status,result['status'])
                self.assertEqual(1,len(self.requests))

    async def test_public_cutoff_and_company_constraints(self):
        service=self.make_service(lambda req:self.fail('must not request'))
        mismatch=await service.call('company',self.args(corp_code='00000001'))
        self.assertEqual('company_mismatch',mismatch['status'])
        future=await service.call('disclosures',self.args(begin='2026-01-01',end='2026-10-21'))
        self.assertEqual('outside_public_cutoff',future['status'])
        arbitrary=await service.call('document',{'run_id':self.run_id,'receipt':'20260813000874'})
        self.assertEqual('receipt_not_in_research',arbitrary['status'])
        self.assertEqual([],self.requests)

    async def test_api_returned_future_correction_is_retained_but_not_usable(self):
        service=self.make_service(lambda req:httpx.Response(200,json=self.financial('20261021000001')))
        result=await service.call('financials',self.args(year=2026,report_code='11012',scope='OFS'))
        self.assertEqual('outside_public_cutoff',result['status'])
        self.assertFalse(result['usable'])
        self.assertTrue(self.store.source_content(result['source_id']))
        doc=await service.call('document',{'run_id':self.run_id,'receipt':'20261021000001'})
        self.assertEqual('receipt_not_in_research',doc['status'])

    async def test_disclosure_page_does_not_claim_all_pages_complete(self):
        data={'status':'000','list':[{'corp_code':'00129642','rcept_no':'20260813000874',
                'rcept_dt':'20260813','report_nm':'합성 반기보고서'}],
              'page_no':1,'total_page':2,'total_count':101}
        service=self.make_service(lambda req:httpx.Response(200,json=data))
        result=await service.call('disclosures',self.args(begin='2026-01-01',end='2026-10-20'))
        self.assertFalse(result['complete'])
        self.assertEqual(1,len(self.requests))
        self.assertEqual('20260101',self.requests[0].url.params['bgn_de'])
        source=self.store.get_source(result['source_id'])
        self.assertEqual('N',source['metadata']['reporting_period']['last_reprt_at'])

    async def test_catalogue_refresh_once_and_stale_cache_is_visible(self):
        content=b'<result><list><corp_code>00129642</corp_code><corp_name>synthetic</corp_name><stock_code>041650</stock_code><modify_date>20261020</modify_date></list><list><corp_code>01933981</corp_code><corp_name>alphanumeric</corp_name><stock_code>0068Y0</stock_code><modify_date>20251121</modify_date></list></result>'
        service=self.make_service(lambda req:httpx.Response(200,content=zip_bytes(content,'CORPCODE.xml')))
        first=await service.call('search_companies',{'query':'synthetic'})
        second=await service.call('search_companies',{'query':'041650'})
        self.assertFalse(first['stale'])
        self.assertEqual(first['items'],second['items'])
        self.assertEqual(1,len(self.requests))
        alpha=await service.call('search_companies',{'query':'0068Y0'})
        self.assertEqual('0068Y0',alpha['items'][0]['stock_code'])
        # ZIP API의 오류는 XML이므로 실제 계약에 맞는 오류를 사용한다.
        failed=self.make_service(lambda req:httpx.Response(200,content=b'<result><status>010</status></result>'))
        stale=await failed.call('search_companies',{'query':'synthetic'})
        self.assertEqual('ok',stale['status'])
        self.assertTrue(stale['stale'])
        self.assertEqual(first['catalogue_updated_at'],stale['catalogue_updated_at'])
        self.assertEqual('authentication_error',stale['refresh_error']['status'])

    async def test_exhausted_budget_prevents_network_after_restart(self):
        with self.store.connection(write=True) as conn:
            usage=json.loads(conn.execute('SELECT usage_json FROM research_runs WHERE id=?',(self.run_id,)).fetchone()[0])
            usage['external_requests']=60
            conn.execute('UPDATE research_runs SET usage_json=? WHERE id=?',(json.dumps(usage),self.run_id))
        service=self.make_service(lambda req:self.fail('budget must prevent requests'))
        result=await service.call('company',self.args())
        self.assertEqual('budget_exhausted',result['status'])
        self.assertEqual([],self.requests)


class FinanceMCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_registration_and_safe_missing_key(self):
        from agent.finance.runtime import create_finance_client
        from agent.finance.tool_policy import FINANCE_TOOL_INPUTS
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'OPENDART_API_KEY':''}):
            client=create_finance_client(Path(tmp)/'session.db',Path(tmp)/'sources')
            await client.start()
            try:
                self.assertEqual({'finance'},set(client.sessions))
                tools=await client.sessions['finance'].list_tools()
                self.assertEqual({'search_companies','dart_company','dart_disclosures','dart_financials','dart_document',
                                  'extract_source','search_source','read_fragment','validate_numbers',
                                  'dart_xbrl','dart_taxonomy','validate_account_mapping'},
                                 {tool.name for tool in tools.tools})
                self.assertEqual(set(FINANCE_TOOL_INPUTS), {tool.name for tool in tools.tools})
                result=await client.call_finance('search_companies',{'query':'synthetic'})
                self.assertEqual('missing_configuration',result['status'])
            finally:
                await client.stop()


class FinanceStartupTests(unittest.TestCase):
    def test_real_mcp_process_exit_preserves_storage_api(self):
        from agent.mcp_client import MCPClient
        import server
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{
            'APP_MODE':'research','APP_DB_PATH':str(Path(tmp)/'session.db'),'APP_SOURCE_ROOT':str(Path(tmp)/'sources')}):
            script=Path(tmp)/'exiting_mcp.py'
            script.write_text('pass\n')
            failed=MCPClient(server_scripts={'finance':script})
            with patch.object(server,'create_finance_client',return_value=failed):
                with TestClient(server.app,base_url='http://127.0.0.1:8100') as client:
                    self.assertEqual('unavailable',client.get('/health').json()['finance_mcp'])
                    self.assertEqual(200,client.get('/researches').status_code)

    def test_mcp_failure_preserves_storage_api(self):
        from unittest.mock import AsyncMock
        import server
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{
            'APP_MODE':'research','APP_DB_PATH':str(Path(tmp)/'session.db'),'APP_SOURCE_ROOT':str(Path(tmp)/'sources')}):
            failed=AsyncMock()
            failed.start.side_effect=RuntimeError('synthetic startup failure')
            with patch.object(server,'create_finance_client',return_value=failed):
                with TestClient(server.app,base_url='http://127.0.0.1:8100') as client:
                    self.assertEqual('unavailable',client.get('/health').json()['finance_mcp'])
                    self.assertEqual(200,client.get('/researches').status_code)
                    self.assertEqual(503,client.get('/companies?query=test').status_code)
