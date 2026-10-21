"""직접 만든 원문의 제한적 문자 보완. 실자료 금융 검증과 구별한다."""
import hashlib
import unittest
from agent.finance.document_extractor import DocumentExtractor, ExtractionFailure
from agent.finance.literal_xml import escape_literal_text


class LiteralXMLTests(unittest.TestCase):
    def test_address_and_literal_brackets_preserve_original_text_and_byte_positions(self):
        content='<DOCUMENT>\n<P>원문</P>\n<TABLE><TR><TE>TK&Dist</TE><TE>&lt;표시&gt;</TE><TE><보수현황></TE></TR></TABLE></DOCUMENT>'.encode()
        before=hashlib.sha256(content).hexdigest()
        result=DocumentExtractor(content).extract();table=result['tables'][0]
        self.assertEqual('xml_literal_escape_strict',result['parser_kind'])
        self.assertEqual('strict_copy_parsed',result['literal_repair']['status'])
        self.assertTrue(result['recovered']);self.assertTrue(table['location']['parser_text_matches_raw'])
        cells=table['rows'][0]['cells'];self.assertEqual(['TK&Dist','<표시>','<보수현황>'],[c['text'] for c in cells])
        for cell in cells:
            loc=cell['location'];self.assertTrue(loc['raw_span_verified']);self.assertTrue(loc['parser_text_matches_raw'])
            self.assertEqual(loc['raw_span_sha256'],hashlib.sha256(content[loc['byte_start']:loc['byte_end']]).hexdigest())
        self.assertEqual(before,hashlib.sha256(content).hexdigest())
        self.assertEqual(2,len(result['literal_repair']['changes']))

    def test_unknown_entity_and_invalid_markup_are_not_guessed(self):
        content=b'<DOCUMENT><TABLE><TR><TE>100 &undefined; 200</TE></TR></TABLE></DOCUMENT>'
        copy,report=escape_literal_text(content)
        self.assertEqual(content,copy);self.assertFalse(report['changes'])
        result=DocumentExtractor(content).extract()
        self.assertNotEqual('xml_literal_escape_strict',result['parser_kind'])
        self.assertIn('&undefined;',result['tables'][0]['rows'][0]['cells'][0]['text'])
        invalid=b'<DOCUMENT><TABLE><TR><TE>TK&Dist</TR></TABLE></DOCUMENT>'
        self.assertEqual('strict_copy_failed_not_adopted',DocumentExtractor(invalid).extract()['literal_repair']['status'])

    def test_comments_cdata_attributes_and_existing_entities_are_not_rewritten(self):
        content=b'<DOCUMENT A="a&amp;b"><!--x&y--><![CDATA[x&y < z]]><P>&amp; &#38; &#x26;</P></DOCUMENT>'
        copy,report=escape_literal_text(content)
        self.assertEqual(content,copy);self.assertFalse(report['changes'])
        self.assertEqual('xml_strict',DocumentExtractor(content).extract()['parser_kind'])

    def test_recovery_disabled_still_rejects_and_entities_remain_forbidden(self):
        with self.assertRaises(ExtractionFailure):DocumentExtractor(b'<DOCUMENT><P>A&B</P></DOCUMENT>',recover=False)
        with self.assertRaises(ExtractionFailure):DocumentExtractor(b'<!DOCTYPE DOCUMENT [<!ENTITY x "1">]><DOCUMENT>&x;</DOCUMENT>')
