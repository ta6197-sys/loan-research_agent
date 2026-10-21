"""합성 응답으로 XBRL/택사노미 전송·예산·보존 검사. 실제 금융 검증과 구분한다."""
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
import httpx

from agent.finance.dart_protocol import DartFailure, DartHTTP
from agent.finance.dart_service import DartService
from agent.research_models import ResearchInput, SourceInput, Budget
from agent.research_store import ResearchStore, now


class XbrlCollectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p=Path(self.tmp.name)
        self.store=ResearchStore(p/'session.db',p/'sources')
        r=self.store.create_research(ResearchInput(company={'name':'합성','corp_code':'00129642'},
            purpose='합성 XBRL 시험',public_cutoff='2026-10-20',comparison_window={},
            request_key='test',budget=Budget(external_requests=2)))
        self.run=r['runs'][0]['id']
        data={'response':{'status':'000','list':[{'rcept_no':'20260318001187',
              'corp_code':'00129642','reprt_code':'11011'}]}}
        self.store.save_source(self.run,SourceInput(kind='synthetic',title='합성 접수 근거',
            publisher='test',retrieved_at=now(),rights_status='synthetic',
            reporting_period={'operation':'financials'},media_type='application/json'),
            json.dumps(data).encode())

    def service(self,handler):
        return DartService(self.store,'synthetic-only-key',transport=httpx.MockTransport(handler))

    async def test_archive_preserves_members_and_separates_document_operation(self):
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as z:
            z.writestr('report.xbrl',b'<xbrl/>')
            z.writestr('company.xsd',b'<schema/>')
        service=self.service(lambda req:httpx.Response(200,content=output.getvalue()))
        result=await service.call('xbrl',dict(run_id=self.run,receipt='20260318001187',report_code='11011'))
        self.assertEqual('ok',result['status'])
        self.assertEqual(2,len(result['members']))
        self.assertEqual(output.getvalue(),self.store.source_content(result['source_id']))
        for member in result['members']:
            meta=self.store.get_source(member['source_id'])['metadata']
            self.assertEqual('xbrl_member',meta['reporting_period']['operation'])
            self.assertFalse(meta['allow_external_model'])

    async def test_receipt_and_report_code_mismatch_prevent_request(self):
        service=self.service(lambda req:self.fail('must not request'))
        for receipt,code in [('20260813000874','11012'),('20260318001187','11012')]:
            result=await service.call('xbrl',dict(run_id=self.run,receipt=receipt,report_code=code))
            self.assertEqual('receipt_not_in_research',result['status'])
        self.assertEqual(0,self.store.get_run(self.run)['usage']['external_requests'])

    async def test_taxonomy_keeps_original_and_does_not_claim_historical_validation(self):
        payload={'status':'000','list':[{'sj_div':'BS1','account_id':'ifrs-full_Assets',
                 'bsns_de':'2025-01-01','label_kor':'합성 자산','ifrs_ref':'synthetic'}]}
        result=await self.service(lambda req:httpx.Response(200,json=payload)).call(
            'taxonomy',dict(run_id=self.run,statement='BS1'))
        self.assertEqual('ok',result['status'])
        self.assertEqual(payload,json.loads(self.store.source_content(result['source_id'])))
        self.assertEqual('not_performed',result['financial_verification'])

    async def test_malformed_or_wrong_statement_dictionary_is_not_adopted(self):
        for rows in [[],[{'sj_div':'BS2','account_id':'a'}],[{'sj_div':'BS1'}]]:
            self.setUp()
            result=await self.service(lambda req:httpx.Response(200,json={'status':'000','list':rows})).call(
                'taxonomy',dict(run_id=self.run,statement='BS1'))
            self.assertEqual('parse_error',result['status'])

    async def test_budget_is_durable_and_blocks_third_request(self):
        service=self.service(lambda req:httpx.Response(200,json={
            'status':'000','list':[{'sj_div':'BS1','account_id':'a'}]}))
        for _ in range(2):
            self.assertEqual('ok',(await service.call('taxonomy',dict(run_id=self.run,statement='BS1')))['status'])
        restarted=ResearchStore(self.store.db_path,self.store.source_root)
        blocked=DartService(restarted,'synthetic-only-key',transport=httpx.MockTransport(lambda req:self.fail('budget')))
        result=await blocked.call('taxonomy',dict(run_id=self.run,statement='BS1'))
        self.assertEqual('budget_exhausted',result['status'])
        self.assertEqual(2,restarted.get_run(self.run)['usage']['external_requests'])

    async def test_invalid_arguments_and_redirect_do_not_leak_key(self):
        async def reserve():pass
        http=DartHTTP('synthetic-only-key',max_response_bytes=1000,
            transport=httpx.MockTransport(lambda req:httpx.Response(302,headers={'location':'https://elsewhere.invalid'})))
        for op,params in [('taxonomy',{'sj_div':'WRONG'}),('xbrl',{'rcept_no':'20260318001187','reprt_code':'BAD'})]:
            with self.assertRaises(DartFailure) as caught:
                await http.request(op,params,before_request=reserve)
            self.assertEqual('invalid_request',caught.exception.kind)
        with self.assertRaises(DartFailure) as caught:
            await http.request('taxonomy',{'sj_div':'BS1'},before_request=reserve)
        self.assertEqual('unexpected_redirect',caught.exception.kind)

    async def test_zip_endpoint_xml_error_is_classified(self):
        async def reserve():pass
        http=DartHTTP('synthetic-only-key',max_response_bytes=1000,
            transport=httpx.MockTransport(lambda req:httpx.Response(200,content=b'<result><status>014</status></result>')))
        with self.assertRaises(DartFailure) as caught:
            await http.request('xbrl',{'rcept_no':'20260318001187','reprt_code':'11011'},before_request=reserve)
        self.assertEqual('file_not_found',caught.exception.kind)

    async def test_reference_has_no_key_and_external_host_is_rejected_before_network(self):
        uri='http://dart.fss.or.kr/Resource/Taxonomy/ifrs/2024-06-30/test.xsd'
        raw=('<schema xmlns="http://www.w3.org/2001/XMLSchema"><import schemaLocation="'+uri+'"/></schema>').encode()
        source=self.store.save_source(self.run,SourceInput(kind='synthetic',title='합성 참조 선언',
            publisher='test',retrieved_at=now(),rights_status='synthetic',
            reporting_period={'operation':'xbrl_member'},media_type='application/xml'),raw)
        def handler(req):
            self.assertEqual('https',req.url.scheme)
            self.assertEqual('dart.fss.or.kr',req.url.host)
            self.assertNotIn('crtfc_key',req.url.params)
            self.assertNotIn('synthetic-only-key',str(req.headers))
            return httpx.Response(200,content=b'<schema xmlns="http://www.w3.org/2001/XMLSchema" targetNamespace="urn:synthetic"/>')
        service=self.service(handler)
        args={'run_id':self.run,'referring_source_id':source['id']}
        rejected=await service.call('taxonomy_reference',dict(args,url='https://example.invalid/test.xsd'))
        self.assertEqual('invalid_reference',rejected['status'])
        self.assertEqual(0,self.store.get_run(self.run)['usage']['external_requests'])
        accepted=await service.call('taxonomy_reference',dict(args,url=uri))
        self.assertEqual('ok',accepted['status'])
        self.assertEqual(uri,accepted['declared_uri'])
        self.assertEqual(1,self.store.get_run(self.run)['usage']['external_requests'])

    async def test_reference_redirect_counts_each_attempt_and_does_not_leave_official_host(self):
        uri='https://dart.fss.or.kr/Resource/Taxonomy/ifrs/2024-06-30/test.xsd'
        raw=('<schema xmlns="http://www.w3.org/2001/XMLSchema"><import schemaLocation="'+uri+'"/></schema>').encode()
        source=self.store.save_source(self.run,SourceInput(kind='synthetic',title='합성 참조',
            publisher='test',retrieved_at=now(),rights_status='synthetic',
            reporting_period={'operation':'xbrl_member'},media_type='application/xml'),raw)
        calls=[]
        def handler(req):
            calls.append(str(req.url))
            return httpx.Response(302,headers={'location':str(req.url)+'-redirected'})
        result=await self.service(handler).call('taxonomy_reference',dict(run_id=self.run,url=uri,referring_source_id=source['id']))
        self.assertEqual('budget_exhausted',result['status'])
        self.assertEqual(2,len(calls))
