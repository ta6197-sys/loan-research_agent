"""원문을 수정하지 않고 텍스트 영역의 명백한 XML 특수문자만 이스케이프한다.

정의되지 않은 엔티티·잘못된 태그/속성은 추정 복구하지 않는다.
원본 위치 확인에는 변환한 바이트가 아닌 원본 RawLocations를 사용해야 한다.
"""
import hashlib
import re
from .source_locations import TOKENS

ENTITY = re.compile(rb'&(?:[A-Za-z_][\w:.-]*|#[0-9]+|#x[0-9a-fA-F]+);')


def escape_literal_text(content):
    parts = []; changes = []

    def append_text(start, end):
        segment = content[start:end]; previous = 0
        for match in re.finditer(rb'[&<]', segment):
            offset = match.start()
            if match.group() == b'&' and ENTITY.match(segment, offset):
                continue
            parts.append(segment[previous:offset])
            replacement = b'&amp;' if match.group() == b'&' else b'&lt;'
            parts.append(replacement); previous = offset + 1
            changes.append({'byte_start': start + offset, 'byte_end': start + offset + 1,
                            'replacement': replacement.decode('ascii'),
                            'reason': 'bare_ampersand' if match.group() == b'&' else 'literal_less_than'})
        parts.append(segment[previous:])

    previous = 0
    for token in TOKENS.finditer(content):
        append_text(previous, token.start())
        parts.append(token.group()); previous = token.end()
    append_text(previous, len(content))
    parsed_copy = b''.join(parts)
    return parsed_copy, {'status': 'candidate' if changes else 'not_applicable',
                         'original_sha256': hashlib.sha256(content).hexdigest(),
                         'parsing_copy_sha256': hashlib.sha256(parsed_copy).hexdigest(),
                         'changes': changes, 'original_modified': False}
