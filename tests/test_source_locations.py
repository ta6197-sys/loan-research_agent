"""원문 위치 인덱스의 독립 시험. 숫자 의미 검증과 구분한다."""
import hashlib
import unittest

from agent.finance.source_locations import RawLocations, raw_text


class SourceLocationTests(unittest.TestCase):
    def test_original_utf8_bytes_and_repeated_values_remain_distinguishable(self):
        content='<TABLE>\n<TR><TD>현금</TD><TE>1,000</TE><TE>1,000</TE></TR>\n</TABLE>'.encode()
        index=RawLocations(content)
        cells=[i for i,e in enumerate(index.elements) if e.tag=='te']
        self.assertEqual(2,len(cells))
        left,right=(index.location(i) for i in cells)
        self.assertNotEqual(left['byte_start'],right['byte_start'])
        self.assertTrue(left['raw_span_verified'])
        self.assertEqual(2,left['line'])
        self.assertEqual(b'<TE>1,000</TE>',content[left['byte_start']:left['byte_end']])
        self.assertEqual(hashlib.sha256(index.raw(cells[0])).hexdigest(),left['raw_span_sha256'])

    def test_missing_end_tag_does_not_get_a_fabricated_verified_span(self):
        index=RawLocations(b'<TABLE><TR><TD>broken</TR></TABLE>')
        cell=next(i for i,e in enumerate(index.elements) if e.tag=='td')
        location=index.location(cell)
        self.assertIsNone(location['byte_end'])
        self.assertFalse(location['raw_span_verified'])
        self.assertIn('raw_missing_closing_tag',location['raw_span_warnings'])

    def test_greater_than_in_attribute_comments_and_cdata_do_not_shift_cells(self):
        content=b'<TABLE><!-- <TD>comment</TD> --><TR><TD label="a > b">0</TD></TR></TABLE>'
        index=RawLocations(content)
        cells=[i for i,e in enumerate(index.elements) if e.tag=='td']
        self.assertEqual(1,len(cells))
        self.assertEqual(b'0',index.inner(cells[0]))
        self.assertTrue(index.location(cells[0])['raw_span_verified'])

    def test_html_void_tags_and_display_text(self):
        index=RawLocations(b'<table><col><tr><td>0<br>1</td></tr></table>',html=True)
        cell=next(i for i,e in enumerate(index.elements) if e.tag=='td')
        self.assertTrue(index.location(cell)['raw_span_verified'])
        self.assertEqual('0 1',raw_text(index.inner(cell)))
        self.assertEqual('1 & 2',raw_text(b'<script>not executed</script>1 &amp; 2'))
