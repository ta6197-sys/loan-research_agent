"""본표 차입 3계정 소계의 구성·리스 분리와 보증 중복 가산 금지.

소계는 전체 금융부채나 보증 노출 총액이 아니다. 보증 미확인을 0으로 바꾸지 않는다.
"""
from decimal import Decimal, localcontext, Inexact
from uuid import uuid4
from .document_extractor import normalize_label, number
from .note_checks import ACCOUNTS,SCALES
from .note_context import NoteContext

VERSION='borrowing-composition-1.2'
RULE='V12_borrowing_composition'


def total(values):
    with localcontext() as ctx:
        ctx.prec=4096;ctx.traps[Inexact]=True
        return sum((Decimal(x) for x in values),Decimal(0))


def scaled(value,unit):
    with localcontext() as ctx:
        ctx.prec=4096;ctx.traps[Inexact]=True
        return str(Decimal(value)*SCALES[unit])


def verify_three_caption_subtotal(amounts,lease_exclusion_proof):
    if set(amounts)!=set(ACCOUNTS):raise ValueError('only_three_borrowing_accounts_allowed')
    if not lease_exclusion_proof:raise ValueError('lease_composition_evidence_required')
    return {'amount':str(total(amounts.values())),'components':amounts,'lease_included':False,
            'guarantee_included':False,'guarantee_amount_status':'not_checked',
            'label':'본표 차입 3계정 소계','total_financial_liabilities':False,
            'limitation':'리스는 별도 근거로 분리. 보증 잔액·보증 한도·전체 금융부채를 확인한 합계가 아니며 보증을 0으로 처리하지 않음'}


def reconcile_composition(source,content,identity,tables,facts,validations,notes,raw_check):
    supported={v['fact_id']:v for v in validations if v.get('fact_id') and v['status']=='pass'}
    context=NoteContext(source,content,identity,facts,supported,raw_check)
    by_fragment=dict(tables);results=[]
    for note in notes:
        nd=note['data']
        if nd['source_id']!=source['id'] or note['status']!='pass':continue
        scope=nd['scope'];as_of=nd['period']['as_of'];checks=nd['account_checks']
        cells={a:checks[a]['note_cells'] for a in ACCOUNTS}
        amounts={a:cells[a][0]['value'] for a in ACCOUNTS}
        lease_proof=None
        shared=set.intersection(*(set(c['fragment_id'] for c in cells[a]) for a in ACCOUNTS))
        for fragment in shared:
            table=by_fragment[fragment]
            for column in {c['column'] for r in table['rows'] for c in r['cells']}:
                selected={a:[c for c in cells[a] if c['fragment_id']==fragment and any(
                    original['column']==column and original['location']['byte_start']==c['location']['byte_start']
                    for r in table['rows'] for original in r['cells'])] for a in ACCOUNTS}
                if not all(selected.values()):continue
                lease=[];all_debt=[]
                for row in table['rows']:
                    label=next((c for c in row['cells'] if c['column']==1),None)
                    cell=next((c for c in row['cells'] if c['column']==column),None)
                    if not label or not cell or not all(raw_check(content,c['location'],c['text']) for c in (label,cell)):continue
                    value=number(cell['text'])
                    if value is None:continue
                    item={'value':scaled(value,table['unit']),
                          'label':label['text'],'location':cell['location'],'label_location':label['location']}
                    name=normalize_label(label['text'])
                    if name=='리스부채':lease.append(item)
                    if name=='합계':all_debt.append(item)
                if len(lease)==len(all_debt)==1 and total([*amounts.values(),lease[0]['value']])==Decimal(all_debt[0]['value']):
                    lease_proof={'basis':'same_column_three_borrowings_plus_separate_lease_equals_financing_liabilities',
                                 'fragment_id':fragment,'lease':lease[0],'financing_liabilities_total':all_debt[0]}
                    break
            if lease_proof:break
        bank_totals=all(any(any(normalize_label(h['text'])=='차입금명칭합계' for h in c['headers'])
                           for c in cells[a]) for a in ACCOUNTS)
        standard_ids={a:{supported[fid]['data'].get('account_id') for fid in checks[a]['fact_ids']} for a in ACCOUNTS}
        known={'short_term_borrowings':{'ifrs-full_ShorttermBorrowings'},
               'current_long_term_borrowings':{'ifrs-full_CurrentPortionOfLongtermBorrowings'},
               'long_term_borrowings':{'ifrs-full_LongtermBorrowings','dart_LongTermBorrowingsGross'}}
        standard_loans=all(ids and None not in ids and ids<=known[a] for a,ids in standard_ids.items())
        if lease_proof is None and (bank_totals or standard_loans):
            for fragment,table in tables:
                if not table['location'].get('parser_text_matches_raw') or not raw_check(content,table['location']):continue
                if table.get('currency')!='KRW' or table.get('unit') not in SCALES:continue
                proof=context.scope(table)
                if not proof or proof['scope']!=scope:continue
                if not all(raw_check(content,u['location']) for u in table.get('unit_candidates',[])):continue
                dates=[context.date(p['text'],allow_balance_label=True) for p in table.get('preamble',[])
                       if raw_check(content,p['location'],p['text'])]
                dates=[p for p in dates if p]
                preamble_dates={p['as_of'] for p in dates}
                values={}
                for row in table['rows']:
                    label=next((c for c in row['cells'] if c['column']==1),None)
                    if not label or not raw_check(content,label['location'],label['text']):continue
                    name=normalize_label(label['text'])
                    aliases={'리스부채합계':'리스부채합계','할인후리스부채':'리스부채합계',
                             '유동리스부채':'유동리스부채','유동':'유동리스부채',
                             '비유동리스부채':'비유동리스부채','비유동':'비유동리스부채'}
                    if name not in aliases:continue
                    key=aliases[name]
                    candidates=[]
                    for cell in row['cells']:
                        if cell['column']==1 or number(cell['text']) is None:continue
                        if not raw_check(content,cell['location'],cell['text']):continue
                        if not cell['column_headers'] or not all(raw_check(content,h['location'],h['text']) for h in cell['column_headers']):continue
                        header_dates=[context.date(h['text']) for h in cell['column_headers']]
                        header_dates=[d for d in header_dates if d]
                        direct={d['as_of'] for d in header_dates}
                        dated=(direct=={as_of} or not direct and preamble_dates=={as_of} and any(
                            normalize_label(h['text'])=='공시금액' for h in cell['column_headers']))
                        if not dated:continue
                        candidates.append({'value':scaled(number(cell['text']),table['unit']),
                                           'location':cell['location'],'label_location':label['location'],'label':label['text'],'headers':cell['column_headers'],'column_date_proofs':header_dates})
                    if len(candidates)==1:values[key]=candidates[0]
                if (set(values)=={'리스부채합계','유동리스부채','비유동리스부채'} and all(Decimal(x['value'])>=0 for x in values.values()) and total([
                    values['유동리스부채']['value'],values['비유동리스부채']['value']])==Decimal(values['리스부채합계']['value'])):
                    lease_proof={'basis':'bank_borrowing_totals_and_disjoint_reconciled_lease_balance_note' if bank_totals else 'standard_borrowing_note_accounts_and_disjoint_reconciled_lease_balance',
                                 'borrowings_standard_ids':{a:sorted(ids) for a,ids in standard_ids.items()},
                                 'fragment_id':fragment,'scope_proof':proof,'date_proofs':dates,'lease_balances':values}
                    break
        if lease_proof is None:continue
        subtotal=verify_three_caption_subtotal(amounts,lease_proof)
        results.append({'id':uuid4().hex,'fact_id':None,'rule_id':RULE,'status':'pass','data':{
            'version':VERSION,'source_id':source['id'],'scope':scope,'period':nd['period'],
            'note_validation_id':note['id'],'account_fact_ids':{a:checks[a]['fact_ids'] for a in ACCOUNTS},
            'lease_exclusion_proof':lease_proof,'subtotal':subtotal,
            'guarantee_policy':{'added_to_subtotal':False,'amount':None,'amount_status':'not_checked',
                                'limitation':'지급보증·금융보증부채·보증한도는 별도 항목. 미확인 보증을 0이나 차입 원금으로 가산하지 않음'}}})
    return results

LINK_RULE='V12_cross_source_composition_link'


def link_same_period_compositions(facts,validations,compositions,reviews=(),detail_records=(),existing_links=()):
    from .verification_levels import confirmed_caption_link
    """같은 기간·공식 코드·원문 계정명·금액의 확인된 구성만 다른 공시에 연결한다."""
    by_fact={f['id']:f for f in facts};by_validation={v['fact_id']:v for v in validations if v.get('fact_id') and v['status']=='pass'}
    covered={fid for c in compositions for ids in c['data']['account_fact_ids'].values() for fid in ids}
    covered.update(fid for link in existing_links for ids in link['data']['account_fact_ids'].values() for fid in ids)
    groups={}
    for fact in facts:
        d=fact['data']
        if d['account'] not in ACCOUNTS or fact['id'] in covered or fact['id'] not in by_validation:continue
        key=(fact['source']['id'],d['scope'],d['period'].get('as_of'),d['currency'])
        groups.setdefault(key,{}).setdefault(d['account'],[]).append(fact)
    results=[]
    for (sid,scope,as_of,currency),accounts in groups.items():
        if set(accounts)!=set(ACCOUNTS) or any(len(rows)!=1 for rows in accounts.values()):continue
        candidates=[]
        for composition in compositions:
            cd=composition['data']
            if composition['status']!='pass' or cd['scope']!=scope or cd['period'].get('as_of')!=as_of:continue
            matches={}
            for account,rows in accounts.items():
                target=rows[0];tv=by_validation[target['id']]
                refs=cd['account_fact_ids'].get(account,[])
                if len(refs)!=1 or refs[0] not in by_fact or refs[0] not in by_validation:break
                support=by_fact[refs[0]];sv=by_validation[support['id']]
                if (not target.get('company',{}).get('corp_code') or support.get('company',{}).get('corp_code')!=target['company']['corp_code']
                    or not target.get('run_id') or support.get('run_id')!=target['run_id']
                    or support['data']['currency']!=currency or support['data']['period']!=target['data']['period']
                    or not tv['data'].get('account_id') or tv['data'].get('account_id')!=sv['data'].get('account_id')
                    or Decimal(tv['data']['amount_comparison']['original_normalized'])!=Decimal(sv['data']['amount_comparison']['original_normalized'])):break
                caption_review=None
                if normalize_label(support['data']['original_account'])!=normalize_label(target['data']['original_account']):
                    caption_review=confirmed_caption_link(target['id'],support['id'],reviews,detail_records)
                    if not caption_review:break
                matches[account]={'target_fact_id':target['id'],'supporting_fact_id':support['id']}
                if caption_review:matches[account]['composition_review_id']=caption_review
            if set(matches)==set(ACCOUNTS):candidates.append({'composition_validation_id':composition['id'],'accounts':matches})
        if not candidates:continue
        candidates.sort(key=lambda x:x['composition_validation_id'])
        results.append({'id':uuid4().hex,'fact_id':None,'rule_id':LINK_RULE,'status':'pass','data':{
            'version':VERSION,'source_id':sid,'scope':scope,'currency':currency,'period':{'kind':'instant','as_of':as_of},
            'account_fact_ids':{a:[rows[0]['id']] for a,rows in accounts.items()},
            'composition_validation_id':candidates[0]['composition_validation_id'],'supporting_links':candidates,
            'limitation':'같은 조사에서 회사·기간·범위 검사를 통과한 공식 자료끼리 연결. 공식 코드·금액과 원문 명칭 일치 또는 독립 검증 축을 통과한 상세 구성 동일성 근거가 필요. 원문 주석의 반복 기재나 은행별 배분 확인을 주장하지 않음'}})
    return results
