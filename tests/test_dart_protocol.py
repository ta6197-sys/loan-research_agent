"""공식 API 계약의 합성 응답 시험. DART/실제 키/금융 수치를 사용하지 않는다."""
import io
import logging
import unittest
import zipfile

import httpx

from agent.finance.dart_protocol import DartFailure, DartHTTP, xml_root, zip_members


class DartProtocolTests(unittest.IsolatedAsyncioTestCase):
    def client(self, callback):
        return DartHTTP('synthetic-only-key', max_response_bytes=1024,
                        transport=httpx.MockTransport(callback))

    async def test_reserves_before_request_and_preserves_exact_response(self):
        calls = []
        async def reserve():
            calls.append('reserved')
        def transport(request):
            self.assertEqual(['reserved'], calls)
            self.assertEqual('opendart.fss.or.kr', request.url.host)
            self.assertEqual('synthetic-only-key', request.url.params['crtfc_key'])
            return httpx.Response(200, content=b'{"status":"000","corp_code":"00129642"}')
        response = await self.client(transport).request('company', {'corp_code': '00129642'}, before_request=reserve)
        self.assertEqual(b'{"status":"000","corp_code":"00129642"}', response.content)

    async def test_classifies_documented_provider_errors_without_echoing_message(self):
        cases = {'010':'authentication_error','011':'authentication_error','012':'access_denied',
                 '013':'no_data','014':'file_not_found','020':'rate_limited','021':'invalid_request',
                 '100':'invalid_request','101':'access_denied','800':'provider_unavailable',
                 '900':'provider_error','901':'authentication_error','999':'provider_error'}
        async def reserve(): pass
        for code, kind in cases.items():
            with self.subTest(code=code):
                client = self.client(lambda req: httpx.Response(200, json={
                    'status': code, 'message': 'synthetic-only-key should never be returned'}))
                with self.assertRaises(DartFailure) as caught:
                    await client.request('company', {'corp_code':'00129642'}, before_request=reserve)
                self.assertEqual(kind, caught.exception.kind)
                self.assertNotIn('synthetic-only-key', str(caught.exception.public()))
                self.assertEqual(code == '800', caught.exception.retryable)

    async def test_rejects_input_before_network_and_reservation(self):
        async def reserve(): self.fail('invalid input must not reserve')
        client = self.client(lambda req: self.fail('invalid input must not request'))
        invalid = [('unknown',{}),('company',{'corp_code':'../bad'}),
                   ('company',{'corp_code':'00129642','url':'http://127.0.0.1'}),
                   ('document',{'rcept_no':'bad'}),
                   ('financials',{'corp_code':'00129642','bsns_year':'2026','reprt_code':'11012','fs_div':'OTHER'}),
                   ('disclosures',{'corp_code':'00129642','bgn_de':'2026-12-31','end_de':'2026-01-01',
                                   'last_reprt_at':'N','page_no':1,'page_count':100})]
        for op,params in invalid:
            with self.subTest(operation=op, params=params):
                with self.assertRaises(DartFailure):
                    await client.request(op,params,before_request=reserve)

    async def test_missing_key_does_not_make_request(self):
        async def reserve(): self.fail('missing key must not reserve')
        client = DartHTTP('',max_response_bytes=1024,
                          transport=httpx.MockTransport(lambda req: self.fail('must not request')))
        with self.assertRaises(DartFailure) as caught:
            await client.request('company', {'corp_code':'00129642'},before_request=reserve)
        self.assertEqual('missing_configuration',caught.exception.kind)

    async def test_no_redirect_and_response_size_limit(self):
        async def reserve(): pass
        for response, kind in [(httpx.Response(302,headers={'location':'http://127.0.0.1/private'}),'unexpected_redirect'),
                               (httpx.Response(200,content=b'x'*1025),'response_limit'),
                               (httpx.Response(200,content=b'invalid JSON'),'parse_error'),
                               (httpx.Response(200,json=[]),'parse_error')]:
            with self.subTest(kind=kind):
                with self.assertRaises(DartFailure) as caught:
                    await self.client(lambda req:response).request('company',{'corp_code':'00129642'},before_request=reserve)
                self.assertEqual(kind,caught.exception.kind)

    async def test_transport_exception_does_not_expose_authentication_url(self):
        async def reserve(): pass
        def failure(req):
            raise httpx.ConnectError('failed URL https://example.test?crtfc_key=synthetic-only-key',request=req)
        with self.assertRaises(DartFailure) as caught:
            await self.client(failure).request('company',{'corp_code':'00129642'},before_request=reserve)
        self.assertEqual('network_error',str(caught.exception))
        self.assertTrue(caught.exception.retryable)

    async def test_httpx_info_log_hides_key(self):
        async def reserve(): pass
        with self.assertLogs('httpx',logging.INFO) as logs:
            await self.client(lambda req:httpx.Response(200,json={'status':'000'})).request(
                'company',{'corp_code':'00129642'},before_request=reserve)
        self.assertNotIn('synthetic-only-key',' '.join(logs.output))

    async def test_xml_error_is_not_misread_as_zip(self):
        async def reserve(): pass
        with self.assertRaises(DartFailure) as caught:
            await self.client(lambda req:httpx.Response(200,content=b'<result><status>014</status></result>')).request(
                'document',{'rcept_no':'20260813000874'},before_request=reserve)
        self.assertEqual('file_not_found',caught.exception.kind)

    def test_zip_paths_size_crc_and_xml_entities(self):
        def archive(name, content):
            result = io.BytesIO()
            with zipfile.ZipFile(result,'w',zipfile.ZIP_DEFLATED) as stream:
                stream.writestr(name,content)
            return result.getvalue()
        valid = archive('report.xml',b'<report>original</report>')
        self.assertEqual([('report.xml',b'<report>original</report>')],
                         zip_members(valid,max_expanded_bytes=100,max_members=5))
        for path in ('../outside.xml','/outside.xml','folder\\outside.xml','C:outside.xml'):
            with self.subTest(path=path):
                with self.assertRaises(DartFailure):
                    zip_members(archive(path,b'x'),max_expanded_bytes=100,max_members=5)
        with self.assertRaises(DartFailure):
            zip_members(valid,max_expanded_bytes=1,max_members=5)
        with self.assertRaises(DartFailure):
            zip_members(b'PK\x03\x04broken',max_expanded_bytes=100,max_members=5)
        with self.assertRaises(DartFailure):
            xml_root(b'<!DOCTYPE x [<!ENTITY y "expanded">]><x>&y;</x>')
        with self.assertRaises(DartFailure):
            xml_root('<!DOCTYPE x [<!ENTITY y "expanded">]><x>&y;</x>'.encode('utf-16'))

    async def test_success_response_echoing_key_is_not_returned(self):
        async def reserve(): pass
        with self.assertRaises(DartFailure) as caught:
            await self.client(lambda req:httpx.Response(200,json={
                'status':'000','unexpected':'synthetic-only-key'})).request(
                    'company',{'corp_code':'00129642'},before_request=reserve)
        self.assertEqual('sensitive_response',caught.exception.kind)

    async def test_malformed_provider_code_does_not_echo_arbitrary_text(self):
        async def reserve(): pass
        with self.assertRaises(DartFailure) as caught:
            await self.client(lambda req:httpx.Response(200,json={
                'status':'synthetic-only-key'})).request(
                    'company',{'corp_code':'00129642'},before_request=reserve)
        self.assertEqual('parse_error',caught.exception.kind)
        self.assertNotIn('synthetic-only-key',str(caught.exception.public()))


if __name__ == '__main__':
    unittest.main()
