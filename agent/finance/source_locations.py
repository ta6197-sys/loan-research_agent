"""수정하지 않은 원문 바이트의 요소 위치 인덱스. 파서의 재직렬화 결과와 구분한다."""
from bisect import bisect_right
from dataclasses import dataclass, field
import hashlib
from html.parser import HTMLParser
import re


TOKENS = re.compile(
    rb'<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|<\?[\s\S]*?\?>|'
    rb'</?[A-Za-z_][\w:.-]*(?:[^<>"\']|"[^"]*"|\'[^\']*\')*>'
)
TAG = re.compile(rb'</?([A-Za-z_][\w:.-]*)')
VOID_HTML_TAGS = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}


@dataclass
class RawElement:
    tag: str
    start: int
    opening_end: int
    parent: int | None
    end: int | None = None
    closing_start: int | None = None
    warnings: list[str] = field(default_factory=list)


class RawLocations:
    def __init__(self, content: bytes, *, html=False):
        self.content = content
        self.elements = []
        self.by_line_tag = {}
        self.line_starts = [0] + [match.end() for match in re.finditer(b'\n',content)]
        stack = []
        for match in TOKENS.finditer(content):
            token = match.group()
            name = TAG.match(token)
            if name is None:
                continue
            tag = name.group(1).decode('ascii').lower()
            if token.startswith(b'</'):
                position = next((i for i in range(len(stack)-1,-1,-1)
                                 if self.elements[stack[i]].tag == tag),None)
                if position is None:
                    continue
                for abandoned in stack[position+1:]:
                    self.elements[abandoned].warnings.append('raw_missing_closing_tag')
                element = self.elements[stack[position]]
                element.closing_start, element.end = match.start(), match.end()
                if position != len(stack)-1:
                    element.warnings.append('raw_non_nested_children')
                del stack[position:]
            else:
                element = RawElement(tag,match.start(),match.end(),stack[-1] if stack else None)
                index = len(self.elements)
                self.elements.append(element)
                line = self.position(match.start())[0]
                self.by_line_tag.setdefault((line,tag),[]).append(index)
                if re.search(rb'/\s*>$',token) or (html and tag in VOID_HTML_TAGS):
                    element.end, element.closing_start = match.end(), match.end()
                else:
                    stack.append(index)
        for index in stack:
            self.elements[index].warnings.append('raw_missing_closing_tag')

    def position(self, offset):
        line_index = bisect_right(self.line_starts,offset)-1
        return line_index+1, offset-self.line_starts[line_index]+1

    def location(self, index):
        element = self.elements[index]
        line, byte_column = self.position(element.start)
        verified = element.end is not None and not element.warnings
        return {
            'byte_start':element.start,'byte_end':element.end,
            'line':line,'byte_column':byte_column,'tag':element.tag,
            'raw_span_sha256':None if element.end is None else hashlib.sha256(
                self.content[element.start:element.end]).hexdigest(),
            'raw_span_verified':verified,'raw_span_warnings':list(element.warnings),
        }

    def raw(self, index):
        element = self.elements[index]
        if element.end is None:
            return None
        return self.content[element.start:element.end]

    def inner(self, index):
        element = self.elements[index]
        if element.closing_start is None:
            return None
        return self.content[element.opening_end:element.closing_start]


class _TextReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {'script','style'}:
            self.hidden += 1
        elif tag.lower() in {'br','p','div','table','tr','td','th','te','tu'} and not self.hidden:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag.lower() in {'script','style'}:
            self.hidden = max(0,self.hidden-1)
        elif tag.lower() in {'p','div','table','tr','td','th','te','tu'} and not self.hidden:
            self.parts.append('\n')

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def raw_text(content: bytes):
    """UTF-8 원문의 표시 텍스트. 원문 바이트 자체는 별도로 보존한다."""
    reader = _TextReader()
    reader.feed(content.decode('utf-8',errors='strict'))
    reader.close()
    return re.sub(r'\s+',' ',''.join(reader.parts)).strip()
