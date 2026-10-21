"""차입 주석의 명시된 합계 열·기간·재분류 산술 대조. 전체 부채 범위 판정은 별도다."""
from collections import defaultdict
from decimal import Decimal,localcontext,Inexact
from uuid import uuid4
from .document_extractor import normalize_label,number
from .note_context import NoteContext

VERSION='borrowing-note-amounts-1.2'
SCALES={'원':Decimal(1),'천원':Decimal(1000),'백만원':Decimal(1000000),'억원':Decimal(100000000)}
ACCOUNTS=('short_term_borrowings','current_long_term_borrowings','long_term_borrowings')
ALIASES={
 'short_term_borrowings':{'단기차입금','유동차입금','유동차입금(사채포함)'},
 'current_long_term_borrowings':{'유동성장기부채','유동성장기차입금','비유동차입금의유동성대체부분','비유동차입금(사채포함)의유동성대체부분'},
 'long_term_borrowings':{'비유동차입금의비유동성부분','비유동차입금(사채포함)의비유동성부분','장기차입금'},
}


def reconcile_note_amounts(source,content,identity,tables,facts,validations,raw_check):
    supported={v['fact_id']:v for v in validations if v.get('fact_id') and v['status']=='pass'}
    groups=defaultdict(lambda:defaultdict(list));results=[]
    for f in facts:
        d=f['data']
        if f['source']['id']==source['id'] and d['account'] in ACCOUNTS and f['id'] in supported:
            groups[(d['scope'],d['period'].get('as_of'),d['currency'])][d['account']].append(f)
    context=NoteContext(source,content,identity,facts,supported,raw_check)
    end=identity.get('end');previous=str(int(end[:4])-1)+'-12-31' if end else None
    current_labels={'당기말','당반기말','당분기말'};previous_labels={'전기말'}
    def date_label(label):
        label=normalize_label(label)
        return end if label in current_labels else (previous if label in previous_labels else None)
    for (scope,as_of,currency),main in groups.items():
        matches=defaultdict(list);equations=[]
        for fragment_id,table in tables:
            if (table.get('statement_hint') is not None or table.get('currency')!=currency
                    or table.get('unit') not in SCALES or not table['location'].get('parser_text_matches_raw')
                    or not raw_check(content,table['location'])
                    or not all(raw_check(content,u['location']) for u in table.get('unit_candidates',[]))):continue
            scope_proof=context.scope(table)
            if not scope_proof or scope_proof['scope']!=scope:continue
            row_labels=[normalize_label(r['cells'][0]['text']) for r in table['rows'] if r['cells']]
            balance_table=any(label in set().union(*ALIASES.values()) for label in row_labels)
            preamble_proofs=[context.date(x['text'],allow_balance_label=balance_table) for x in table.get('preamble',[])
                             if raw_check(content,x['location'],x['text'])]
            preamble_proofs=[p for p in preamble_proofs if p]
            dates={p['as_of'] for p in preamble_proofs}
            preamble_date=next(iter(dates)) if len(dates)==1 else None
            manual_headers=[r for r in table['rows'] if r['cells'] and normalize_label(r['cells'][0]['text'])=='구분'
                            and any(normalize_label(c['text']) in {'기말','당기말','당반기말','전기말'} for c in r['cells'])]
            value_kind_headers=[r for r in table['rows'] if r['cells'] and all(
                normalize_label(c['text']) in {'장부금액','공정가치'} for c in r['cells'])]
            has_reclassification=any('유동성대체부분' in x for x in row_labels)
            local=defaultdict(list)
            for row in table['rows']:
                label=next((c for c in row['cells'] if c['column']==1),None)
                if not label or not raw_check(content,label['location'],label['text']):continue
                name=normalize_label(label['text'])
                account=next((a for a,aliases in ALIASES.items() if name in aliases),None)
                if name=='장기차입금' and has_reclassification:account='gross_long_term'
                if account is None:continue
                for cell in row['cells']:
                    value=number(cell['text'])
                    if cell['column']==1 or value is None or not raw_check(content,cell['location'],cell['text']):continue
                    headers=list(cell['column_headers'])
                    for header_row in manual_headers+value_kind_headers:
                        if header_row['row']>=row['row']:continue
                        for h in header_row['cells']:
                            if h['column']<=cell['column']<h['column']+h['colspan'] and h['text']:
                                headers.append({'text':h['text'],'location':h['location']})
                    if not all(raw_check(content,h['location'],h['text']) for h in headers):continue
                    if any(normalize_label(h['text'])=='공정가치' for h in headers):continue
                    column_dates={date_label(h['text']) for h in headers};column_dates.discard(None)
                    end_column=any(normalize_label(h['text']) in {'기말','당기말','당반기말','전기말'} for h in headers)
                    if manual_headers and end_column and preamble_date:
                        amount_date=preamble_date
                    else:amount_date=next(iter(column_dates)) if len(column_dates)==1 else preamble_date
                    # 상세 은행 열이 아닌, 원문이 명시한 날짜 열 또는 전체 차입 합계 열만 사용한다.
                    total_column=any(normalize_label(h['text'])=='차입금명칭합계' for h in headers)
                    if amount_date!=as_of or not (column_dates or total_column or manual_headers and end_column):continue
                    with localcontext() as ctx:
                        ctx.prec=4096;ctx.traps[Inexact]=True;exact=Decimal(value)*SCALES[table['unit']]
                    local[account].append({'value':exact,'fragment_id':fragment_id,'location':cell['location'],
                                          'row_label':label['text'],'headers':headers,'unit':table['unit'],
                                          'scope_proof':scope_proof,'date_proofs':preamble_proofs,
                                          'resolved_as_of':amount_date})
            gross=local.get('gross_long_term',[]);current=local.get('current_long_term_borrowings',[]);noncurrent=local.get('long_term_borrowings',[])
            deduction_ok=False
            if len(gross)==len(current)==len(noncurrent)==1 and current[0]['value']<=0:
                with localcontext() as ctx:
                    ctx.prec=4096;ctx.traps[Inexact]=True
                    deduction_ok=gross[0]['value']+current[0]['value']==noncurrent[0]['value']
                equations.append({'fragment_id':fragment_id,'status':'pass' if deduction_ok else 'not_checked',
                    'formula':'gross_long_term + current_reclassification_deduction = noncurrent',
                    'inputs':[str(gross[0]['value']),str(current[0]['value'])],'result':str(noncurrent[0]['value'])})
            for account,items in local.items():
                if account=='gross_long_term':continue
                for item in items:
                    if item['value']<0:
                        if account!='current_long_term_borrowings' or not deduction_ok:continue
                        item={**item,'value':item['value'].copy_abs(),'transformation':'verified_deduction_to_current_balance'}
                    matches[account].append(item)
        checks={}
        for account in ACCOUNTS:
            values={x['value'] for x in matches[account]};expected=main.get(account,[])
            passed=(len(expected)==1 and len(values)==1 and next(iter(values))==Decimal(
                supported[expected[0]['id']]['data']['amount_comparison']['original_normalized']))
            checks[account]={'status':'pass' if passed else 'not_checked',
                'reason':'exact_note_column_match' if passed else 'note_evidence_missing_ambiguous_or_composition_differs',
                'fact_ids':[f['id'] for f in expected],
                'note_cells':[{**x,'value':str(x['value'])} for x in matches[account]]}
        passed=all(x['status']=='pass' for x in checks.values())
        results.append({'id':uuid4().hex,'fact_id':None,'rule_id':'V12_note_amounts','status':'pass' if passed else 'not_checked',
            'data':{'version':VERSION,'reason':'exact_note_amounts_reconciled' if passed else 'borrowing_note_evidence_incomplete',
                    'source_id':source['id'],'scope':scope,'period':{'kind':'instant','as_of':as_of},
                    'account_checks':checks,'reclassification_checks':equations,
                    'limitations':['금액 대조이며 리스·보증·전체 금융부채 구성 검증을 완료했다는 뜻이 아님']}})
    return results
