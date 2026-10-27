"""원문 인덱스·API 레코드·13개 계정 후보를 기존 근거 테이블에 원자적으로 저장한다."""
import hashlib
import json
from uuid import uuid4

from lxml import etree

from agent.research_models import FactInput, FragmentInput
from agent.research_store import encode, now, row_data, safe_payload
from .document_extractor import DocumentExtractor, ExtractionFailure, VERSION, normalize_label, number


# 이름은 후보 검색 규칙이다. 계정 ID·구성·주석 대조는 4단계에서 수행한다.
ACCOUNT_RULES={
    'revenue':('CIS',{'매출액','수익(매출액)'}),
    'operating_profit':('CIS',{'영업이익','영업이익(손실)'}),
    'net_profit':('CIS',{'반기순이익','반기순이익(손실)','당기순이익','당기순이익(손실)','분기순이익','분기순이익(손실)'}),
    'operating_cashflow':('CF',{'영업활동으로 인한 현금흐름','영업활동현금흐름','영업활동으로부터의 현금흐름'}),
    'cash':('BS',{'현금및현금성자산','현금 및 현금성자산'}),
    'inventory':('BS',{'재고자산','유동재고자산'}),
    'receivables':('BS',{'매출채권 및 기타유동채권','매출채권및기타유동채권','매출채권및기타채권'}),
    'short_term_borrowings':('BS',{'단기차입금','유동 차입금','유동 차입금(사채 포함)'}),
    'current_long_term_borrowings':('BS',{'유동성장기차입금','유동성장기부채','유동성차입금','비유동차입금의 유동성 대체 부분','비유동차입금(사채 포함)의 유동성 대체 부분'}),
    'long_term_borrowings':('BS',{'장기차입금','비유동차입금','비유동차입금(사채 포함)의 비유동성 부분'}),
    'assets':('BS',{'자산총계'}),
    'liabilities':('BS',{'부채총계'}),
    'equity':('BS',{'자본총계'}),
}

# 본문 13개를 바꾸지 않고 현금 검산에 필요한 보조 입력만 별도로 식별한다.
SUPPORT_RULES={
    'cash_begin':('CF',{'기초현금및현금성자산','기초의 현금및현금성자산'}),
    'cash_end':('CF',{'기말현금및현금성자산','반기말의 현금및현금성자산','기말의 현금및현금성자산'}),
    'investing_cashflow':('CF',{'투자활동으로 인한 현금흐름','투자활동현금흐름'}),
    'financing_cashflow':('CF',{'재무활동으로 인한 현금흐름','재무활동현금흐름'}),
    'cash_fx':('CF',{'외화현금및현금성자산의 환율변동효과','현금및현금성자산에 대한 환율변동효과'}),
    'cash_change':('CF',{'현금및현금성자산의 증가(감소)','현금및현금성자산의순증가(감소)'}),
}



def candidates(table):
    if table['scope_hint']=='unknown' or not table['unit'] or not table['currency']:
        return []
    if not table['location'].get('raw_span_verified') or not table['location'].get('parser_text_matches_raw'):
        return []
    if not table.get('scope_location') or not table['scope_location'].get('raw_span_verified'):
        return []
    if not all(item['location'].get('raw_span_verified') for item in table['unit_candidates']+table['period_declarations']):
        return []
    result=[]
    for row in table['rows']:
        label=next((cell for cell in row['cells'] if cell['column']==1),None)
        if not label or label['tag']=='th' or not label['location'].get('raw_span_verified'):
            continue
        for account,(statement,aliases) in {**ACCOUNT_RULES,**SUPPORT_RULES}.items():
            if table['statement_hint']!=statement or normalize_label(label['text']) not in {normalize_label(x) for x in aliases}:
                continue
            if account=='receivables' and normalize_label(label['text'])=='매출채권및기타채권':
                category=row.get('row_section')
                if not category or category['text']!='유동자산' or not category['location'].get('raw_span_verified'):
                    continue
            for cell in row['cells']:
                if (cell['column']==1 or not cell['period'] or cell['nested_table']
                        or not cell['location'].get('raw_span_verified')):
                    continue
                if not all(head['location'].get('raw_span_verified') for head in cell['column_headers']):
                    continue
                value=number(cell['text'])
                if value is None:
                    continue
                result.append((account,label,cell,value))
    return result


class ExtractionService:
    def __init__(self,store):
        self.store=store

    def _fingerprint(self,source):
        return hashlib.sha256(encode({'source_hash':source['content_hash'],'version':VERSION,
                                      'lxml':list(etree.LXML_VERSION),'libxml':list(etree.LIBXML_VERSION),
                                      'policy':'strict_then_recover_candidates'}).encode()).hexdigest()

    def _existing(self,conn,source_id,fingerprint):
        for row in conn.execute('SELECT * FROM research_fragments WHERE source_id=?',(source_id,)):
            data=json.loads(row['data_json'])
            if data['context'].get('kind')=='extraction_manifest' and data['context'].get('fingerprint')==fingerprint:
                return row_data(row)
        return None

    def extract_source(self,source_id):
        source=self.store.get_source(source_id)
        content=self.store.source_content(source_id)  # 캐시 조회 전에도 원문 해시를 확인한다.
        fingerprint=self._fingerprint(source)
        with self.store.connection() as conn:
            existing=self._existing(conn,source_id,fingerprint)
        if existing:
            return {'status':'ok','manifest_id':existing['id'],**existing['data']['context']['summary'],'reused':True}
        metadata=source['metadata']
        if metadata['kind'] not in {'dart','synthetic'}:
            raise ExtractionFailure('source_kind_not_supported_in_stage3')
        fragments=[];facts=[]
        def add(value):
            fragment_id=uuid4().hex
            fragments.append((fragment_id,value))
            return fragment_id
        if metadata['media_type']=='application/json':
            data=json.loads(content)
            pointer_prefix=''
            if isinstance(data.get('response'),dict):
                wrapper=data
                for key,value in wrapper.get('query',{}).items():
                    if metadata['reporting_period'].get(key)!=value:
                        raise ExtractionFailure('wrapper_query_context_mismatch')
                data=wrapper['response'];pointer_prefix='/response'
            if metadata['reporting_period'].get('operation')!='financials' or not isinstance(data.get('list'),list):
                raise ExtractionFailure('unsupported_json_source')
            for index,record in enumerate(data['list']):
                if not isinstance(record,dict):
                    raise ExtractionFailure('invalid_financial_record')
                add(FragmentInput(location={'json_pointer':f'{pointer_prefix}/list/{index}','source_hash':source['content_hash'],
                    'receipt_number':metadata['receipt_number']},text=str(record.get('account_nm','')),
                    context={'kind':'api_record','record':record,
                             'record_semantic_sha256':hashlib.sha256(encode(record).encode()).hexdigest(),
                             'query_context':metadata['reporting_period'],'financial_verification':'not_performed'}))
            summary={'kind':'api_index','record_count':len(data['list']),'coverage':'indexed',
                     'financial_verification':'not_performed'}
        elif metadata['media_type'] in {'application/xml','text/xml','text/html'}:
            result=DocumentExtractor(content,media_type=metadata['media_type']).extract()
            warnings=['document_recovered_candidates_only'] if result['recovered'] else []
            for table in result['tables']:
                text='\n'.join(' | '.join(cell['text'] for cell in row['cells']) for row in table['rows'])
                table_id=add(FragmentInput(location={**table['location'],'source_hash':source['content_hash'],
                    'table_number':table['table_number'],'section_path':table['section_path'],
                    'receipt_number':metadata['receipt_number']},text=text,context=table,extraction_warnings=warnings))
                for account,label,cell,value in candidates(table):
                    cell_id=add(FragmentInput(location={**cell['location'],'source_hash':source['content_hash'],
                        'table_number':table['table_number'],'row':cell['row'],'column':cell['column'],
                        'section_path':table['section_path'],'receipt_number':metadata['receipt_number']},
                        text=cell['text'],context={'kind':'account_cell','table_fragment_id':table_id,
                            'row_label':label['text'],'row_label_location':label['location'],
                            'column_headers':cell['column_headers'],'unit_candidates':table['unit_candidates'],
                            'period_declarations':table['period_declarations'],'scope_basis':table['scope_basis'],
                            'scope_location':table['scope_location'],
                            'row_section':next(row.get('row_section') for row in table['rows'] if row['row']==cell['row']),
                            'period':cell['period'],'scope_hint':table['scope_hint']},extraction_warnings=warnings))
                    value=FactInput(account=account,original_account=label['text'],mapping_version=VERSION+'-name-candidate',
                        raw_value=cell['text'],exact_value=value,scope=table['scope_hint'],period=cell['period'],
                        currency=table['currency'],unit=table['unit'],transformations=[
                            {'rule':'comma_and_parentheses_to_decimal_string','version':VERSION,
                             'limitation':'계정 구성·단위 환산·API 대조·금융 의미 검증 전'}])
                    facts.append((uuid4().hex,cell_id,value))
            for paragraph in result['paragraphs']:
                add(FragmentInput(location={**paragraph['location'],'section_path':paragraph['section_path'],
                    'source_hash':source['content_hash'],'receipt_number':metadata['receipt_number']},
                    text=paragraph['text'],context=paragraph,extraction_warnings=warnings))
            summary={key:result[key] for key in ('parser_version','parser_kind','recovered','literal_repair','warnings',
                'raw_table_count','coverage','content_mismatch_table_count','original_grid_cells','expanded_grid_cells','financial_verification')}
            summary.update(table_count=len(result['tables']),paragraph_count=len(result['paragraphs']),
                           unmapped_raw_table_count=len(result['unmapped_raw_tables']),candidate_count=len(facts))
            # 생략된 원문 위치도 검색 인덱스의 한계로 보존한다.
            add(FragmentInput(location={'source_hash':source['content_hash']},text='원문 인덱스 검사 범위',
                context={'kind':'extraction_coverage','unmapped_raw_tables':result['unmapped_raw_tables'],
                         'warnings':result['warnings'],'content_mismatch_tables':result['content_mismatch_tables']},extraction_warnings=warnings))
        else:
            raise ExtractionFailure('unsupported_media_type')
        manifest_id=uuid4().hex
        manifest=FragmentInput(location={'source_hash':source['content_hash']},text='문서 추출 후보 인덱스',
            context={'kind':'extraction_manifest','fingerprint':fingerprint,'summary':summary,
                     'fragment_ids':[x[0] for x in fragments],'candidate_ids':[x[0] for x in facts]})
        fragments.append((manifest_id,manifest))
        # 파싱·직렬화는 DB 잠금 밖에서 끝내고, 후보/위치/완료 표시는 함께 확정한다.
        fragment_rows=[(fid,source_id,safe_payload(value.model_dump(mode='json')),
                        hashlib.sha256(value.text.encode()).hexdigest(),now()) for fid,value in fragments]
        fact_rows=[(fid,source['run_id'],fragment_id,safe_payload(value.model_dump(mode='json')),'candidate',now())
                   for fid,fragment_id,value in facts]
        with self.store.connection(write=True) as conn:
            existing=self._existing(conn,source_id,fingerprint)
            if existing:
                return {'status':'ok','manifest_id':existing['id'],**existing['data']['context']['summary'],'reused':True}
            conn.executemany('INSERT INTO research_fragments VALUES (?,?,?,?,?)',fragment_rows)
            conn.executemany('INSERT INTO research_facts VALUES (?,?,?,?,?,?)',fact_rows)
            _,basis=self.store._context(conn,source['run_id'])
            self.store._event(conn,basis['research_id'],basis['id'],source['run_id'],'source_indexed',
                             {'source_id':source_id,'manifest_id':manifest_id,**summary})
        return {'status':'ok','manifest_id':manifest_id,**summary,'reused':False}

    def fragment(self,fragment_id):
        with self.store.connection() as conn:
            result=row_data(self.store._one(conn,'research_fragments',fragment_id))
        self.store.source_content(result['source_id'])
        return result

    def original(self,fragment_id):
        fragment=self.fragment(fragment_id)
        location=fragment['data']['location']
        if not location.get('raw_span_verified'):
            raise ExtractionFailure('raw_span_unconfirmed')
        content=self.store.source_content(fragment['source_id'])
        start,end=location.get('byte_start'),location.get('byte_end')
        if not isinstance(start,int) or not isinstance(end,int) or not 0<=start<end<=len(content):
            raise ExtractionFailure('raw_span_unconfirmed')
        original=content[start:end]
        if hashlib.sha256(original).hexdigest()!=location.get('raw_span_sha256'):
            raise ExtractionFailure('raw_span_hash_mismatch')
        return original

    def search(self,source_id,query,limit=10):
        return self.search_many(source_id,[query],limit)[query]

    def search_many(self,source_id,queries,limit=10):
        """같은 원문의 여러 검색어를 한 번의 원문·조각 검증 순회로 평가한다."""
        queries=tuple(dict.fromkeys(queries))
        if not queries or any(not isinstance(q,str) or not q or len(q)>200 for q in queries) or not 1<=limit<=50:
            raise ExtractionFailure('invalid_query')
        self.store.source_content(source_id)
        source=self.store.get_source(source_id)
        normalized={q:q.casefold() for q in queries}
        hits={q:[] for q in queries};totals={q:0 for q in queries}
        with self.store.connection() as conn:
            manifest=self._existing(conn,source_id,self._fingerprint(source))
            if manifest is None:
                return {q:{'status':'not_indexed','items':[],'search_scope':'indexed_fragments_only',
                           'financial_verification':'not_performed'} for q in queries}
            summary=manifest['data']['context']['summary']
            selected_ids=set(manifest['data']['context']['fragment_ids'])
            for row in conn.execute('SELECT * FROM research_fragments WHERE source_id=? ORDER BY rowid',(source_id,)):
                if row['id'] not in selected_ids:
                    continue
                fragment=row_data(row); data=fragment['data']; context=data['context'];kind=context.get('kind')
                if kind=='extraction_manifest':
                    summary=context['summary']
                    continue
                if kind=='table':
                    units=context['unit_candidates']
                    values=((cell['text'],{**cell['location'],'table_number':context['table_number'],
                             'row':cell['row'],'column':cell['column']},cell['column_headers'],cell['period'])
                            for r in context['rows'] for cell in r['cells'])
                elif kind in {'paragraph','api_record'}:
                    units=[]
                    value=data['text'] if kind=='paragraph' else encode(context['record'])
                    values=((value,data['location'],[],None),)
                else:
                    continue
                for value,location,headers,period in values:
                    folded=value.casefold()
                    for query,needle in normalized.items():
                        if needle not in folded:
                            continue
                        totals[query]+=1
                        if len(hits[query])<limit:
                            hits[query].append({'fragment_id':fragment['id'],'kind':kind,'snippet':value[:800],
                                                'location':location,'section_path':context.get('section_path',[]),
                                                'unit_candidates':units,'column_headers':headers,'period':period,
                                                'warnings':data['extraction_warnings']})
        return {q:{'status':'ok' if hits[q] else 'no_match_in_index','items':hits[q],
                   'match_count':totals[q],'returned_count':len(hits[q]),'truncated':totals[q]>len(hits[q]),
                   'index_summary':summary,'manifest_id':manifest['id'],
                   'search_scope':'indexed_fragments_only','financial_verification':'not_performed'} for q in queries}
