"""사업 설명·판매 경로·연결 지역·거래 표시를 원문 인용과 연결한다."""
import json
from agent.research_store import row_data,IntegrityFailure
from .numeric_validation import NumericValidationService


def context_locations(raw,context):
    locations=[context['location']]
    locations += [p['location'] for p in context.get('preamble',[])
        if NumericValidationService._raw_check(raw,p['location'],p['text'])]
    if context.get('scope_location'):locations.append(context['scope_location'])
    return locations

RULES=(
 ('products','제품과 판매 시장','paragraph','1. 사업의 개요',('당사의 사업부문은','주문자생산','보수용'),
  '공시의 사업 설명이다. 연결 관련 설명을 차주 단독 매출 구성비로 바꾸지 않는다.'),
 ('oem','OEM 판매 경로','table','4. 매출 및 수주상황',('OEM 영업',),
  '공시의 판매 경로다. 개별 고객의 현재 계약·회수 조건을 확인한 것은 아니다.'),
 ('oes','OES 판매 경로','table','4. 매출 및 수주상황',('OES 영업',),
  '공시의 판매 경로다. 고객 집중도나 신용도를 추정하지 않는다.'),
 ('retail','시중 판매 경로','table','4. 매출 및 수주상황',('시중판매(직판)',),
  '공시의 유통 경로다. 최근 대리점별 회수 실적은 별도 미확인이다.'),
 ('orders','수주 관리 방식','paragraph','4. 매출 및 수주상황',('다. 수주상황','생산계획','수주잔고'),
  '공시의 주문/수주 관리 설명이다. 수주잔고 부재를 미래 매출 0으로 해석하지 않는다.'),
 ('regions','연결 보고에 표시된 지역','table','5. 영업부문 (연결)',('한국','중국','인도','미국'),
  '연결 당반기 지역 표의 머리글이다. 차주 단독 지역 매출이나 매출 비중으로 쓰지 않는다.'),
 ('related_party','특수관계자 거래의 총액·순액 표시','table','32. 특수관계자',('유상사급','순액','거래총액'),
  '별도 주석의 거래 표시 설명이다. 주석 거래총액을 본표 매출액과 바로 나눠 집중도를 계산하지 않는다.'))


def collect_business_review(store,sources):
    selected=[]
    for source in sources:
        if source['metadata'].get('receipt_number')!='20260813000874' or source['metadata']['reporting_period'].get('operation')!='document_member':continue
        raw=store.source_content(source['id'])
        with store.connection() as conn:fragments=[row_data(r) for r in conn.execute('SELECT * FROM research_fragments WHERE source_id=? ORDER BY rowid',(source['id'],))]
        selected.append((source,raw,fragments))
    items=[]
    for key,title,kind,section,needles,limitation in RULES:
        candidates={}
        for source,raw,fragments in selected:
            for fragment in fragments:
                data=fragment['data'];context=data['context'];paths=context.get('section_path',[])
                if context.get('kind')!=kind or section not in paths:continue
                if key in {'products','oem','oes','retail','orders'} and 'II. 사업의 내용' not in paths:continue
                if key=='regions' and (context.get('scope_hint')!='CFS' or not any(p['text']=='당반기' and NumericValidationService._raw_check(raw,p['location'],p['text']) for p in context.get('preamble',[]))):continue
                if key=='related_party' and (context.get('scope_hint')!='OFS' or '5. 재무제표 주석' not in paths):continue
                options=[(None,[{'text':data['text'],'location':data['location']}])] if kind=='paragraph' else [(r['row'],[{'text':c['text'],'location':c['location']} for c in r['cells']]) for r in context['rows']]
                for row,cells in options:
                    quote=' | '.join(c['text'] for c in cells)
                    if not all(n in quote for n in needles):continue
                    if not all(NumericValidationService._raw_check(raw,c['location'],c['text']) for c in cells):continue
                    contexts=[data['location']] if kind=='paragraph' else context_locations(raw,context)
                    if not all(NumericValidationService._raw_check(raw,loc) for loc in contexts):continue
                    value={'source_id':source['id'],'fragment_id':fragment['id'],'content_hash':source['content_hash'],
                        'receipt_number':'20260813000874','section_path':paths,'kind':kind,'row':row,'cells':cells,
                        'context_locations':contexts,'quote':quote,'scope':'CFS' if key=='regions' else 'OFS' if key=='related_party' else 'business_disclosure'}
                    candidates[(source['content_hash'],tuple(c['location']['byte_start'] for c in cells))]=value
        items.append({'id':key,'title':title,'status':'verified_excerpt' if len(candidates)==1 else 'withheld',
            'quote':next(iter(candidates.values())) if len(candidates)==1 else None,
            'match_count':len(candidates),'limitation':limitation})
    return {'version':'business-review-1','items':items,'status':'source_quotes_available' if all(i['status']=='verified_excerpt' for i in items) else 'partial',
        'limitation':'선택한 사업/판매/지역/거래 원문을 확인했다. 전체 사업 분석·질문 유용성·현재 거래조건 확인은 아니다.'}


def check_business_quote(store,quote,basis_id,rule_id):
    rule=next((r for r in RULES if r[0]==rule_id),None)
    if rule is None:raise IntegrityFailure('business_rule_missing')
    with store.connection() as conn:
        source=store._one(conn,'research_sources',quote['source_id']);run=store._one(conn,'research_runs',source['run_id'])
        fragment=store._one(conn,'research_fragments',quote['fragment_id'])
    data=json.loads(fragment['data_json']);context=data['context']
    raw=store.source_content(source['id'])
    if context.get('kind')=='paragraph':
        expected=[{'text':data['text'],'location':data['location']}];locations=[data['location']]
    else:
        row=next((r for r in context.get('rows',[]) if r['row']==quote['row']),None)
        expected=[{'text':c['text'],'location':c['location']} for c in row['cells']] if row else None
        locations=context_locations(raw,context)
    if (run['basis_id']!=basis_id or fragment['source_id']!=source['id'] or source['content_hash']!=quote['content_hash']
            or context.get('kind')!=quote['kind'] or context.get('section_path')!=quote['section_path']
            or expected!=quote['cells'] or locations!=quote['context_locations']
            or quote['quote']!=' | '.join(c['text'] for c in (expected or []))
            or rule[3] not in quote['section_path'] or not all(n in quote['quote'] for n in rule[4])
            or quote['scope']!=('CFS' if rule_id=='regions' else 'OFS' if rule_id=='related_party' else 'business_disclosure')):raise IntegrityFailure('business_quote_context_mismatch')
    if (not all(NumericValidationService._raw_check(raw,c['location'],c['text']) for c in quote['cells'])
            or not all(NumericValidationService._raw_check(raw,loc) for loc in locations)):
        raise IntegrityFailure('business_quote_original_mismatch')
    return row_data(source)


def minimum_review(context):
    """기본표의 근거 확보/미확인/검토 미실행을 분리한다. 전체 완료 판정 아님."""
    business=context.get('business_review',{});findings={f['id']:f for f in context.get('public_findings',[])}
    available=[i['id'] for i in business.get('items',[]) if i['status']=='verified_excerpt']
    rows=context['financial_rows'];confirmed=sum(r['status']=='verified' for r in rows)
    def finding(key):return key if findings.get(key,{}).get('status')=='verified_excerpt' else None
    return [
        {'id':'identity','title':'기업 식별·조사 기준','status':'partial','observed':'법인·목적·기준일·기간·별도/연결과 검증 수치의 원문을 연결했다.',
         'references':['company','public_cutoff','numeric_batch_id'],'remaining':'실제 신청 법인·신청 조건과 전체 정정 이력의 의미 검토는 별도다.'},
        {'id':'business','title':'사업·매출 구조','status':'partial','observed':f'사업·판매·지역·거래 원문 {len(available)}항목을 연결했다.',
         'references':available,'remaining':'현재 고객별 계약·회수 조건, 연결 설명과 차주 단독 구성의 차이는 미확인이다.'},
        {'id':'finance','title':'재무상태·수익성','status':'partial','observed':f'{len(rows)}칸 중 {confirmed}칸의 값·기간·범위·단위를 확인했다.',
         'references':['numeric_batch_id',finding('noncash_fx')],'remaining':'차입 구성 보류·공식 버전 대응·범위 조정과 모든 변화의 원인 설명은 남아 있다.'},
        {'id':'cashflow','title':'현금흐름·운전자본','status':'partial','observed':f'채권/재고/채무 조정·환산손익 중 {sum(bool(finding(k)) for k in ("cashflow_working_capital","noncash_fx"))}묶음의 원문 인용을 연결했다.',
         'references':[finding('cashflow_working_capital'),finding('noncash_fx')],'remaining':'회수 지연/판매/계약 조건의 기여도와 향후 회수·지급 일정은 미확인이다.'},
        {'id':'borrowing','title':'차입·만기·유동성','status':'partial','observed':'확인된 차입 계정과 구성 보류를 구분해 표시했다.',
         'references':['financial_rows','U_borrowing'],'remaining':'공개 만기·리스·담보·약정 전체 의미 검토, 현재 차환/미사용 한도는 미확인이다.'},
        {'id':'disclosure','title':'공시 부담·보고서 신뢰성','status':'partial','observed':f'연간 감사·소송·후속 보증 중 {sum(bool(finding(k)) for k in ("audit_opinion","litigation","subsequent_guarantee"))}묶음의 원문 인용을 연결했다.',
         'references':[finding('audit_opinion'),finding('litigation'),finding('subsequent_guarantee')],'remaining':'반기 검토결론 원문·전체 후속 공시와 보증/소송의 현재 상태는 별도 확인이 필요하다.'},
        {'id':'purpose','title':'대출 목적·상환재원','status':'waiting_internal_information','observed':'제공된 조사 목적과 공시 기준을 연결했다.',
         'references':['purpose','U_working_capital','U_repayment'],'remaining':'실제 신청 금액·기간·집행·회수/상환 계획은 제공되지 않았다.'}]
