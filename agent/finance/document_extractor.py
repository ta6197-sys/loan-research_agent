"""DART 원문에서 위치를 가진 표/문단 후보를 만든다. 금융 검증은 하지 않는다."""
from datetime import date
from calendar import monthrange
import hashlib
import re

from lxml import etree

from .source_locations import RawLocations, raw_text
from .literal_xml import escape_literal_text

VERSION='dart-extraction-1.7'
CELL_TAGS={'td','th','te','tu'}


class ExtractionFailure(ValueError):
    pass


def tag(node):
    return node.tag.rsplit('}',1)[-1].lower() if isinstance(node.tag,str) else ''


def text(node):
    return re.sub(r'\s+',' ',' '.join(node.itertext())).strip()


def normalize_label(value):
    return re.sub(r'\s+','',re.sub(r'^\s*(?:\([0-9]+\)|[IVXLCDMⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫ]+\.)\s*','',value))


def number(value):
    """원문 수치의 표현만 정리한다. 단위 환산·0 보완·반올림은 하지 않는다."""
    if not isinstance(value,str):
        return None
    value=value.strip()
    if not value or value in {'-','—','–'}:
        return None
    if not re.fullmatch(r'(?:-?\d[\d,]*(?:\.\d+)?|\(\d[\d,]*(?:\.\d+)?\))',value):
        return None
    unsigned=value.strip('()-')
    integer=unsigned.split('.')[0]
    if ',' in integer and not re.fullmatch(r'\d{1,3}(?:,\d{3})+',integer):
        return None
    value=value.replace(',','')
    return '-'+value[1:-1] if value.startswith('(') else value


def section_path(node):
    result=[]
    for ancestor in reversed(list(node.iterancestors())):
        if not (tag(ancestor).startswith('section') or tag(ancestor)=='table-group'):
            continue
        titles=[child for child in ancestor if tag(child) in {'title','h1','h2','h3','h4'}]
        if titles:
            title=text(titles[0])
            if title and (not result or title!=result[-1]):
                result.append(title)
    # HTML의 앞선 제목은 DOM 조상이 아닐 수 있다. 해당 위치보다 앞의 제목만 참고한다.
    if not result:
        previous=node.xpath('preceding::*[self::h1 or self::h2 or self::h3 or self::h4]')
        if previous:
            result=[text(previous[-1])]
    return result


def scope_and_statement(node,sections):
    marker=''
    for ancestor in node.iterancestors():
        for key,value in ancestor.attrib.items():
            if key.upper()=='ACLASS' and value.startswith('{XBRL}'):
                marker=value
                break
        if marker:
            break
    match=re.match(r'\{XBRL\}(BS|IS|CF)_([CS])',marker)
    if match:
        return ('CFS' if match[2]=='C' else 'OFS'), {'BS':'BS','IS':'CIS','CF':'CF'}[match[1]],marker
    note=re.match(r'\{XBRL\}NT_([CS])_',marker)
    if note:return ('CFS' if note[1]=='C' else 'OFS'),None,marker
    joined=' / '.join(sections)
    scope='CFS' if '연결' in joined else ('OFS' if '별도' in joined else 'unknown')
    statement=None
    if '주석' not in joined:
        for word,kind in [('재무상태표','BS'),('포괄손익계산서','CIS'),('손익계산서','IS'),('현금흐름표','CF')]:
            if word in joined:
                statement=kind
    return scope,statement,marker or 'section_title_hint'


def period_declarations(preamble):
    result=[]
    for item in preamble:
        value=item['text']
        dates=re.findall(r'\b(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})\b',value)
        if not dates:
            continue
        try:
            dates=[date(*(int(x) for x in parts)).isoformat() for parts in dates]
        except ValueError:
            continue
        label=normalize_label(re.split(r'\b20\d{2}[.\-/]',value,maxsplit=1)[0])
        if len(dates)==2 and ('부터' in value or 'From' in value):
            if dates[0]>dates[1]:
                continue
            period={'kind':'duration','start':dates[0],'end':dates[1],'column_kind':'declared_period'}
        elif len(dates)==1 and ('현재' in value or '기말' in value or '말' in value):
            period={'kind':'instant','as_of':dates[0]}
        else:
            continue
        result.append({'label':label,'period':period,'source_text':value,'location':item.get('location')})
    return result


def column_period(headers,declarations):
    matched=[item for item in declarations if item['label'] and
             any(item['label']==normalize_label(header['text']) for header in headers)]
    unique={repr(item['period']):item['period'] for item in matched}
    if len(unique)!=1:
        return None
    period=dict(next(iter(unique.values())))
    if period['kind']=='duration':
        labels=[normalize_label(item['text']) for item in headers]
        if '3개월' in labels:
            end=date.fromisoformat(period['end'])
            declared_start=date.fromisoformat(period['start'])
            if end.day!=monthrange(end.year,end.month)[1] or declared_start.day!=1:
                return None
            month=end.year*12+end.month-3
            start=date(month//12,month%12+1,1).isoformat()
            if start<period['start']:
                return None
            period.update(start=start,column_kind='three_months',
                          derivation='explicit_3_month_header_and_declared_end')
        elif '누적' in labels:
            period['column_kind']='cumulative'
        else:
            period['column_kind']='declared_period'
    return period


class DocumentExtractor:
    def __init__(self,content, *, media_type='application/xml',max_cells=50000,recover=True):
        self.content=content
        self.max_cells=max_cells
        self.allow_recovery=recover
        upper=content.upper()
        if b'\x00' in content or b'<!ENTITY' in upper or re.search(rb'<!DOCTYPE[^>]*(?:SYSTEM|PUBLIC|\[)',upper):
            raise ExtractionFailure('unsafe_or_unsupported_document')
        content.decode('utf-8',errors='strict')
        self.is_html=media_type=='text/html' or bool(re.search(rb'<html(?:\s|>)|<!doctype\s+html',content[:2048],re.I))
        self.raw=RawLocations(content,html=self.is_html)
        self.locations={}
        self.warnings=[]
        self.recovered=False
        self.literal_repair={'status':'not_needed','changes':[],'original_modified':False}
        options=dict(no_network=True,load_dtd=False,resolve_entities=False,huge_tree=False)
        strict=etree.XMLParser(recover=False,**options)
        try:
            self.root=etree.fromstring(content,strict)
            self.parser_kind='xml_strict'
        except etree.XMLSyntaxError:
            self.warnings.extend(self._logs(strict.error_log,'strict'))
            if not recover:
                raise ExtractionFailure('strict_parse_failed') from None
            repaired_root=None
            if not self.is_html:
                parsing_copy,self.literal_repair=escape_literal_text(content)
                if self.literal_repair['changes']:
                    copy_parser=etree.XMLParser(recover=False,**options)
                    try:
                        repaired_root=etree.fromstring(parsing_copy,copy_parser)
                        self.literal_repair['status']='strict_copy_parsed'
                        self.warnings.append({'phase':'literal_repair','type':'text_characters_escaped_in_parsing_copy',
                                              'count':len(self.literal_repair['changes'])})
                    except etree.XMLSyntaxError:
                        self.literal_repair['status']='strict_copy_failed_not_adopted'
                        self.warnings.extend(self._logs(copy_parser.error_log,'literal_repair_strict'))
            if repaired_root is not None:
                self.root=repaired_root
                self.parser_kind='xml_literal_escape_strict'
            else:
                parser=(etree.HTMLParser(no_network=True,recover=True,huge_tree=False) if self.is_html
                        else etree.XMLParser(recover=True,**options))
                self.root=etree.fromstring(content,parser)
                self.warnings.extend(self._logs(parser.error_log,'recovery'))
                self.parser_kind='html_recovery' if self.is_html else 'xml_recovery'
            self.recovered=True
        if self.root is None:
            raise ExtractionFailure('empty_document')

    @staticmethod
    def _logs(log,phase):
        # 원문/키가 오류 전문에 반사되지 않게 위치와 분류만 기록한다.
        return [{'phase':phase,'line':e.line,'column':e.column,'type':e.type_name,
                 'level':e.level_name} for e in log]

    def locate(self,node):
        if node in self.locations:
            return self.locations[node]
        candidates=self.raw.by_line_tag.get((node.sourceline,tag(node)),[])
        matches=[]
        for index in candidates:
            value=self.raw.inner(index)
            if value is not None and raw_text(value)==text(node):
                matches.append(index)
        if len(matches)>1 and node.getparent() is not None:
            parent_location=self.locate(node.getparent())
            if parent_location.get('raw_index') is not None:
                parent_index=parent_location['raw_index']
                siblings=[i for i in candidates if self.raw.elements[i].parent==parent_index]
                parsed_siblings=[child for child in node.getparent() if tag(child)==tag(node)]
                if len(siblings)==len(parsed_siblings):
                    selected=siblings[parsed_siblings.index(node)]
                    if selected in matches:
                        matches=[selected]
        if len(matches)==1:
            index=matches[0]
            location={**self.raw.location(index),'raw_index':index,'parser_text_matches_raw':True}
        elif tag(node)=='table' and not self.is_html and self.recovered and len(candidates)==1 and self.raw.location(candidates[0])['raw_span_verified']:
            # 복구로 내용이 달라진 표도 유일한 원본 경계는 반환한다. 내용 일치를 뜻하지 않는다.
            index=candidates[0]
            location={**self.raw.location(index),'raw_index':index,'parser_text_matches_raw':False,
                      'raw_span_warnings':['parser_table_content_changed']}
        else:
            location={'line':node.sourceline,'raw_span_verified':False,
                      'parser_text_matches_raw':False,'raw_span_warnings':[
                          'ambiguous_raw_location' if matches else 'raw_text_or_location_unconfirmed']}
        location['xpath']=node.getroottree().getpath(node)
        location['xpath_basis']='parsed_tree'
        self.locations[node]=location
        return location

    def preamble(self,table):
        elements=[]
        for sibling in table.itersiblings(preceding=True):
            if tag(sibling) in {'title','h1','h2','h3','h4'}:
                break
            if tag(sibling)=='table' and sibling.get('BORDER',sibling.get('border',''))=='1':
                break
            elements.append(sibling)
        items=[]
        for sibling in reversed(elements):
            cells=[node for node in sibling.iter() if tag(node) in CELL_TAGS]
            for node in cells or [sibling]:
                value=text(node)
                if value:
                    items.append({'text':value,'location':self.locate(node)})
        return items

    def extract(self):
        tables=[]
        total_cells=0
        for table_number,table in enumerate((node for node in self.root.iter() if tag(node)=='table'),1):
            sections=section_path(table)
            scope,statement,scope_basis=scope_and_statement(table,sections)
            scope_node=next((ancestor for ancestor in table.iterancestors()
                             if any(key.upper()=='ACLASS' and value==scope_basis
                                    for key,value in ancestor.attrib.items())),None)
            scope_location=self.locate(scope_node) if scope_node is not None else None
            preamble=self.preamble(table)
            rows=[]; occupied={}; original=[]
            table_warnings=[]
            for row_number,tr in enumerate((node for node in table.iter() if tag(node)=='tr' and
                    next((a for a in node.iterancestors() if tag(a)=='table'),None) is table),1):
                cells=[]; column=1
                for node in (child for child in tr if tag(child) in CELL_TAGS):
                    while (row_number,column) in occupied:
                        column+=1
                    attrs={key.lower():value for key,value in node.attrib.items()}
                    try:
                        rowspan=int(attrs.get('rowspan','1'));colspan=int(attrs.get('colspan','1'))
                        if rowspan<1 or colspan<1 or rowspan*colspan>self.max_cells:
                            raise ValueError
                    except ValueError:
                        raise ExtractionFailure('invalid_table_span') from None
                    total_cells+=rowspan*colspan
                    if total_cells>self.max_cells:
                        raise ExtractionFailure('table_cell_limit')
                    for r in range(row_number,row_number+rowspan):
                        for c in range(column,column+colspan):
                            if (r,c) in occupied:
                                raise ExtractionFailure('overlapping_table_span')
                            occupied[(r,c)]=(row_number,column)
                    location=self.locate(node)
                    raw_index=location.get('raw_index')
                    value=raw_text(self.raw.inner(raw_index)) if raw_index is not None else text(node)
                    nested=any(tag(child)=='table' for child in node.iterdescendants())
                    cell={'row':row_number,'column':column,'rowspan':rowspan,'colspan':colspan,
                          'tag':tag(node),'text':value,'location':location,'nested_table':nested}
                    cells.append(cell);original.append(cell)
                    column+=colspan
                rows.append({'row':row_number,'cells':cells})
            # 같은 그룹의 원문 머리말과 현재 표의 단위 표시를 함께 보존한다.
            declarations=preamble+[{'text':cell['text'],'location':cell['location']}
                                   for cell in original if '단위' in cell['text']]
            units=[]
            for item in declarations:
                for match in re.finditer(r'단위\s*[:：]\s*([^\)\]）\n]+)',item['text']):
                    units.append({'text':match[1].strip(),'location':item['location']})
            recognized={item['text'] for item in units if item['text'] in {'원','천원','백만원','억원','USD','달러'}}
            unit=next(iter(recognized)) if len(recognized)==1 and all(x['text'] in recognized for x in units) else None
            periods=period_declarations(preamble)
            for cell in original:
                headers=[head for head in original if head['tag']=='th' and head['row']<cell['row']
                         and head['column']<=cell['column']<head['column']+head['colspan']]
                cell['column_headers']=[{'text':head['text'],'row':head['row'],'column':head['column'],
                                        'location':head['location']} for head in headers if head['text']]
                cell['period']=column_period(cell['column_headers'],periods)
            row_section=None
            for row in rows:
                label=next((cell for cell in row['cells'] if cell['column']==1),None)
                if label and normalize_label(label['text']) in {'자산','유동자산','비유동자산','부채','유동부채','비유동부채','자본'}:
                    row_section={'text':normalize_label(label['text']),'location':label['location']}
                row['row_section']=row_section
            tables.append({'kind':'table','table_number':table_number,'section_path':sections,
                'scope_hint':scope,'scope_basis':scope_basis,'statement_hint':statement,
                'scope_location':scope_location,
                'location':self.locate(table),'preamble':preamble,'unit_candidates':units,
                'unit':unit,'currency':'KRW' if unit in {'원','천원','백만원','억원'} else ('USD' if unit in {'USD','달러'} else None),
                'period_declarations':periods,'rows':rows,'warnings':table_warnings})
        paragraphs=[]
        for node in self.root.iter():
            if tag(node) not in {'p','title','h1','h2','h3','h4'} or not text(node):
                continue
            if any(tag(a)=='table' for a in node.iterancestors()):
                continue
            paragraphs.append({'kind':'paragraph','text':text(node),'section_path':section_path(node),
                               'location':self.locate(node)})
        raw_tables=[i for i,e in enumerate(self.raw.elements) if e.tag=='table']
        mapped={table['location'].get('raw_index') for table in tables}
        omitted=[self.raw.location(i) for i in raw_tables if i not in mapped]
        changed=[t['location'] for t in tables if not t['location'].get('parser_text_matches_raw')]
        if changed:
            self.warnings.append({'phase':'coverage','type':'parsed_table_content_changed','count':len(changed)})
        if omitted:
            self.warnings.append({'phase':'coverage','type':'raw_tables_not_mapped','count':len(omitted)})
        return {'parser_version':VERSION,'lxml_version':etree.LXML_VERSION,
                'source_hash':hashlib.sha256(self.content).hexdigest(),'parser_kind':self.parser_kind,
                'recovered':self.recovered,'literal_repair':self.literal_repair,'warnings':self.warnings,'tables':tables,'paragraphs':paragraphs,
                'raw_table_count':len(raw_tables),'unmapped_raw_tables':omitted,
                'content_mismatch_tables':changed,'content_mismatch_table_count':len(changed),
                'coverage':'partial' if omitted or changed else 'indexed',
                'original_grid_cells':sum(len(row['cells']) for t in tables for row in t['rows']),
                'expanded_grid_cells':total_cells,'financial_verification':'not_performed'}
