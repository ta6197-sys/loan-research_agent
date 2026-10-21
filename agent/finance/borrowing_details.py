"""원문 은행별 차입 구성과 합계·재분류를 분리해 대조한다. 결측값은 보완하지 않는다."""
from collections import defaultdict
from decimal import Decimal, localcontext, Inexact
from uuid import uuid4
from .document_extractor import normalize_label, number
from .note_checks import SCALES, ACCOUNTS
from .note_context import NoteContext

VERSION='borrowing-details-1.0'
RULE='V12_borrowing_details'


def exact_total(values):
    with localcontext() as ctx:
        ctx.prec=4096;ctx.traps[Inexact]=True
        return str(sum((Decimal(v) for v in values),Decimal(0)))


def amount(cell,unit):
    value=number(cell['text'])
    if value is None:return None
    with localcontext() as ctx:
        ctx.prec=4096;ctx.traps[Inexact]=True
        return str(Decimal(value)*SCALES[unit])


def item(name_cell,value_cell,unit,name=None):
    return {'name':name or name_cell['text'],'name_location':name_cell['location'],
            'amount':amount(value_cell,unit),'amount_location':value_cell['location']}


def vector(components):
    return sorted((normalize_label(c['name']),Decimal(c['amount'])) for c in components)


def horizontal(table):
    rows=table['rows'];headers=[c for r in rows[:4] for c in r['cells'] if normalize_label(c['text'])=='차입금명칭합계']
    if len(headers)!=1:return []
    column=headers[0]['column']
    header_row=next(r['row'] for r in rows if headers[0] in r['cells'])
    name_rows=[r for r in rows if r['row']==header_row+1]
    if len(name_rows)!=1:return []
    names=[c for c in name_rows[0]['cells'] if c['text'] and 1<c['column']<column];output=[]
    covered=[i for c in names for i in range(c['column'],c['column']+c['colspan'])]
    if covered!=list(range(2,column)):return []
    for row in rows:
        label=next((c for c in row['cells'] if c['column']==1),None)
        if not label:continue
        name=normalize_label(label['text'])
        family='short' if name in {'단기차입금','유동차입금','유동차입금(사채포함)'} else ('long' if name=='장기차입금' else None)
        if family is None:continue
        total=next((c for c in row['cells'] if c['column']==column),None)
        if not total or amount(total,table['unit']) is None:continue
        components=[];missing=[]
        for group in names:
            cells=[c for c in row['cells'] if group['column']<=c['column']<group['column']+group['colspan']]
            numeric=[c for c in cells if number(c['text']) is not None]
            if len(numeric)!=1 or any(c['text'] for c in cells if c not in numeric):
                missing.append({'name':group['text'],'reason':'missing_or_ambiguous_amount','locations':[c['location'] for c in cells]});continue
            components.append(item(group,numeric[0],table['unit']))
        classification={}
        if family=='long':
            for key,aliases in {'current':{'비유동차입금의유동성대체부분','비유동차입금(사채포함)의유동성대체부분'},
                                'noncurrent':{'비유동차입금의비유동성부분','비유동차입금(사채포함)의비유동성부분'}}.items():
                matches=[c for r in rows if r['cells'] and normalize_label(r['cells'][0]['text']) in aliases
                         for c in r['cells'] if c['column']==column and amount(c,table['unit']) is not None]
                if len(matches)==1:classification[key]={'amount':amount(matches[0],table['unit']),'location':matches[0]['location']}
        output.append({'family':family,'components':components,'unresolved':missing,'displayed_total':amount(total,table['unit']),
                       'total_location':total['location'],'classification':classification,'column_headers':[headers[0]]})
    return output


def vertical(table):
    rows=table['rows'];headers=[r for r in rows if any(normalize_label(c['text'])=='차입처' for c in r['cells'])
                              and any(normalize_label(c['text'])=='내역' for c in r['cells'])]
    if len(headers)!=1:return []
    header=headers[0];output=[]
    for date_header in header['cells']:
        if normalize_label(date_header['text']) not in {'당기말','당반기말','당분기말','전기말'}:continue
        column=date_header['column'];components=[];missing=[];totals=[];classification={};lender=None;until=0
        for row in rows:
            if row['row']<=header['row']:continue
            label=next((c for c in row['cells'] if c['column']==1),None)
            value=next((c for c in row['cells'] if c['column']==column),None)
            label_text=normalize_label(label['text']) if label else ''
            if label_text in {'합계','소계','차감:유동성장기부채','차감계'}:
                if not value:continue
                parsed=amount(value,table['unit'])
                if label_text in {'합계','소계'}:totals.append((label_text,parsed,value['location']))
                else:classification['current' if label_text.startswith('차감:') else 'noncurrent']={'amount':parsed,'location':value['location']}
                continue
            purpose=next((c for c in row['cells'] if c['column']==2),None)
            if label and label['colspan']==1 and label['text']:
                lender=label;until=row['row']+label['rowspan']
            if not purpose and not value:continue
            if not lender or row['row']>=until or not purpose or not purpose['text'] or not value:
                missing.append({'reason':'unresolved_lender_or_purpose','row':row['row']});continue
            if amount(value,table['unit']) is None:
                missing.append({'name':lender['text']+' '+purpose['text'],'reason':'missing_amount','location':value['location']});continue
            component=item(lender,value,table['unit'],lender['text']+' '+purpose['text'])
            component['purpose_location']=purpose['location'];components.append(component)
        if len(totals)!=1 or totals[0][1] is None:continue
        kind,total,loc=totals[0]
        output.append({'family':'long' if kind=='소계' else 'short','components':components,'unresolved':missing,
                       'displayed_total':total,'total_location':loc,'classification':classification,'column_headers':[date_header]})
    return output


def assess_detail(data):
    """저장된 근거 금액을 다시 계산한다. 완료 플래그만으로 통과시키지 않는다."""
    components=data.get('components',[]);names=[normalize_label(c['name']) for c in components]
    complete=bool(components) and not data.get('unresolved') and len(set(names))==len(names) and all(Decimal(c['amount'])>=0 for c in components)
    summed=exact_total(c['amount'] for c in components)
    total=data['displayed_total'];accounts=data['account_amounts']
    anchor=(accounts['short_term_borrowings'] if data['family']=='short' else exact_total(accounts[a] for a in ACCOUNTS[1:]))
    status='not_checked' if not complete else ('pass' if Decimal(summed)==Decimal(total)==Decimal(anchor) else 'fail')
    reclassification={'status':'not_applicable','reason':'short_term_account_has_no_long_term_reclassification'}
    if data['family']=='long':
        c=data.get('classification',{});values=[c.get(k,{}).get('amount') for k in ('current','noncurrent')]
        state='not_checked'
        if all(v is not None for v in values):
            current,noncurrent=map(Decimal,values)
            valid=current<=0 and noncurrent>=0 and Decimal(total)+current==noncurrent
            valid=valid and -current==Decimal(accounts[ACCOUNTS[1]]) and noncurrent==Decimal(accounts[ACCOUNTS[2]])
            state='pass' if valid else 'fail'
        reclassification={'status':state,'reason':'gross_plus_explicit_current_deduction_equals_noncurrent',
                          'gross':total,'classification':c,'bank_allocation_verified':False}
    return {'detail_total':{'status':status,'explicit_sum':summed,'displayed_total':total,'main_statement_anchor':anchor},
            'reclassification':reclassification}


def collect_details(source,content,identity,tables,facts,validations,raw_check):
    supported={v['fact_id']:v for v in validations if v.get('fact_id') and v.get('status')=='pass'}
    context=NoteContext(source,content,identity,facts,supported,raw_check);groups=defaultdict(lambda:defaultdict(list))
    for fact in facts:
        d=fact['data']
        if fact['id'] in supported and fact['source']['id']==source['id'] and d['account'] in ACCOUNTS:
            groups[(d['scope'],d['period'].get('as_of'),d['currency'])][d['account']].append(fact)
    results=[]
    for fragment_id,table in tables:
        if table.get('statement_hint') is not None or table.get('unit') not in SCALES:continue
        if not table['location'].get('parser_text_matches_raw') or not raw_check(content,table['location']):continue
        if not table.get('unit_candidates') or not all(raw_check(content,c['location']) for c in table['unit_candidates']):continue
        if not all(raw_check(content,c['location'],c['text']) for r in table['rows'] for c in r['cells']):continue
        scope=context.scope(table)
        if not scope:continue
        for observation in horizontal(table)+vertical(table):
            dates=[context.date(h['text']) for h in observation['column_headers']];dates=[d for d in dates if d]
            if not dates:dates=[context.date(p['text'],allow_balance_label=True) for p in table.get('preamble',[]) if raw_check(content,p['location'],p['text'])];dates=[d for d in dates if d]
            ends={d['as_of'] for d in dates}
            if len(ends)!=1:continue
            as_of=next(iter(ends));accounts=groups.get((scope['scope'],as_of,table.get('currency')),{});required=ACCOUNTS[:1] if observation['family']=='short' else ACCOUNTS[1:]
            if not all(len(accounts.get(a,[]))==1 for a in required):continue
            selected=[accounts[a][0] for a in required]
            data={**observation,'version':VERSION,'source_id':source['id'],'content_hash':source['content_hash'],
                  'company_code':selected[0]['company']['corp_code'],'run_id':selected[0]['run_id'],
                  'scope':scope['scope'],'period':{'kind':'instant','as_of':as_of},'currency':table['currency'],
                  'scope_proof':scope,'date_proofs':dates,'unit':table['unit'],'unit_evidence':table['unit_candidates'],
                  'fragment_id':fragment_id,'table_location':table['location'],
                  'account_fact_ids':{a:accounts[a][0]['id'] for a in required},
                  'account_amounts':{a:supported[accounts[a][0]['id']]['data']['amount_comparison']['original_normalized'] for a in required}}
            data['checks']=assess_detail(data)
            statuses=[v['status'] for v in data['checks'].values()]
            status='fail' if 'fail' in statuses else ('pass' if data['checks']['detail_total']['status']=='pass' else 'not_checked')
            results.append({'id':uuid4().hex,'fact_id':None,'rule_id':RULE,'status':status,'data':data})
    return results
