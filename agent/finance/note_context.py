"""승인된 원문 절 구조와 잔액형 주석의 기준일 연결. 값 일치만으로 범위를 추정하지 않는다."""
from collections import defaultdict
import re
from .document_extractor import normalize_label
from .source_locations import RawLocations, raw_text

VERSION = 'note-context-1.0'


class NoteContext:
    def __init__(self, source, content, identity, facts, supported, raw_check):
        self.content=content;self.identity=identity;self.raw_check=raw_check
        self.index=RawLocations(content);self.children=defaultdict(list)
        for i,e in enumerate(self.index.elements):self.children[e.parent].append(i)
        self.anchors=[f for f in facts if f['source']['id']==source['id'] and f['id'] in supported
                      and f.get('fragment',{}).get('data',{}).get('context',{}).get('scope_basis','').startswith('{XBRL}BS_')]
        self.scope_cache={}

    def title(self, parent):
        titles=[i for i in self.children[parent] if self.index.elements[i].tag=='title']
        if len(titles)!=1:return None
        i=titles[0];location=self.index.location(i);value=raw_text(self.index.inner(i))
        return {'text':value,'location':location} if self.raw_check(self.content,location,value) else None

    def scope(self, table):
        marker=table.get('scope_basis','');location=table.get('scope_location')
        if marker.startswith(('{XBRL}NT_C_','{XBRL}NT_S_')) and self.raw_check(self.content,location):
            raw=self.content[location['byte_start']:location['byte_end']].split(b'>',1)[0]
            if ('ACLASS="'+marker+'"').encode() in raw:
                return {'scope':'CFS' if 'NT_C_' in marker else 'OFS','basis':'explicit_xbrl_note_scope','location':location}
        index=table['location'].get('raw_index')
        if type(index) is not int or not 0<=index<len(self.index.elements):return None
        parent=self.index.elements[index].parent
        while parent is not None:
            element=self.index.elements[parent]
            if element.tag=='section-2':
                if parent in self.scope_cache:return self.scope_cache[parent]
                result=self._chapter_scope(parent);self.scope_cache[parent]=result
                return result
            parent=element.parent
        return None

    def _chapter_scope(self, parent):
        title=self.title(parent)
        if not title:return None
        scopes={'3.연결재무제표주석':('CFS','2.연결재무제표'), '5.재무제표주석':('OFS','4.재무제표')}
        scope_pair=scopes.get(normalize_label(title['text']))
        if not scope_pair:return None
        scope,paired_title=scope_pair;node=self.index.elements[parent]
        statements=[]
        for sibling in self.children[node.parent]:
            e=self.index.elements[sibling]
            if e.tag!='section-2' or e.end is None or e.end>node.start:continue
            heading=self.title(sibling)
            if heading and normalize_label(heading['text'])==paired_title:statements.append((e,heading))
        if len(statements)!=1:return None
        statement,heading=statements[0]
        anchors=[f for f in self.anchors if f['data']['scope']==scope
                 and statement.start<f['fragment']['data']['location'].get('byte_start',-1)<statement.end
                 and self.raw_check(self.content,f['fragment']['data']['location'])]
        if not anchors:return None
        anchor=min(anchors,key=lambda f:f['fragment']['data']['location']['byte_start'])
        return {'scope':scope,'basis':'paired_statement_and_note_chapters','note_heading':title,
                'statement_heading':heading,'statement_fact_id':anchor['id'],
                'statement_location':anchor['fragment']['data']['location']}

    def date(self, label, *, allow_balance_label=False):
        value=normalize_label(label);end=self.identity.get('end')
        if not end:return None
        current={'당기말','당반기말','당분기말'};previous={'전기말'}
        if value in current:return {'as_of':end,'basis':'explicit_end_label_and_cover','label':label}
        if value in previous:return {'as_of':str(int(end[:4])-1)+'-12-31','basis':'explicit_previous_year_end_label_and_cover','label':label}
        if not allow_balance_label:return None
        # 새로운 연결 해석은 원본 표지의 회사·기간 위치도 모두 확인한 경우에 한한다.
        locations=self.identity.get('locations',{})
        if not all(self.raw_check(self.content,locations.get(k)) for k in ('company','start','end')):return None
        match=re.search(r'(?:^|[.)])\s*(당기|당반기|당분기|전기|전반기|전분기)$',value)
        if not match:return None
        period=match[1]
        date=end if period.startswith('당') else (str(int(end[:4])-1)+end[4:] if period!='전기' else str(int(end[:4])-1)+'-12-31')
        return {'as_of':date,'basis':'balance_note_period_label_and_verified_cover',
                'label':label,'identity_locations':locations,'version':VERSION}
