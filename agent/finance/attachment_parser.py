"""로컬 첨부의 제한된 읽기. 수식·매크로·링크·PDF 동작은 실행하지 않는다."""
import csv
import io
import json
from pathlib import PurePosixPath
import re
import zipfile
from lxml import etree

MAX_FILE=20*1024*1024
MAX_PAGES=200
MAX_CELLS=50000
MAX_TEXT=40000


class AttachmentLimit(ValueError):pass


def xml(raw):
    if len(raw)>32*1024*1024 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('unsafe_xml')
    return etree.fromstring(raw,etree.XMLParser(resolve_entities=False,no_network=True,huge_tree=False))


def decode(raw):
    for encoding in ('utf-8-sig','cp949'):
        try:return raw.decode(encoding),encoding
        except UnicodeError:pass
    raise ValueError('unsupported_encoding')


def parse_attachment(filename,raw):
    if len(raw)>MAX_FILE:raise AttachmentLimit('file_size_limit')
    extension=PurePosixPath(filename).suffix.lower()
    result={'version':'attachment-read-1','status':'readable','financial_verification':'not_performed',
        'text':'','locations':[],'warnings':[],'complete_read':False}
    if extension in ('.txt','.csv'):
        value,encoding=decode(raw);result['encoding']=encoding
        if extension=='.txt':
            lines=value.splitlines();result['line_count']=len(lines)
            result['locations']=[{'line':i,'text':line} for i,line in enumerate(lines,1)]
        else:
            sample=value[:8192]
            try:delimiter=csv.Sniffer().sniff(sample,delimiters=',;\t|').delimiter
            except csv.Error:delimiter=','
            rows=[];count=0
            for i,row in enumerate(csv.reader(io.StringIO(value),delimiter=delimiter),1):
                count+=len(row)
                if count>MAX_CELLS:raise AttachmentLimit('cell_limit')
                rows.extend({'row':i,'column':j,'text':cell,'formula_execution':'never'} for j,cell in enumerate(row,1))
            result.update(locations=rows,cell_count=count,delimiter=delimiter)
        result['complete_read']=True
    elif extension=='.pdf':
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(raw),strict=True)
        if reader.is_encrypted:return {**result,'status':'encrypted','warnings':['password_not_requested_or_stored']}
        if len(reader.pages)>MAX_PAGES:raise AttachmentLimit('pdf_page_limit')
        unreadable=[];locations=[]
        for number,page in enumerate(reader.pages,1):
            try:text=page.extract_text() or ''
            except Exception:text=''
            if not text.strip():unreadable.append(number)
            locations.append({'page':number,'text':text,'status':'text_extracted' if text.strip() else 'no_extractable_text'})
        result.update(locations=locations,page_count=len(reader.pages),unreadable_pages=unreadable,
            complete_read=not unreadable,status='partial' if unreadable else 'readable')
        if unreadable:result['warnings'].append('unreadable_pages_not_reviewed; OCR_not_performed')
        if reader.root_object.get('/OpenAction'):result['warnings'].append('pdf_action_not_executed')
    elif extension=='.xlsx':
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        rns='{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos=archive.infolist();names=[i.filename for i in infos]
            if len(infos)>2000 or len(set(names))!=len(names):raise AttachmentLimit('xlsx_entry_limit')
            if any(PurePosixPath(n).is_absolute() or '..' in PurePosixPath(n).parts or '\\' in n for n in names):raise ValueError('unsafe_xlsx_path')
            if sum(i.file_size for i in infos)>64*1024*1024:raise AttachmentLimit('xlsx_expanded_size_limit')
            if any(n.lower().endswith('vbaproject.bin') for n in names):raise ValueError('active_workbook_not_supported')
            if any(i.flag_bits&1 for i in infos):return {**result,'status':'encrypted'}
            if any(n.startswith('xl/externalLinks/') for n in names):result['warnings'].append('external_links_not_opened')
            workbook=xml(archive.read('xl/workbook.xml'));rels=xml(archive.read('xl/_rels/workbook.xml.rels'))
            relation={e.get('Id'):e for e in rels}
            shared=[]
            if 'xl/sharedStrings.xml' in names:
                shared=[''.join(e.itertext()) for e in xml(archive.read('xl/sharedStrings.xml'))]
            entries=[];sheets=[];missing_cache=0
            for sheet in workbook.findall('s:sheets/s:sheet',ns):
                rel=relation.get(sheet.get(rns))
                if rel is None or rel.get('TargetMode')=='External':raise ValueError('external_worksheet_not_supported')
                target=rel.get('Target','');path=target.lstrip('/') if target.startswith('/') else 'xl/'+target
                if not path.startswith('xl/worksheets/') or '..' in PurePosixPath(path).parts:raise ValueError('unsafe_worksheet_target')
                tree=xml(archive.read(path));hidden_columns=[]
                for col in tree.findall('s:cols/s:col',ns):
                    if col.get('hidden')=='1':hidden_columns.append((int(col.get('min')),int(col.get('max'))))
                state=sheet.get('state','visible');sheets.append({'name':sheet.get('name'),'state':state,'hidden_columns':hidden_columns})
                for row in tree.findall('s:sheetData/s:row',ns):
                    for cell in row.findall('s:c',ns):
                        if len(entries)>=MAX_CELLS:raise AttachmentLimit('cell_limit')
                        address=cell.get('r','');match=re.fullmatch('([A-Z]{1,3})([1-9][0-9]{0,6})',address)
                        if not match:raise ValueError('invalid_cell_address')
                        column=0
                        for char in match[1]:column=column*26+ord(char)-64
                        if column>16384 or int(match[2])>1048576:raise ValueError('invalid_cell_address')
                        value=cell.findtext('s:v',namespaces=ns);kind=cell.get('t','n');formula=cell.findtext('s:f',namespaces=ns)
                        if kind=='s':
                            index=int(value) if value is not None else -1
                            if not 0<=index<len(shared):raise ValueError('invalid_shared_string')
                            value=shared[index]
                        elif kind=='inlineStr':value=''.join(cell.find('s:is',ns).itertext()) if cell.find('s:is',ns) is not None else ''
                        if formula is not None and value is None:missing_cache+=1
                        entries.append({'sheet':sheet.get('name'),'cell':address,'text':value or '',
                            'data_type':kind,'style_index':cell.get('s'),'formula_present':formula is not None,
                            'formula_cached_value':value if formula is not None else None,'formula_execution':'never',
                            'cache_state':'missing' if formula is not None and value is None else 'freshness_unverified' if formula is not None else 'not_formula',
                            'hidden_sheet':state!='visible','hidden_row':row.get('hidden')=='1',
                            'hidden_column':any(a<=column<=b for a,b in hidden_columns)})
            result.update(locations=entries,sheets=sheets,cell_count=len(entries),missing_formula_cache_count=missing_cache,
                complete_read=missing_cache==0,status='partial' if missing_cache else 'readable')
            if any(e['formula_present'] for e in entries):result['warnings'].append('cached_formula_values_not_recalculated_or_certified_current')
    else:raise ValueError('unsupported_attachment_type')
    # 원문 위치 목록과 제한된 발췌를 구분한다. 텍스트 절단도 전체 확인으로 표시하지 않는다.
    text='\n'.join(str(e.get('text','')) for e in result['locations'])
    if len(text)>MAX_TEXT:
        result['warnings'].append('text_preview_truncated');result['complete_read']=False;result['status']='partial'
    result['text']=text[:MAX_TEXT]
    if len(json.dumps(result,ensure_ascii=False).encode())>2*1024*1024:
        result['locations']=result['locations'][:500];result['complete_read']=False;result['status']='partial'
        result['warnings'].append('location_preview_truncated')
    return result
