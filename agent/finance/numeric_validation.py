"""설계 1.0의 결정적 수치 대조. 모델·네트워크 없이 불변 검사 이력을 만든다."""
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, Inexact, InvalidOperation, ROUND_HALF_UP, ROUND_DOWN, localcontext
import hashlib
import json
import re
from uuid import uuid4
from pathlib import Path

from agent.research_store import IntegrityFailure, encode, now, row_data, safe_payload
from .document_extractor import ExtractionFailure, normalize_label, number
from .extraction_service import ExtractionService, ACCOUNT_RULES, SUPPORT_RULES
from .source_locations import RawLocations, raw_text
from .disclosure_audit import audit_disclosure_history
from .note_checks import reconcile_note_amounts
from .composition_review import composition_reviews, withhold_affected_calculations
from .borrowing_details import collect_details
from .account_equivalence import prepare_context, verified_alias_pair
from .borrowing_composition import reconcile_composition, link_same_period_compositions
from .scope_observations import observe_scope, unchanged_scope_support
from .consolidation_checks import VERSION as SCOPE_CHANGE_VERSION, collect_disclosed_changes, blocking_changes

POLICY_VERSION='numeric-validation-1.27'
UNIT_SCALE={'원':'1','천원':'1000','백만원':'1000000','억원':'100000000'}
ACCOUNT_IDS={
    'revenue':{'ifrs-full_Revenue'},
    'operating_profit':{'dart_OperatingIncomeLoss'},
    'net_profit':{'ifrs-full_ProfitLoss'},
    'operating_cashflow':{'ifrs-full_CashFlowsFromUsedInOperatingActivities'},
    'cash':{'ifrs-full_CashAndCashEquivalents'},
    'inventory':{'ifrs-full_Inventories'},
    'receivables':{'ifrs-full_TradeAndOtherCurrentReceivables'},
    'short_term_borrowings':{'ifrs-full_ShorttermBorrowings'},
    'current_long_term_borrowings':{'ifrs-full_CurrentPortionOfLongtermBorrowings'},
    'long_term_borrowings':{'ifrs-full_LongtermBorrowings','dart_LongTermBorrowingsGross'},
    'assets':{'ifrs-full_Assets'},'liabilities':{'ifrs-full_Liabilities'},'equity':{'ifrs-full_Equity'},
    'cash_begin':{'dart_CashAndCashEquivalentsAtBeginningOfPeriodCf'},
    'cash_end':{'dart_CashAndCashEquivalentsAtEndOfPeriodCf'},
    'investing_cashflow':{'ifrs-full_CashFlowsFromUsedInInvestingActivities'},
    'financing_cashflow':{'ifrs-full_CashFlowsFromUsedInFinancingActivities'},
    'cash_fx':{'ifrs-full_EffectOfExchangeRateChangesOnCashAndCashEquivalents'},
    'cash_change':{'ifrs-full_IncreaseDecreaseInCashAndCashEquivalents'},
}
BORROWINGS={'short_term_borrowings','current_long_term_borrowings','long_term_borrowings'}


def decimal(value):
    if not isinstance(value,str) or len(value)>512:
        raise ValueError('exact_decimal_string_required')
    try:
        result=Decimal(value)
    except InvalidOperation:
        raise ValueError('invalid_decimal') from None
    if not result.is_finite() or abs(result.as_tuple().exponent)>512:
        raise ValueError('finite_bounded_decimal_required')
    return result


def exact_sum(values):
    with localcontext() as context:
        context.prec=4096
        context.traps[Inexact]=True
        return sum((decimal(x) for x in values),Decimal(0))


def match_amount(displayed, api_value, unit, rounding=None):
    """반올림은 확인된 선언만 적용. 단위 표시만으로 반올림 방법을 추측하지 않는다."""
    if displayed is None or api_value is None:
        return {'status':'not_checked','match':'missing','reason':'missing_is_not_zero'}
    if unit not in UNIT_SCALE:
        return {'status':'not_checked','match':'unknown_unit'}
    with localcontext() as context:
        context.prec=4096;context.traps[Inexact]=True
        original=decimal(displayed);api=decimal(api_value);scale=decimal(UNIT_SCALE[unit])
        normalized=original*scale
        result={'original_normalized':str(normalized),'api_value':str(api),
                'difference':str(api-normalized),'unit_scale':str(scale)}
        if normalized==api:
            return {**result,'status':'pass','match':'exact'}
        if rounding and rounding.get('evidence_confirmed') is True:
            methods={'half_up':ROUND_HALF_UP,'truncate':ROUND_DOWN}
            digits=rounding.get('decimal_places')
            method=methods.get(rounding.get('method'))
            if type(digits) is int and 0<=digits<=12 and method:
                step=Decimal(1).scaleb(-digits)
                # quantize의 의도된 표시 반올림만 Inexact를 허용한다.
                context.traps[Inexact]=False
                rounded=(api/scale).quantize(step,rounding=method)
                if rounded!=original:
                    return {**result,'status':'fail','match':'outside_declared_rounding_rule','rounding_rule':rounding}
                if rounded==original:
                    return {**result,'status':'pass','match':'rounding',
                            'rounding_rule':rounding,'display_step_in_base_unit':str(step*scale)}
        return {**result,'status':'fail' if unit=='원' else 'not_checked',
                'match':'different' if unit=='원' else 'rounding_method_unconfirmed'}


def period_key(period):
    return {k:period[k] for k in ('kind','as_of','start','end') if k in period}


def api_field(period, statement, query):
    """12월 결산·역년 보고기간만 지원. 다른 회계연도는 원문을 확인해도 보류한다."""
    try:
        year=int(query['bsns_year']);code=query['reprt_code']
        month={'11011':'12-31','11012':'06-30','11013':'03-31','11014':'09-30'}[code]
        if period.get('kind')=='instant' and statement=='BS':
            candidates={f'{year}-{month}':'thstrm_amount',f'{year-1}-12-31':'frmtrm_amount'}
            if code=='11011':candidates[f'{year-2}-12-31']='bfefrmtrm_amount'
            return candidates.get(period.get('as_of'))
        if period.get('kind')!='duration' or statement not in {'CF','IS','CIS'}:
            return None
        start=date.fromisoformat(period['start']);end=date.fromisoformat(period['end'])
        shift=year-end.year
        if end.isoformat()!=f'{end.year}-{month}' or shift not in (0,1,2):return None
        if shift==2 and code!='11011':return None
        if code=='11011':
            return ('thstrm_amount','frmtrm_amount','bfefrmtrm_amount')[shift] if start.isoformat()==f'{end.year}-01-01' else None
        if statement=='CF':
            return ('thstrm_amount','frmtrm_q_amount')[shift] if shift<2 and start.isoformat()==f'{end.year}-01-01' else None
        if period.get('column_kind')=='three_months':
            quarter_month={3:1,6:4,9:7}[end.month]
            return ('thstrm_amount','frmtrm_q_amount')[shift] if start.isoformat()==f'{end.year}-{quarter_month:02}-01' else None
        if start.isoformat()==f'{end.year}-01-01':
            return ('thstrm_add_amount','frmtrm_add_amount')[shift]
        quarter_month={3:1,6:4,9:7}[end.month]
        if start.isoformat()==f'{end.year}-{quarter_month:02}-01':
            return ('thstrm_amount','frmtrm_q_amount')[shift]
    except (KeyError,ValueError,TypeError):
        return None
    return None


def comparison_policy(current, previous, *, account_equivalent=False):
    if current['scope']!=previous['scope'] or current['currency']!=previous['currency'] or current['account']!=previous['account']:
        return 'scope_currency_or_account_mismatch'
    if not account_equivalent and normalize_label(current.get('original_account',''))!=normalize_label(previous.get('original_account','')):
        return 'account_composition_unconfirmed'
    a,b=current['period'],previous['period']
    if a.get('kind')!=b.get('kind'):return 'instant_duration_mismatch'
    if a.get('kind')=='instant':
        x=date.fromisoformat(a['as_of']);y=date.fromisoformat(b['as_of'])
        if y.isoformat()==f'{x.year-1}-12-31':return 'previous_year_end'
        if x.month==y.month and x.day==y.day and x.year==y.year+1:return 'prior_same_date_supplementary'
        return 'incomparable_dates'
    x0,x1=(date.fromisoformat(a[k]) for k in ('start','end'))
    y0,y1=(date.fromisoformat(b[k]) for k in ('start','end'))
    if (x0.month,x0.day,x1.month,x1.day)==(y0.month,y0.day,y1.month,y1.day) and x0.year==y0.year+1 and x1.year==y1.year+1:
        return 'same_period'
    return 'incomparable_durations'


class NumericValidationService:
    def __init__(self,store):
        self.store=store;self.extraction=ExtractionService(store)

    @staticmethod
    def _raw_check(content,location,text=None):
        if not location or not location.get('raw_span_verified'):return False
        start,end=location.get('byte_start'),location.get('byte_end')
        if type(start) is not int or type(end) is not int or not 0<=start<end<=len(content):return False
        raw=content[start:end]
        return (hashlib.sha256(raw).hexdigest()==location.get('raw_span_sha256') and
                (text is None or raw_text(raw)==text))

    def _document_identity(self,content):
        index=RawLocations(content);company_codes=set();result={'company':None,'start':None,'end':None,'locations':{}}
        for i,element in enumerate(index.elements):
            if not index.location(i)['raw_span_verified']:continue
            token=content[element.start:element.opening_end]
            if element.tag=='company-name' and element.parent is not None and index.elements[element.parent].tag=='document':
                match=re.search(rb'\bAREGCIK="(\d{8})"',token,re.I)
                if match:
                    company_codes.add(match[1].decode());result['locations']['company']=index.location(i)
            if element.tag=='tu':
                kind=re.search(rb'\bAUNIT="(PERIODFROM|PERIODTO)"',token,re.I)
                value=re.search(rb'\bAUNITVALUE="(\d{8})"',token,re.I)
                if kind and value:
                    key='start' if kind[1].upper()==b'PERIODFROM' else 'end'
                    if result[key] is None:
                        try:result[key]=date.fromisoformat(value[1].decode()).isoformat()
                        except ValueError:continue
                        result['locations'][key]=index.location(i)
        result['company']=next(iter(company_codes)) if len(company_codes)==1 else None
        return result

    def _declared_rounding(self,content,table):
        declarations=[]
        for item in table.get('preamble',[]):
            if not self._raw_check(content,item['location'],item['text']):continue
            # 단위만으로 방법을 정하지 않고 원문에 정확한 처리 방법이 쓰인 경우만 지원한다.
            match=re.fullmatch(r'\s*(원|천원|백만원|억원)\s*미만(?:은|의\s*금액은)?\s*(사사오입|절사)(?:하였습니다|합니다)?[.]?\s*',item['text'])
            if match and match[1]==table['unit']:
                declarations.append({'evidence_confirmed':True,'decimal_places':0,
                    'method':'half_up' if match[2]=='사사오입' else 'truncate','location':item['location'],
                    'declaration':item['text']})
        methods={x['method'] for x in declarations}
        return declarations[0] if len(methods)==1 else None

    def _validate_fact(self,fact,source,content,identity,api_records,basis,company,superseded,fiscal_month=None):
        d=fact['data'];fragment=fact['fragment']['data'];ctx=fragment['context'];checks={}
        def check(rule,status,reason):checks[rule]={'status':status,'reason':reason}
        corp=company.get('corp_code')
        check('V01','pass' if corp and identity['company']==corp else 'fail','original_company_code')
        receipt=source['metadata'].get('receipt_number') or ''
        cutoff=date.fromisoformat(basis['data']['public_cutoff'])
        try:
            receipt_date=date.fromisoformat(receipt[:8])
            published=source['metadata'].get('published_at')
            dates_ok=bool(re.fullmatch(r'[0-9]{14}',receipt)) and receipt_date<=cutoff and (not published or date.fromisoformat(published[:10])<=cutoff)
        except ValueError:dates_ok=False
        check('V02','pass' if dates_ok else 'fail','receipt_and_publication_cutoff')
        check('V13','not_checked' if source['id'] in superseded else 'pass',
              'superseded_before_cutoff' if source['id'] in superseded else 'same_receipt_pair_only_latest_history_not_audited')
        check('V18','pass' if d['exact_value'] is not None and d['raw_value'] is not None else 'not_checked','missing_is_not_zero')
        table=self.extraction.fragment(ctx['table_fragment_id'])['data']['context']
        proof=[(fragment['location'],fragment['text']),
               (ctx['row_label_location'],ctx['row_label']), (ctx.get('scope_location'),None)]
        proof += [(x['location'],x.get('text',x.get('source_text'))) for x in ctx['column_headers']+ctx['period_declarations']]
        for unit in ctx['unit_candidates']:
            loc=unit['location']
            proof.append((loc,None))
            if self._raw_check(content,loc):
                declared=re.findall(r'단위\s*[:：]\s*([^\)\]）\n]+)',raw_text(content[loc['byte_start']:loc['byte_end']]))
                if unit['text'] not in [x.strip() for x in declared]:proof.append((None,None))
        table_cell=next((c for row in table['rows'] for c in row['cells']
                         if c['row']==fragment['location'].get('row') and c['column']==fragment['location'].get('column')),None)
        raw_ok=all(self._raw_check(content,loc,text) for loc,text in proof)
        row=next((r for r in table['rows'] if r['row']==fragment['location'].get('row')),None)
        label=next((c for c in row['cells'] if c['column']==1),None) if row else None
        raw_ok=raw_ok and bool(label and label['text']==d['original_account']==ctx['row_label'] and
            label['location']['byte_start']==ctx['row_label_location']['byte_start'])
        raw_ok=raw_ok and all(ctx.get(k)==table.get(k) for k in ('period_declarations','unit_candidates','scope_location'))
        raw_ok=raw_ok and bool(table_cell and table_cell['period']==d['period'] and table_cell['text']==d['raw_value'])
        raw_ok=raw_ok and fragment['location'].get('source_hash')==source['content_hash']
        check('V07','pass' if raw_ok else 'fail','original_cell_label_headers_unit_period_scope_hash')
        scope=d['scope'];check('V03','pass' if scope in ('OFS','CFS') and scope==ctx['scope_hint']==table['scope_hint'] else 'fail','scope_group')
        if d['account']=='receivables' and normalize_label(d['original_account'])=='매출채권및기타채권':
            category=ctx.get('row_section')
            category_ok=category and category.get('text')=='유동자산' and self._raw_check(content,category['location'])
            if category_ok:
                loc=category['location']
                category_ok=normalize_label(raw_text(content[loc['byte_start']:loc['byte_end']]))=='유동자산'
            if not category_ok:
                check('V06','fail','current_asset_hierarchy_missing')
        matches=[];unsupported=[]
        rules={**ACCOUNT_RULES,**SUPPORT_RULES};statement=rules[d['account']][0]
        aliases={normalize_label(x) for x in rules[d['account']][1]}
        for item in api_records:
            record=item['data']['context']['record'];query=item['data']['context']['query_context']
            if query.get('fs_div')!=scope or record.get('sj_div') not in ({'IS','CIS'} if statement=='CIS' else {statement}):continue
            if record.get('rcept_no')!=receipt:continue
            if normalize_label(record.get('account_nm',''))!=normalize_label(d['original_account']):continue
            if record.get('account_id') not in ACCOUNT_IDS[d['account']]:
                unsupported.append(item['id']);continue
            field=api_field(d['period'],record['sj_div'],query)
            matches.append((item,record,query,field))
        details={'checks':checks,'account':d['account'],'original_account':d['original_account'],'currency':d['currency'],'source_id':source['id'],'scope':scope,'period':d['period'],
                 'extraction_warnings':fragment['extraction_warnings'],'identity_locations':identity['locations'],
                 'limitations':['동일 접수번호의 수치 대조이며 최신 정정 전체 이력을 확인한 결과가 아님']}
        if d['account']=='receivables':details['limitations'].append('원문 표시의 기타채권 포함 계정이며 순수 매출채권으로 대체하거나 회전율에 사용하지 않음')
        if d['account'] in BORROWINGS:
            check('V12','not_checked','notes_lease_guarantee_composition_requires_separate_review')
            details['limitations'].append('본표 차입 항목 대조. 주석 구성·리스·보증 중복 검사는 미완료')
        if len(matches)!=1:
            check('V06','not_checked','unknown_account_id' if unsupported else 'api_match_missing_or_ambiguous')
            check('V04','not_checked','api_match_missing_or_ambiguous')
            check('V05','not_checked','api_match_missing_or_ambiguous')
            details['api_candidate_count']=len(matches)
        else:
            item,record,query,field=matches[0]
            api_source=self.store.get_source(item['source_id'])
            api_meta=api_source['metadata'];api_published=api_meta.get('published_at')
            try:api_in_cutoff=not api_published or date.fromisoformat(api_published[:10])<=cutoff
            except ValueError:api_in_cutoff=False
            if not api_in_cutoff:check('V02','fail','api_publication_after_cutoff')
            if api_meta.get('receipt_number')!=receipt:check('V13','fail','api_source_record_receipt_mismatch')
            if record.get('fs_div',scope)!=scope:check('V03','fail','api_record_scope_mismatch')
            check('V01','pass' if corp and identity['company']==corp==record.get('corp_code')==query.get('corp_code') else 'fail','original_api_research_company_code')
            check('V06',checks.get('V06',{}).get('status','pass'),'standard_id_original_label_and_hierarchy')
            # API의 회계연도/보고서 코드와 원문 표지의 역년 시작/끝이 함께 맞아야 한다.
            code_end={'11011':'12-31','11012':'06-30','11013':'03-31','11014':'09-30'}.get(query.get('reprt_code'))
            calendar_ok=(fiscal_month in (None,12) and
                         identity['start']==str(query.get('bsns_year'))+'-01-01' and
                         identity['end']==str(query.get('bsns_year'))+'-'+str(code_end) and
                         record.get('bsns_year')==query.get('bsns_year') and record.get('reprt_code')==query.get('reprt_code'))
            check('V04','pass' if field and calendar_ok else 'not_checked',
                  'company_fiscal_month_conflict' if fiscal_month == 0 else
                  'non_calendar_fiscal_period_mapping_pending' if fiscal_month not in (None,12)
                  else 'calendar_cover_statement_specific_api_column')
            lexical=number(d['raw_value']) if d['raw_value'] is not None else None
            amounts=match_amount(d['exact_value'],number(record.get(field,'')) if field else None,d['unit'],self._declared_rounding(content,table))
            currency_ok=d['currency']==record.get('currency') and d['unit']==table['unit'] and d['currency']==table['currency']
            check('V05',amounts['status'] if currency_ok and lexical==d['exact_value'] else 'fail','currency_unit_sign_and_value')
            details.update(api_fragment_id=item['id'],api_source_id=item['source_id'],api_field=field,
                           account_id=record['account_id'],amount_comparison=amounts)
        mandatory=('V01','V02','V03','V04','V05','V06','V07','V13','V18')
        statuses=[checks[k]['status'] for k in mandatory]
        status='fail' if 'fail' in statuses else ('pass' if all(s=='pass' for s in statuses) else 'not_checked')
        details['numeric_state']={'pass':'supported','fail':'conflict','not_checked':'unverified'}[status]
        details['report_eligible']=False  # 주장/보고서 게이트는 5단계에서 별도 검증한다.
        return {'id':uuid4().hex,'fact_id':fact['id'],'rule_id':'numeric_fact','status':status,'data':details}

    def validate_run(self,run_id):
        run=self.store.get_run(run_id)
        with self.store.connection() as conn:
            basis=row_data(self.store._one(conn,'research_bases',run['basis_id']))
            company=json.loads(self.store._one(conn,'research_researches',basis['research_id'])['company_json'])
            sources=[row_data(row) for row in conn.execute('SELECT * FROM research_sources WHERE run_id=? ORDER BY id',(run_id,))]
        documents={};apis=[];manifests=[];facts=[];contents={};identities={};excluded=[];tables={}
        for source in sources:
            meta=source['metadata'];media=meta['media_type'];operation=meta['reporting_period'].get('operation')
            if operation in {'xbrl_member','taxonomy_reference'} or meta['kind']=='official_standard':
                excluded.append({'source_id':source['id'],'reason':'xbrl_checked_by_separate_concept_mapping_service'})
                continue
            if media not in ('application/xml','text/xml','text/html') and not (media=='application/json' and operation=='financials'):continue
            member=meta['reporting_period'].get('member_name');receipt=meta.get('receipt_number')
            if operation=='document_member' and member and receipt and Path(member).name not in {receipt+'.xml',receipt+'.html',receipt+'.htm'}:
                excluded.append({'source_id':source['id'],'reason':'supporting_attachment_not_primary_financial_document'})
                continue
            contents[source['id']]=self.store.source_content(source['id'])
            try:
                extracted=self.extraction.extract_source(source['id'])
            except ExtractionFailure as error:
                # 원문은 해시와 함께 보존하되, 안전 한도를 넘은 표에서 추정한 수치를 만들지 않는다.
                contents.pop(source['id'],None)
                excluded.append({'source_id':source['id'],'reason':'extraction_'+str(error)})
                continue
            manifest=self.extraction.fragment(extracted['manifest_id'])['data']['context']
            manifests.append({'source_id':source['id'],'content_hash':source['content_hash'],'manifest_id':extracted['manifest_id']})
            if media=='application/json':
                payload=json.loads(contents[source['id']])
                raw_records=(payload['response'] if 'response' in payload else payload)['list']
                for fragment_id in manifest['fragment_ids']:
                    item=self.extraction.fragment(fragment_id)
                    pointer=item['data']['location']['json_pointer']
                    if not re.fullmatch(r'(?:/response)?/list/[0-9]+',pointer):raise IntegrityFailure('invalid_api_pointer')
                    index=int(pointer.rsplit('/',1)[1])
                    if index>=len(raw_records) or raw_records[index]!=item['data']['context']['record']:
                        raise IntegrityFailure('api_record_original_mismatch')
                    apis.append(item)
            else:
                selected_ids=set(manifest['fragment_ids'])
                with self.store.connection() as conn:
                    rows=conn.execute('SELECT id,data_json FROM research_fragments WHERE source_id=?',(source['id'],)).fetchall()
                tables[source['id']]=[(row['id'],json.loads(row['data_json'])['context']) for row in rows
                    if row['id'] in selected_ids and json.loads(row['data_json'])['context'].get('kind')=='table']
                documents[source['id']]=source
                identities[source['id']]=self._document_identity(contents[source['id']])
                facts += [self.store.get_evidence(i) for i in manifest['candidate_ids']]
        audit=audit_disclosure_history(self.store,sources,company,basis,{s['metadata']['receipt_number'] for s in documents.values()})
        fingerprint=hashlib.sha256(encode({'policy':POLICY_VERSION,'basis_id':basis['id'],
            'sources':[(s['id'],s['content_hash'],s['metadata'].get('supersedes_id')) for s in sources],
            'manifests':manifests}).encode()).hexdigest()
        with self.store.connection() as conn:
            old=conn.execute('SELECT id FROM research_validation_batches WHERE run_id=? AND fingerprint=?',(run_id,fingerprint)).fetchone()
        if old:return self.batch(old[0]) | {'reused':True}
        superseded=set()
        fiscal_months=set()
        for source in sources:
            meta=source['metadata'];parent=meta.get('supersedes_id');receipt=meta.get('receipt_number') or ''
            if meta.get('reporting_period',{}).get('operation')=='company':
                try:
                    month=json.loads(self.store.source_content(source['id'])).get('acc_mt')
                    if isinstance(month,str) and re.fullmatch(r'0[1-9]|1[0-2]',month):
                        fiscal_months.add(int(month))
                except (ValueError,TypeError):
                    pass
            try:allowed=date.fromisoformat(receipt[:8])<=date.fromisoformat(basis['data']['public_cutoff'])
            except ValueError:allowed=False
            if parent and allowed:superseded.add(parent)
        fiscal_month=(0 if len(fiscal_months)>1 else next(iter(fiscal_months)) if fiscal_months else None)
        if run['trigger'].get('kind')=='general_company':
            planned=run['trigger'].get('collection_result',{}).get('fiscal_period',{}).get('month')
            fiscal_month=planned if planned in range(1,13) and fiscal_months=={planned} else 0
        validations=[self._validate_fact(f,documents[f['source']['id']],contents[f['source']['id']],
            identities[f['source']['id']],apis,basis,company,superseded,fiscal_month) for f in facts]
        notes=[]
        for sid,source in documents.items():
            notes+=reconcile_note_amounts(source,contents[sid],identities[sid],tables[sid],facts,validations,self._raw_check)
        compositions=[]
        for sid,source in documents.items():
            compositions+=reconcile_composition(source,contents[sid],identities[sid],tables[sid],facts,validations,notes,self._raw_check)
        composition_links=link_same_period_compositions(facts,validations,compositions)
        link_by_fact={fid:c for c in composition_links for ids in c['data']['account_fact_ids'].values() for fid in ids}
        compositions_by_fact={fid:c for c in compositions for ids in c['data']['account_fact_ids'].values() for fid in ids}
        for validation in validations:
            composition=compositions_by_fact.get(validation['fact_id'])
            link=link_by_fact.get(validation['fact_id'])
            if link:
                composition=next(c for c in compositions if c['id']==link['data']['composition_validation_id'])
            if composition:
                validation['data']['checks']['V12']={'status':'pass','reason':'three_caption_lease_separation_and_guarantee_exclusion_verified',
                                                    'composition_validation_id':composition['id']}
                if link:validation['data']['checks']['V12']['cross_source_link_validation_id']=link['id']
        detail_records=[]
        for sid,source in documents.items():
            detail_records+=collect_details(source,contents[sid],identities[sid],tables[sid],facts,validations,self._raw_check)
        # XBRL 매핑이 이 모듈의 계정 코드를 참조하므로 호출 시점에만 가져온다.
        from .xbrl_mapping import load_reports
        from .xbrl_borrowing import attach_evidence,native_usage_counts
        native_reports,native_report_failures=load_reports(self.store,sources)
        attach_evidence(detail_records,native_reports,{s['id']:s for s in sources})
        composition_review_records=composition_reviews(facts,validations,detail_records)
        additional_links=link_same_period_compositions(facts,validations,compositions,composition_review_records,detail_records,composition_links)
        composition_links+=additional_links
        for link in additional_links:
            for ids in link['data']['account_fact_ids'].values():
                for fid in ids:
                    validation=next(v for v in validations if v['fact_id']==fid)
                    validation['data']['checks']['V12']={'status':'pass','reason':'exact_components_with_independent_verification_levels',
                        'composition_validation_id':link['data']['composition_validation_id'],'cross_source_link_validation_id':link['id']}
        for review in composition_review_records:
            for validation in validations:
                if validation['fact_id'] in review['data']['affected_fact_ids']:
                    validation['data']['composition_verification_levels']=review['data']['verification_levels']
                    validation['data']['investigation_use_status']=review['data']['use_status']
        reviews_by_fact=defaultdict(list)
        for review in composition_review_records:
            if review['status']=='not_checked':
                for fid in review['data']['affected_fact_ids']:reviews_by_fact[fid].append(review['id'])
        for validation in validations:
            if validation['fact_id'] in reviews_by_fact:
                validation['data']['account_composition_review_ids']=reviews_by_fact[validation['fact_id']]
                validation['data']['investigation_use_status']='withheld_pending_component_confirmation'
        alias_contexts={}
        for fact in facts:
            validation=next((v for v in validations if v['fact_id']==fact['id']),None)
            if validation:
                context=prepare_context(fact,validation,contents[fact['source']['id']],self._raw_check)
                if context:
                    alias_contexts[fact['id']]=context
                    validation['data']['account_comparison_context']=context
        confirmed_sources={f['source']['id'] for f in facts if any(
            v['fact_id']==f['id'] and v['status']=='pass' for v in validations)}
        scope_changes=[];scope_observations=[]
        for sid,source in documents.items():
            if sid in confirmed_sources:
                scope_changes+=collect_disclosed_changes(source,contents[sid],identities[sid],tables[sid],self._raw_check)
                scope_observations+=observe_scope(source,contents[sid],identities[sid],tables[sid],facts,validations,self._raw_check)
        calculations=self._calculations(facts,validations,scope_changes,scope_observations,compositions,composition_links,alias_contexts)
        withhold_affected_calculations(calculations,composition_review_records)
        validations+=notes+scope_changes+scope_observations+compositions+composition_links+detail_records+composition_review_records
        for item in manifests:
            source=next(s for s in sources if s['id']==item['source_id'])
            if source['metadata']['media_type']!='application/json':continue
            receipt=source['metadata'].get('receipt_number')
            if not any(d['metadata'].get('receipt_number')==receipt for d in documents.values()):
                validations.append({'id':uuid4().hex,'fact_id':None,'rule_id':'V07_source_coverage','status':'not_checked',
                    'data':{'source_id':source['id'],'receipt_number':receipt,'reason':'matching_original_not_available'}})
        if not facts:
            validations.append({'id':uuid4().hex,'fact_id':None,'rule_id':'V07_fact_coverage','status':'not_checked',
                'data':{'reason':'no_original_candidates_available'}})
        coverage=[]
        for source_id,source in documents.items():
            end=identities[source_id]['end']
            for scope in ('OFS','CFS'):
                selected=[v for v in validations if v['fact_id'] and v['data']['scope']==scope
                    and next(f for f in facts if f['id']==v['fact_id'])['source']['id']==source_id
                    and (v['data']['period'].get('as_of')==end or
                         v['data']['period'].get('end')==end and v['data']['period'].get('start')==identities[source_id]['start'])]
                accounts={v['data']['account'] for v in selected if v['status']=='pass'}
                coverage.append({'source_id':source_id,'receipt_number':source['metadata'].get('receipt_number'),
                    'scope':scope,'current_period':end,'supported_accounts':sorted(accounts & set(ACCOUNT_RULES)),
                    'missing_or_unverified_accounts':sorted(set(ACCOUNT_RULES)-accounts)})
        rule_counts=defaultdict(Counter)
        for v in validations:
            for rule,check_result in v['data'].get('checks',{}).items():rule_counts[rule][check_result['status']]+=1
        summary={'balance_only_composition_review_count':sum(v['data']['use_status']=='account_balance_totals_only' for v in composition_review_records),'borrowing_detail_counts':dict(Counter(v['status'] for v in detail_records)),'resolved_account_composition_review_count':sum(v['status']=='pass' for v in composition_review_records),'open_account_composition_review_count':sum(v['status']=='not_checked' for v in composition_review_records),'cross_source_composition_link_count':len(composition_links),'borrowing_composition_count':len(compositions),'disclosed_consolidation_changes':{'version':SCOPE_CHANGE_VERSION,'detected_count':len(scope_changes),'validation_ids':[v['id'] for v in scope_changes],'absence_observation_count':sum(v['data']['absence_observed_within_reporting_period'] for v in scope_observations),'scope_observation_ids':[v['id'] for v in scope_observations]},'borrowing_note_amount_counts':dict(Counter(n['status'] for n in notes)),'disclosure_history_audit':audit,'excluded_sources':excluded,'rule_status_counts':{k:dict(v) for k,v in rule_counts.items()},'account_coverage':coverage,'validation_counts':dict(Counter(x['status'] for x in validations)),
                 'calculation_counts':dict(Counter(x['status'] for x in calculations)),
                 'fact_count':len(facts),'source_count':len(manifests),
                 'external_requests':0,'model_calls':0,'report_publication_available':False,
                 'limitations':['수집한 전체 공시 목록 안에서 정정 후보를 확인함' if audit['status']=='pass' else '전체 공시 목록의 정정 후보 검사가 미완료임',
                    '본표 소계·리스 분리와 보증 가산 금지, 연결 범위는 기록한 근거 범위에서만 확인함',
                    '사채 포함 표현의 구성 불명확성은 별도 검토로 남기며 관련 조사 사용·계산을 보류함',
                    '원문 미보유·미확인·미실행 검사를 통과로 집계하지 않음'],
                 'rule_scope':{'V01_V07_V13_V18':'per_fact_checks','V08_V11':'calculations',
                    'V12':'per_group_evidence' if compositions or composition_links else 'not_checked','latest_correction_history':'checked' if audit['status']=='pass' else 'not_checked','consolidation_scope_changes':'per_comparison_evidence' if scope_observations else 'not_checked','V14_V17_V19':'not_checked_report_stage',
                    'V20':'immutable_versions','V21':'local_only_no_model','V22':'no_forced_pass'}}
        summary['native_borrowing_evidence']=native_usage_counts(detail_records)
        summary['native_xbrl_report_failures']=native_report_failures
        batch_id=uuid4().hex
        reused=False
        with self.store.connection(write=True) as conn:
            old=conn.execute('SELECT id FROM research_validation_batches WHERE run_id=? AND fingerprint=?',(run_id,fingerprint)).fetchone()
            if old:batch_id=old[0];reused=True
            else:
                conn.execute('INSERT INTO research_validation_batches VALUES (?,?,?,?,?,?,?)',
                    (batch_id,run_id,fingerprint,POLICY_VERSION,safe_payload(manifests),safe_payload(summary),now()))
                for v in validations:
                    conn.execute('INSERT INTO research_validations VALUES (?,?,?,?,?,?,?)',
                        (v['id'],batch_id,v['fact_id'],v['rule_id'],v['status'],safe_payload(v['data']),now()))
                for c in calculations:
                    conn.execute('INSERT INTO research_calculations VALUES (?,?,?,?,?,?)',
                        (c['id'],batch_id,c['formula_version'],c['status'],safe_payload(c['data']),now()))
                    for role,v in c['inputs'].items():
                        conn.execute('INSERT INTO research_calculation_inputs VALUES (?,?,?,?)',(c['id'],role,v['id'],v['fact_id']))
                self.store._event(conn,basis['research_id'],basis['id'],run_id,'numeric_validation_completed',
                    {'batch_id':batch_id,'policy_version':POLICY_VERSION,**summary})
        return self.batch(batch_id) | {'reused':reused}

    def _calculations(self,facts,validations,scope_changes=(),scope_observations=(),compositions=(),composition_links=(),alias_contexts=None):
        alias_contexts=alias_contexts or {}
        composition_by_group={(c['data']['source_id'],c['data']['scope'],c['data']['period']['as_of']):c for c in compositions if c['status']=='pass'}
        composition_by_group.update({(c['data']['source_id'],c['data']['scope'],c['data']['period']['as_of']):c for c in composition_links})
        facts_by_id={f['id']:f for f in facts};groups=defaultdict(lambda:defaultdict(list));results=[]
        raw_groups=defaultdict(set)
        for fact in facts:
            d=fact['data'];key=(fact['source']['id'],d['scope'],encode(period_key(d['period'])),d['currency'])
            raw_groups[key].add(d['account'])
        for v in validations:
            if v['status']!='pass' or v['data'].get('amount_comparison',{}).get('match')!='exact':continue
            f=facts_by_id[v['fact_id']];d=f['data']
            # 같은 원문·범위·기간의 입력만 모은다.
            key=(f['source']['id'],d['scope'],encode(period_key(d['period'])),d['currency'])
            groups[key][d['account']].append(v)
        def add(formula,inputs,kind='sum',expected=None,limitations=None):
            data={'formula':formula,'kind':kind,'inputs':{},'limitations':limitations or []}
            for role,v in inputs.items():
                f=facts_by_id[v['fact_id']]
                data['inputs'][role]={'fact_id':f['id'],'validation_id':v['id'],
                    'value':v['data']['amount_comparison']['original_normalized'],
                    'scope':f['data']['scope'],'period':f['data']['period'],'currency':f['data']['currency'],'account':f['data']['account']}
            values={k:x['value'] for k,x in data['inputs'].items()}
            if kind=='difference':value=exact_sum([values['current'],str(decimal(values['previous']).copy_negate())])
            else:value=exact_sum([x for k,x in values.items() if k!='expected'])
            data['result']=str(value)
            status='pass'
            if expected is not None:
                data['expected']=values['expected'];data['difference']=str(exact_sum([str(value),str(decimal(values['expected']).copy_negate())]))
                status='pass' if value==decimal(values['expected']) else 'fail'
            results.append({'id':uuid4().hex,'formula_version':POLICY_VERSION+':'+formula,
                            'status':status,'data':data,'inputs':inputs})
        for key,raw_accounts in raw_groups.items():
            accounts=groups.get(key,{})
            def unavailable(formula,names):
                results.append({'id':uuid4().hex,'formula_version':POLICY_VERSION+':'+formula,
                    'status':'not_checked','data':{'reason':'missing_ambiguous_or_unverified_inputs',
                    'source_id':key[0],'scope':key[1],'period':json.loads(key[2]),
                    'missing_or_unverified_inputs':[n for n in names if len(accounts.get(n,[]))!=1]},'inputs':{}})
            def selected(*names):return all(len(accounts.get(n,[]))==1 for n in names)
            def get(*names):return {n:accounts[n][0] for n in names}
            if selected('assets','liabilities','equity'):
                add('V08_assets_equals_liabilities_plus_equity',
                    {'liabilities':accounts['liabilities'][0],'equity':accounts['equity'][0],'expected':accounts['assets'][0]},expected=True)
            elif raw_accounts & {'assets','liabilities','equity'}:
                unavailable('V08_assets_equals_liabilities_plus_equity',('assets','liabilities','equity'))
            if selected(*sorted(BORROWINGS)):
                add('V12_three_borrowing_captions_subtotal',get(*sorted(BORROWINGS)),limitations=[
                    '세 본표 계정의 산술 소계. 주석 구성·리스·보증 중복 미검증으로 전체 차입 확정 합계가 아님'])
                results[-1]['status']='not_checked'
                results[-1]['data']['deferred_reason']='borrowing_note_composition_required'
                add('V12_current_borrowing_captions_subtotal',get('short_term_borrowings','current_long_term_borrowings'),
                    limitations=['유동 차입 두 본표 항목의 산술 소계. 주석 구성·중복 검증 전'])
                results[-1]['status']='not_checked'
                results[-1]['data']['deferred_reason']='borrowing_note_composition_required'
                composition=composition_by_group.get((key[0],key[1],json.loads(key[2]).get('as_of')))
                if composition:
                    for calculation in results[-2:]:
                        calculation['status']='pass'
                        calculation['data'].pop('deferred_reason',None)
                        if composition.get('rule_id')=='V12_cross_source_composition_link':
                            calculation['data']['composition_validation_id']=composition['data']['composition_validation_id']
                            calculation['data']['cross_source_link_validation_id']=composition['id']
                        else:calculation['data']['composition_validation_id']=composition['id']
                        calculation['data']['included_accounts']=list(calculation['data']['inputs'])
                        calculation['data']['lease_included']=False
                        calculation['data']['guarantee_included']=False
                        calculation['data']['total_financial_liabilities']=False
                        calculation['data']['limitations']=['본표차입 계정의 소계. 리스 분리 근거 확인·보증 가산 금지. 전체 금융부채나 보증 총액이 아님']
            elif raw_accounts & BORROWINGS:
                unavailable('V12_three_borrowing_captions_subtotal',sorted(BORROWINGS))
            cf_names=('cash_begin','operating_cashflow','investing_cashflow','financing_cashflow','cash_fx','cash_end')
            if selected(*cf_names):
                inputs=get(*cf_names[:-1]);inputs['expected']=accounts['cash_end'][0]
                add('V10_cash_rollforward',inputs,expected=True,limitations=[
                    '표시된 영업·투자·재무·환율 효과만 합산. 차이가 있으면 조정항을 만들지 않음'])
            elif raw_accounts & set(cf_names):
                unavailable('V10_cash_rollforward',cf_names)
            if selected('cash_end'):
                period=json.loads(key[2]);end=period.get('end')
                balance_key=(key[0],key[1],encode({'kind':'instant','as_of':end}),key[3])
                cash=groups.get(balance_key,{}).get('cash',[])
                cross_source=False
                if not cash:
                    candidates=[v for other_key,other in groups.items()
                        if other_key[1:]==balance_key[1:] for v in other.get('cash',[])]
                    values={decimal(v['data']['amount_comparison']['original_normalized']) for v in candidates}
                    if len(values)==1 and candidates:
                        cash=[candidates[0]];cross_source=True
                if len(cash)==1:
                    add('V09_cf_end_equals_bs_cash',{'cash_end':accounts['cash_end'][0],'expected':cash[0]},expected=True)
                    results[-1]['data']['balance_source_selection']='other_validated_receipt_same_date' if cross_source else 'same_document'
                else:unavailable('V09_cf_end_equals_bs_cash',('cash_end','cash_at_same_end_date'))
            elif raw_accounts & set(cf_names):
                unavailable('V09_cf_end_equals_bs_cash',('cash_end','cash_at_same_end_date'))
        # 여러 공시의 같은 시점 값을 임의로 최신 선택하지 않는다. 다른 값이면 비교 보류.
        unique=defaultdict(list)
        for key,accounts in list(groups.items()):
            for account,items in accounts.items():
                if account not in ACCOUNT_RULES:continue
                for v in items:
                    f=facts_by_id[v['fact_id']]
                    unique[(key[1],account,key[2],key[3])].append((f,v))
        comparable=[]
        for items in unique.values():
            values={decimal(v['data']['amount_comparison']['original_normalized']) for f,v in items}
            if len(values)==1:comparable.append(items)
            else:
                results.append({'id':uuid4().hex,'formula_version':POLICY_VERSION+':V13_cross_source_conflict',
                    'status':'fail','data':{'reason':'same_scope_account_period_different_source_values',
                        'fact_ids':[f['id'] for f,v in items]},'inputs':{str(i):v for i,(f,v) in enumerate(items)}})
        for current_items in comparable:
            for previous_items in comparable:
                pairs=[]
                for f,v in current_items:
                    for p,w in previous_items:
                        equivalence=verified_alias_pair(alias_contexts.get(f['id']),alias_contexts.get(p['id']))
                        policy=comparison_policy(f['data'],p['data'],account_equivalent=equivalence is not None)
                        if policy in ('same_period','previous_year_end','prior_same_date_supplementary'):
                            pairs.append((f,v,p,w,policy,equivalence))
                if not pairs:continue
                # 같은 값의 중복 근거를 모두 보존한다. 대표 입력의 정렬은 최신/정정 판정이 아니다.
                def reference_order(pair):
                    f,v,p,w,policy,equivalence=pair
                    return (equivalence is not None,normalize_label(f['data']['original_account']),f['source'].get('content_hash',''),
                            p['source'].get('content_hash',''),f.get('fragment',{}).get('data',{}).get('location',{}).get('byte_start',0))
                f,v,p,w,policy,equivalence=sorted(pairs,key=reference_order)[0]
                add('V11_change_'+policy,{'current':v,'previous':w},kind='difference',limitations=[
                    '기간·범위·통화·원문 계정 수준 비교. 연결 범위 변경·재작성 의미는 별도 확인 필요'])
                if equivalence:results[-1]['data']['account_equivalence']=equivalence
                results[-1]['data']['comparison_policy']=policy
                results[-1]['data']['supporting_fact_ids']={
                    'current':[f['id'] for f,v in current_items], 'previous':[p['id'] for p,w in previous_items]}
                if f['data']['scope']=='CFS':
                    results[-1]['status']='not_checked'
                    results[-1]['data']['deferred_reason']='consolidation_scope_review_required'
                    results[-1]['data']['limitations'].append('연결 범위 변경을 확인하기 전 연결 증감 해석 보류')
                    detected=blocking_changes(results[-1]['data']['inputs'],scope_changes)
                    if detected:
                        data=results[-1]['data']
                        data['scope_review']={'status':'checked_change_detected',
                            'validation_ids':[item['id'] for item in detected],
                            'comparison_status':'withheld','same_scope_adjustment_available':False}
                        data['deferred_reason']='disclosed_consolidation_change_comparison_withheld'
                        data['unverified_arithmetic_result']=data.pop('result')
                        data['limitations'].append('공시된 변동 기간과 비교 구간이 겹침. 정확한 편입일·조정 근거 없이 동일 범위 증감으로 사용하지 않음')
                    else:
                        review=unchanged_scope_support(results[-1]['data']['inputs'],scope_observations)
                        if review:
                            results[-1]['status']='pass'
                            results[-1]['data']['scope_review']=review
                            results[-1]['data'].pop('deferred_reason',None)
                            results[-1]['data']['limitations']=['기간·범위·통화·계정 및 확보한 공시 범위의 연결 변동을 확인함. 변동 미발견을 모든 미공개 사실의 부재로 보증하지 않음']
        return results

    def batch(self,batch_id):
        with self.store.connection() as conn:
            result=row_data(self.store._one(conn,'research_validation_batches',batch_id))
            result['validations']=[row_data(r) for r in conn.execute('SELECT * FROM research_validations WHERE batch_id=? ORDER BY rowid',(batch_id,))]
            result['calculations']=[row_data(r) for r in conn.execute('SELECT * FROM research_calculations WHERE batch_id=? ORDER BY rowid',(batch_id,))]
        return result

    def history(self,run_id):
        self.store.get_run(run_id)
        with self.store.connection() as conn:
            return [row_data(r) for r in conn.execute('SELECT * FROM research_validation_batches WHERE run_id=? ORDER BY rowid',(run_id,))]
