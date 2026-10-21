"""고정 보관 사례의 주석·감사·후속 보증 행을 원문 인용으로 연결한다.

숫자 추정·원인 해석·신용 판단을 하지 않는다. 선택 행과 문맥의 바이트를
다시 확인하고, 다중/미발견/범위 불명은 확인된 인용으로 승격하지 않는다.
"""
import json
from agent.research_store import row_data,IntegrityFailure
from .numeric_validation import NumericValidationService
from .extraction_service import ExtractionService

PLANS=(
 ('cashflow_working_capital','cashflow','영업 자산·부채 변동의 공시 행','20260813000874','27. 현금흐름표',
  ['매출채권의 감소(증가) 조정','재고자산의 감소(증가)','매입채무의 증가(감소)'],
  '별도 당반기 현금흐름 조정의 일부다. 세 항목 합계를 전체 영업현금흐름으로 쓰거나 회수 지연을 단정하지 않는다.'),
 ('noncash_fx','cashflow','외화환산이익과 비현금 조정의 공시 행','20260813000874','',
  ['외화환산이익','외화환산이익 조정'],
  '별도 기타수익 표의 누적 열과 현금흐름 조정을 구분한다. 미실현 환산이익을 현금 유입으로 보지 않는다.'),
 ('audit_opinion','disclosure','외부감사 표의 당기 별도 감사보고서 행','20260318001187','1. 외부감사에 관한 사항',
  ['제51기(당기)'],
  '2025 사업보고서의 당기 별도 감사보고서 행이다. 적정의견은 상환 가능성이나 위험 부재를 보증하지 않는다.'),
 ('litigation','disclosure','별도 우발상황의 소송 설명','20260813000874','31. 우발상황 및 주요 약정사항',
  ['의무의 성격에 대한 기술, 우발부채'],
  '공시 당시 설명을 인용한다. 소송 결과·손실액·현재 진행 상태를 추정하지 않는다.'),
 ('subsequent_guarantee','disclosure','후속 채무보증 공시의 금액·기간·설명','20260820800110','',
  ['1. 채무자','채무보증금액(원)','채무보증기간','종료일','5. 채무보증 총 잔액(원)','1) 상기 채무보증건은 기 보증을 연장하는 것입니다.'],
  '2026-08-20 공시 기준이다. 총잔액에 개별 연장액을 더하거나 차주 차입금에 합산하지 않는다. 기준일 현재 잔액은 별도 미확인이다.'))


def collect_public_content(store,sources):
    documents=[s for s in sources if s['metadata']['reporting_period'].get('operation')=='document_member'
        and s['metadata'].get('receipt_number') in {p[3] for p in PLANS}]
    tables={};raw={}
    for source in documents:
        sid=source['id'];raw[sid]=store.source_content(sid)
        ExtractionService(store).extract_source(sid)
        with store.connection() as conn:
            tables[sid]=[(r['id'],json.loads(r['data_json'])['context']) for r in conn.execute(
                'SELECT id,data_json FROM research_fragments WHERE source_id=? ORDER BY rowid',(sid,))
                if json.loads(r['data_json'])['context'].get('kind')=='table']
    findings=[]
    for key,section,title,receipt,note,labels,limitation in PLANS:
        selected=[];missing=[]
        for label in labels:
            matches=[]
            for source in documents:
                if source['metadata'].get('receipt_number')!=receipt:continue
                sid=source['id']
                for fragment_id,table in tables[sid]:
                    paths=table.get('section_path',[])
                    if note and note not in paths:continue
                    if receipt=='20260813000874' and ('5. 재무제표 주석' not in paths or table.get('scope_hint')!='OFS'):continue
                    if key=='noncash_fx' and not any(p in paths for p in ('25. 기타수익과 기타비용','27. 현금흐름표')):continue
                    if key in {'cashflow_working_capital','noncash_fx'} and not any(p['text']=='당반기' for p in table.get('preamble',[])):continue
                    if key=='subsequent_guarantee' and table.get('table_number')!=1:continue
                    for row in table['rows']:
                        texts=[c['text'] for c in row['cells']]
                        if not any(t==label or (label.startswith('1) ') and t.startswith(label)) for t in texts):continue
                        if key=='audit_opinion' and '감사보고서' not in texts:continue
                        if not NumericValidationService._raw_check(raw[sid],table['location']):continue
                        cells=[{'text':c['text'],'location':c['location']} for c in row['cells']]
                        if not all(NumericValidationService._raw_check(raw[sid],c['location'],c['text']) for c in cells):continue
                        # 확장 셀이 아니라 실제 머리글·범위·단위 위치도 보존/재검사한다.
                        context_locations=[table['location']]
                        if table.get('scope_location'):context_locations.append(table['scope_location'])
                        context_locations += [u['location'] for u in table.get('unit_candidates',[])]
                        context_locations += [p['location'] for p in table.get('preamble',[])]
                        headers=[c for r in table['rows'] for c in r['cells'] if c['tag']=='th']
                        context_locations += [h['location'] for h in headers]
                        if not all(NumericValidationService._raw_check(raw[sid],loc) for loc in context_locations):continue
                        matches.append({'source_id':sid,'fragment_id':fragment_id,'content_hash':source['content_hash'],
                            'receipt_number':receipt,'section_path':paths,'table_number':table['table_number'],
                            'row':row['row'],'cells':cells,'context_locations':context_locations,
                            'scope':'OFS' if receipt!='20260820800110' else 'guarantee_disclosure',
                            'quote':' | '.join(texts)+(' · 당반기' if key in {'cashflow_working_capital','noncash_fx'} else '')+
                                (' · 머리글: '+' / '.join(dict.fromkeys(h['text'] for h in headers if h['text'])) if headers else '')})
            # 같은 원문이 둘 이상 보관돼도 동일 해시/위치는 하나의 인용이다.
            unique={(m['content_hash'],tuple(c['location']['byte_start'] for c in m['cells'])):m for m in matches}
            if len(unique)==1:selected.append(next(iter(unique.values())))
            else:missing.append({'label':label,'reason':'not_found_or_ambiguous','match_count':len(unique)})
        findings.append({'id':key,'section':section,'title':title,'status':'verified_excerpt' if not missing else 'withheld',
            'quotes':selected if not missing else [],'missing':missing,'limitation':limitation})
    return findings


def check_public_quote(store,quote,basis_id):
    with store.connection() as conn:
        source=store._one(conn,'research_sources',quote['source_id'])
        run=store._one(conn,'research_runs',source['run_id'])
        fragment=store._one(conn,'research_fragments',quote['fragment_id'])
    if run['basis_id']!=basis_id or fragment['source_id']!=source['id'] or source['content_hash']!=quote['content_hash']:
        raise IntegrityFailure('public_quote_source_or_basis_mismatch')
    raw=store.source_content(source['id'])
    table=json.loads(fragment['data_json'])['context']
    row=next((r for r in table.get('rows',[]) if r['row']==quote['row']),None)
    expected=[{'text':c['text'],'location':c['location']} for c in row['cells']] if row else None
    headers=[c for r in table.get('rows',[]) for c in r['cells'] if c['tag']=='th']
    expected_locations=[table['location']]+([table['scope_location']] if table.get('scope_location') else [])
    expected_locations += [u['location'] for u in table.get('unit_candidates',[])]+[p['location'] for p in table.get('preamble',[])]+[h['location'] for h in headers]
    current=any(p['text']=='당반기' for p in table.get('preamble',[])) and any(p in table.get('section_path',[]) for p in ('25. 기타수익과 기타비용','27. 현금흐름표'))
    expected_quote=' | '.join(c['text'] for c in (expected or []))+(' · 당반기' if current else '')+(' · 머리글: '+' / '.join(dict.fromkeys(h['text'] for h in headers if h['text'])) if headers else '')
    if (table.get('kind')!='table' or table.get('table_number')!=quote['table_number']
            or table.get('section_path')!=quote['section_path'] or expected!=quote['cells']
            or expected_locations!=quote['context_locations']
            or quote['scope']!=('guarantee_disclosure' if quote['receipt_number']=='20260820800110' else 'OFS')
            or json.loads(source['metadata_json']).get('receipt_number')!=quote['receipt_number']):
        raise IntegrityFailure('public_quote_table_context_mismatch')
    if (not all(NumericValidationService._raw_check(raw,c['location'],c['text']) for c in quote['cells'])
            or not all(NumericValidationService._raw_check(raw,loc) for loc in quote['context_locations'])
            or not all(NumericValidationService._raw_check(raw,p['location'],p['text']) for p in table.get('preamble',[])+headers)
            or quote['quote']!=expected_quote):
        raise IntegrityFailure('public_quote_original_mismatch')
    return row_data(source)
