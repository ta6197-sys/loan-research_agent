"""검증된 값·보류·원문 검색 범위를 일곱 항목/13계정/차트 입력으로 고정한다."""
from collections import defaultdict
from decimal import Decimal
from html import escape
from decimal import InvalidOperation

from .extraction_service import ExtractionService,ACCOUNT_RULES
from .numeric_validation import fiscal_report_period,period_key
from .validation_acceptance import MANDATORY_CHECKS

SECTIONS=(('identity','기업 식별'),('business','사업'),('finance','재무'),('cashflow','현금흐름'),
          ('borrowing','차입'),('disclosure','공시 부담·신뢰성'),('purpose','목적·상환재원'))
PERIOD_ENDS=('2023-12-31','2024-12-31','2025-12-31','2025-06-30','2026-06-30')
LABELS={'revenue':'매출액','operating_profit':'영업이익','net_profit':'당기순이익',
        'operating_cashflow':'영업현금흐름','cash':'현금및현금성자산','inventory':'재고자산',
        'receivables':'유동 매출채권 및 기타채권','short_term_borrowings':'단기차입금',
        'current_long_term_borrowings':'유동성장기차입금','long_term_borrowings':'장기차입금',
        'assets':'자산','liabilities':'부채','equity':'자본'}
BORROWINGS={'short_term_borrowings','current_long_term_borrowings','long_term_borrowings'}


def numeric_rows(batch,period_ends=PERIOD_ENDS,periods=None):
    expected={item['end']:item for item in periods or ()}
    groups=defaultdict(list)
    for validation in batch['validations']:
        if validation.get('rule_id')!='numeric_fact':continue
        d=validation['data'];end=d['period'].get('as_of') or d['period'].get('end')
        target=expected.get(end)
        if periods is not None and (target is None or d['period'].get('kind') not in {'duration','instant'}
                or (d['period'].get('kind')=='duration' and
                    (d['period'].get('start'),d['period'].get('end')) != (target['start'],target['end']))
                or (d['period'].get('kind')=='instant' and d['period'].get('as_of')!=target['end'])):
            continue
        if periods is None and d['period'].get('kind')=='duration' and d['period'].get('start')!=(end or '')[:4]+'-01-01':continue
        if d.get('account') in ACCOUNT_RULES:groups[(d['scope'],end,d['account'])].append(validation)
    output=[]
    for scope in ('OFS','CFS'):
        for end in period_ends:
            target=expected.get(end)
            for account in LABELS:
                items=groups.get((scope,end,account),[])
                values={v['data'].get('amount_comparison',{}).get('original_normalized') for v in items}
                good=bool(items) and len(values)==1 and None not in values
                good=good and all(v['status']=='pass' and all(v['data']['checks'].get(k,{}).get('status')=='pass'
                    for k in MANDATORY_CHECKS) for v in items)
                if account in BORROWINGS:
                    good=good and all(v['data']['checks'].get('V12',{}).get('status')=='pass'
                        and not v['data'].get('investigation_use_status','').startswith('withheld') for v in items)
                row={'scope':scope,'end':end,'account':account,'label':LABELS[account],
                    'status':'verified' if good else 'withheld','currency':'KRW','unit':'원',
                    'fact_ids':sorted(v['fact_id'] for v in items),
                    'validation_ids':sorted(v['id'] for v in items),
                    'source_ids':sorted({v['data']['source_id'] for v in items})}
                row['period']=items[0]['data']['period'] if items else (
                    {'kind':'instant','as_of':end} if target and account not in {'revenue','operating_profit','net_profit','operating_cashflow'}
                    else {'kind':'duration','start':target['start'],'end':end} if target else {'end':end})
                if good:row['value']=next(iter(values))
                else:row['reason']='composition_or_numeric_evidence_unconfirmed' if items else 'input_missing'
                output.append(row)
    return output


def svg_chart(title,series):
    # 금액은 문자열로 보존하고 그림의 좌표만 비율로 변환한다.
    width=720;height=280;baseline=160;top=100
    values=[abs(Decimal(p['value'])) for p in series if p.get('status')=='verified']
    scale=max(values,default=Decimal(1)) or Decimal(1)
    parts=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img">',
           '<rect width="720" height="280" fill="#fff"/>',
           f'<text x="20" y="24" fill="#111" font-size="16">{escape(title)} (별도·원)</text>',
           f'<line x1="20" y1="{baseline}" x2="700" y2="{baseline}" stroke="#bbb"/>']
    step=660/max(len(series),1)
    for i,p in enumerate(series):
        x=30+i*step
        if p.get('status')=='verified':
            value=Decimal(p['value']);bar=float(abs(value)/scale*top);y=baseline-bar if value>=0 else baseline
            color='#2563eb' if p['series_index']==0 else '#059669'
            parts.append(f'<rect x="{x:.2f}" y="{y:.2f}" width="{step*.6:.2f}" height="{bar:.2f}" fill="{color}"/>')
            parts.append(f'<text x="{x:.2f}" y="{250 if value<0 else max(45,y-6):.2f}" font-size="9">{escape(p["value"])}</text>')
        else:parts.append(f'<text x="{x:.2f}" y="145" font-size="10">미확인</text>')
        parts.append(f'<text x="{x:.2f}" y="268" font-size="9">{escape(p["end"][:4]+" "+p["label"])}</text>')
    return ''.join(parts)+'</svg>'


def charts(rows,batch,mapping,period_ends=PERIOD_ENDS,periods=None):
    lookup={(r['scope'],r['end'],r['account']):r for r in rows};result=[]
    annual=tuple(p['end'] for p in periods if p['period_kind']=='annual') if periods is not None else tuple(end for end in period_ends if end.endswith('-12-31'))
    interim=tuple(p['end'] for p in periods if p['period_kind']=='interim') if periods is not None else tuple(end for end in period_ends if end.endswith('-06-30'))
    for kind,ends in (('annual',annual),('interim',interim)):
        for topic,accounts in (('영업이익·영업현금흐름',('operating_profit','operating_cashflow')),
                               ('현금·차입금',('cash','borrowing_total'))):
            points=[];unconfirmed_bridge=False
            for end in ends:
                for index,account in enumerate(accounts):
                    if account=='borrowing_total':
                        candidates=[c for c in batch['calculations'] if c['formula_version'].endswith(':V12_three_borrowing_captions_subtotal')
                            and c['status']=='pass' and c['data'].get('inputs')
                            and all(v.get('scope')=='OFS' and v.get('period',{}).get('as_of')==end for v in c['data']['inputs'].values())]
                        values={c['data']['result'] for c in candidates}
                        point={'end':end,'label':'차입 3계정 소계','status':'withheld'}
                        if len(values)==1 and all(lookup[('OFS',end,a)]['status']=='verified' for a in BORROWINGS):
                            point.update(status='verified',value=next(iter(values)))
                    else:point=dict(lookup[('OFS',end,account)])
                    point['series_index']=index;points.append(point)
            # 각 보고서의 같은 이름을 버전 간 개념 대응 확인으로 바꾸지 않는다.
            target_accounts={a for a in accounts if a!='borrowing_total'}
            for link in mapping.get('manifest',{}).get('concept_links',[]):
                if link.get('scope')!='OFS' or link.get('account') not in target_accounts:continue
                # 반기 2025/2026은 보관 XBRL의 공식 버전 대응이 미확인이다.
                if kind=='interim' and link.get('reason')=='namespace_version_differs_official_bridge_missing':unconfirmed_bridge=True
            chart={'id':kind+'-'+accounts[0],'topic':topic,'period_kind':kind,'scope':'OFS','unit':'원',
                   'status':'withheld' if unconfirmed_bridge else 'available_with_gaps','points':points}
            if unconfirmed_bridge:chart['reason']='official_cross_version_bridge_unconfirmed'
            else:chart['svg']=svg_chart(topic,points)
            result.append(chart)
    return result


def context_period_ends(comparison_window):
    """조사에 저장된 비교 창을 수치표·차트·모델 입력에 동일하게 적용한다."""
    annual=comparison_window.get('annual_years',comparison_window.get('years',[]))
    interim=comparison_window.get('interim_periods',[])
    try:
        annual_ends=tuple(f'{int(year):04d}-12-31' for year in annual)
        interim_ends=tuple(f'{int(str(period)[:4]):04d}-06-30' for period in interim if str(period).endswith('-H1'))
    except (TypeError,ValueError):
        return PERIOD_ENDS
    values=annual_ends+interim_ends
    return values if len(annual_ends)>=2 and len(interim_ends)>=1 and len(set(values))==len(values) else PERIOD_ENDS


def fiscal_periods(target_reports, fiscal_month):
    """공시 수집 계획의 연도·코드와 검증 결산월로 비교 기간을 재구성한다."""
    if type(fiscal_month) is not int or not 1 <= fiscal_month <= 12:
        raise ValueError('fiscal_month_review_required')
    annual, interim = [], []
    seen = set()
    for target in target_reports:
        code = target.get('report_code')
        try: year = int(target['year'])
        except (KeyError, TypeError, ValueError): raise ValueError('fiscal_target_invalid') from None
        bounds = fiscal_report_period(year, code, fiscal_month)
        if bounds is None or (year, code) in seen:
            raise ValueError('fiscal_target_invalid_or_duplicate')
        seen.add((year, code))
        start, end = (value.isoformat() for value in bounds)
        item = {'start':start,'end':end,'period_kind':'annual' if code=='11011' else 'interim',
                'report_code':code,'year':year}
        (annual if code=='11011' else interim).append(item)
    annual.sort(key=lambda p:p['end']);interim.sort(key=lambda p:p['end'])
    periods = annual + interim
    if len(annual)<2 or len(interim)<1 or len({p['end'] for p in periods})!=len(periods):
        raise ValueError('fiscal_targets_insufficient_or_overlapping')
    return periods


def xbrl_report_gate(rows, numeric_batch, mapping):
    """원문/API 숫자와 별도로 XBRL 계정·기간·금액 대응을 요구한다."""
    validations = {v['id']:v for v in numeric_batch.get('validations',[])}
    bindings = [v for v in mapping.get('validations',[]) if
                v.get('rule_id')=='XBRL_STANDARD_CONCEPT' and
                all(v.get('data',{}).get(k)=='pass' for k in ('concept_status','context_status','amount_status'))]
    missing=[];matched=[];mapping_ids=[]
    def same_amount(left,right):
        try:return Decimal(str(left).replace(',',''))==Decimal(str(right).replace(',',''))
        except (InvalidOperation,TypeError):return False
    for row in rows:
        if row['status']!='verified':continue
        found=None
        for vid in row['validation_ids']:
            d=validations[vid]['data']
            value=d['amount_comparison']['api_value']
            found=next((item for item in bindings if
                (b:=item['data']).get('account')==row['account'] and b.get('scope')==row['scope']
                and period_key(b.get('period',{}))==period_key(d['period'])
                and b.get('currency')==d.get('currency') and b.get('api_value') is not None
                and same_amount(b['api_value'],value)),None)
            if found:break
        key=f"{row['scope']}:{row['end']}:{row['account']}"
        if found:
            matched.append(key);mapping_ids.append(found['id'])
        else:missing.append(key)
    return {'status':'pass' if matched and not missing else 'withheld',
            'matched_row_keys':sorted(matched),'unmatched_row_keys':sorted(missing),
            'mapping_validation_ids':sorted(set(mapping_ids)),
            'reason':None if matched and not missing else 'independent_xbrl_period_account_amount_unconfirmed'}


def prepare_context(store,batch,mapping,*,report_run_id=None):
    evidence_run=store.get_run(batch['run_id'])
    periods=None
    if evidence_run['trigger'].get('kind') == 'general_company':
        collection=evidence_run['trigger'].get('collection_result',{})
        fiscal=collection.get('fiscal_period',{})
        if fiscal.get('status') not in {'supported','identity_only'}:
            raise ValueError('fiscal_month_review_required')
        periods=fiscal_periods(collection.get('target_reports',[]),fiscal.get('month'))
        if not batch.get('validations'):
            raise ValueError('numeric_validation_required')
    run=store.get_run(report_run_id or batch['run_id'])
    if evidence_run['basis_id']!=run['basis_id']:raise ValueError('report_basis_mismatch')
    trigger=run['trigger'];followup=trigger.get('kind')=='followup'
    parent=store.get_report(trigger['effective_parent_report_id']) if followup else None
    with store.connection() as conn:
        from agent.research_store import row_data
        basis=row_data(store._one(conn,'research_bases',run['basis_id']))
        research=store.get_research(basis['research_id'])
        sources=[row_data(r) for r in conn.execute('SELECT * FROM research_sources WHERE run_id=? ORDER BY id',(evidence_run['id'],))]
    period_ends=tuple(p['end'] for p in periods) if periods is not None else context_period_ends(basis['data'].get('comparison_window',{}))
    rows=numeric_rows(batch,period_ends,periods);extractor=ExtractionService(store)
    report_gate=None
    if periods is not None and fiscal['month'] != 12:
        report_gate=xbrl_report_gate(rows,batch,mapping)
        if report_gate['status']!='pass':
            raise ValueError('independent_xbrl_period_account_amount_unconfirmed')
    from .change_analysis import analyze_financial_changes
    change_analysis=analyze_financial_changes(rows)
    # 보관 사례의 특정 접수번호·표 구조 인용 규칙은 일반 기업에 적용하지 않는다.
    # 일반 기업은 아래 공통 원문 검색과 검증 수치·변화 조사 결과만 사용한다.
    if evidence_run['trigger'].get('kind')=='cached_case':
        from .public_content import collect_public_content
        public_findings=collect_public_content(store,sources)
        from .business_review import collect_business_review
        business_review=collect_business_review(store,sources)
    else:
        public_findings=[];business_review=None
    # 보관 사례의 특정 접수번호에 묶지 않는다. 일반기업의 정기공시 원문도 같은 검색·근거
    # 계약으로 읽되, 검색 발췌는 전체 공시 검토나 부재의 증명이 아니다.
    documents=[s for s in sources if s['metadata']['reporting_period'].get('operation')=='document_member']
    from .change_analysis import investigate_hypotheses
    def search_stored_documents(query):
        return [extractor.search(source['id'],query,limit=2) for source in documents]
    change_analysis['actions']=investigate_hypotheses(change_analysis['hypotheses'],search_stored_documents)
    queries={'identity':['회사명'],'business':['주요제품','사업의 내용'],'finance':['재무제표'],
             'cashflow':['영업활동'],'borrowing':['차입금','리스'],'disclosure':['담보','보증','감사의견'],
             'purpose':['재고자산','매출채권']}
    sections=[]
    focus=trigger.get('focus_sections',[])
    for section,label in sorted(SECTIONS,key=lambda item:(item[0] not in focus,focus.index(item[0]) if item[0] in focus else 0)):
        if followup and section not in trigger['affected_sections']:
            from copy import deepcopy
            sections.append(deepcopy(next(s for s in parent['content']['context']['sections'] if s['id']==section)))
            continue
        attempts=[];fragments=[]
        for source in documents:
            for query in queries[section]:
                found=extractor.search(source['id'],query,limit=2)
                attempts.append({'source_id':source['id'],'query':query,'status':found['status'],
                                 'match_count':found.get('match_count',0),'truncated':found.get('truncated',False)})
                for item in found['items']:
                    fragments.append({'source_id':source['id'],'content_hash':source['content_hash'],**item})
        sections.append({'id':section,'title':label,'status':'partial','searches':attempts,'source_fragments':fragments,
            'limitation':'보관한 공시의 검색·확인 범위다. 검색 결과 없음이나 발췌 일부를 공시 전체의 내용 부재/검토 완료로 바꾸지 않는다.'})
    evidence=[]
    preferred=('revenue','operating_profit','operating_cashflow','cash','inventory','receivables')
    model_source_ids=set()
    for account in preferred:
        row=next(r for r in rows if r['scope']=='OFS' and r['end']==period_ends[-1] and r['account']==account)
        if row['status']=='verified':
            # 이전 합성/복구 시험의 행은 source_ids 이전 계약을 사용한다.
            # 그 경우 권한 없는 원격 전송으로 승격하지 않고 빈 집합으로 남긴다.
            model_source_ids.update(row.get('source_ids',[]))
            period=row['period'];label=(period['start']+'~'+period['end']+' 누적') if period.get('kind')=='duration' else row['end']+' 말'
            evidence.append({'evidence_id':'F_'+account,'state':'verified',
                'text':f"{research['company']['name']}의 {label} 별도 {row['label']}은 {row['value']}원이다.",
                'fact_ids':row['fact_ids']})
    unknowns=[{'evidence_id':'U_borrowing','state':'withheld','text':'차입 구성과 은행별 만기·상환 배분의 미확인 항목이 남아 있다.'},
              {'evidence_id':'U_working_capital','state':'withheld','text':'신청 금액·자금 집행 일정·매출채권 회수 및 재고 회전의 내부 자료가 없다.'},
              {'evidence_id':'U_repayment','state':'withheld','text':'확인된 공개 공시만으로 신청 대출의 구체적 상환 계획은 확인되지 않는다.'},
              {'evidence_id':'U_scope','state':'withheld','text':'공식 계정 버전 대응과 연결 범위 조정이 미확인인 비교가 남아 있다.'}]
    observations=[]
    if followup:
        from .research_followup import ISSUE_SECTIONS
        for source_id in trigger['frozen_source_ids']:
            source=store.get_source(source_id);meta=source['metadata']['reporting_period']
            observation={'input_id':meta['followup_input_id'],'source_id':source_id,
                'content_hash':source['content_hash'],'kind':meta['kind'],'question_ids':meta['question_ids'],
                'text':store.source_content(source_id).decode('utf-8'),'status':'user_provided_unverified'}
            if meta.get('attachment_bundle_id'):
                from .attachments import AttachmentService
                observation['attachments']=AttachmentService(store).observations(meta['attachment_bundle_id'],meta['followup_input_id'])
            observations.append(observation)
            attachment_preview='\n'.join('첨부 '+a['name']+' ('+a['status']+', 금융 미검증): '+a['text_preview'][:200]
                for a in observation.get('attachments',[])[:3])
            evidence.append({'evidence_id':'I_'+observation['input_id'],'state':'data',
                'text':'사용자 제공 미검증 '+meta['kind']+': '+observation['text']+('\n'+attachment_preview if attachment_preview else ''), 'source_id':source_id})
        for unknown in unknowns:
            received=[o['source_id'] for o in observations if unknown['evidence_id'] in o['question_ids']]
            if received:
                unknown.update(status='answered_unverified',received_source_ids=received)
                unknown['text']+=' 관련 사용자 입력을 받았으나 독립 근거 확인 전이므로 미확인 상태를 유지한다.'
        for section in sections:
            if section['id'] in trigger['affected_sections']:
                section['followup_review']={'status':'rechecked_with_unverified_input',
                    'source_ids':[o['source_id'] for o in observations if not o['question_ids']
                        or any(section['id'] in ISSUE_SECTIONS[q] for q in o['question_ids'])],
                    'finding':'보관 공시를 재검색하고 기존 원문·수치 검증을 재실행했다. 사용자 설명의 독립 검증·외부 신규 수집은 수행하지 않았다.'}
    evidence+=unknowns
    source_by_id={source['id']:source for source in sources}
    disallowed_source_ids=sorted(source_id for source_id in model_source_ids
        if not source_by_id.get(source_id,{}).get('metadata',{}).get('allow_external_model',False))
    archived_public_only=bool(model_source_ids) and all(
        source_by_id.get(source_id,{}).get('metadata',{}).get('kind') in {'dart','official_standard'}
        and source_by_id.get(source_id,{}).get('metadata',{}).get('rights_status') in {'public_disclosure','public_reference'}
        for source_id in model_source_ids)
    external_model_policy={
        'allowed':bool(model_source_ids) and not disallowed_source_ids,
        'archived_public_only':archived_public_only,
        'model_evidence_source_count':len(model_source_ids),
        'blocked_source_count':len(disallowed_source_ids),
        'reason':None if model_source_ids and not disallowed_source_ids else 'source_external_model_permission_missing',
    }
    return {'run_id':run['id'],'evidence_run_id':evidence_run['id'],'basis_id':basis['id'],'company':research['company'],
        'parent_report_id':parent['id'] if parent else None,
        'followup_input_ids':trigger.get('followup_input_ids',[]),'user_observations':observations,
        'affected_sections':trigger.get('affected_sections',[]),
        'purpose':basis['data']['purpose'],'public_cutoff':basis['data']['public_cutoff'],
        'numeric_batch_id':batch['id'],'mapping_batch_id':mapping['id'],
        'sections':sections,'financial_rows':rows,'comparison_period_ends':list(period_ends),
        'comparison_periods':periods or [],'fiscal_end_month':fiscal['month'] if periods is not None else 12,
        'xbrl_report_gate':report_gate,
        'charts':charts(rows,batch,mapping,period_ends,periods),'model_evidence':evidence,
        'external_model_policy':external_model_policy,
        'public_findings':public_findings,**({'business_review':business_review} if business_review is not None else {}),
        'unknowns':unknowns,'interpretations':change_analysis['hypotheses'],'change_analysis':change_analysis,
        'limitations':['공개 공시 기반 사전조사 초안이며 실제 대출 신청·승인 판단이 아니다.',
            '공식 IR 자동 수집과 스캔 OCR은 범위 밖이다. 로컬 PDF/XLSX/CSV/TXT 첨부는 읽기 한계·미검증 상태를 보존한다.',
            '후속 사용자 입력은 미검증 출처로 보존한다. 외부 재수집이나 내부 자료의 독립 검증 없이 공시 수치와 미확인을 변경하지 않는다.',
            '기타채권 포함 합계를 순수 매출채권으로 대체하거나 DSO·CCC를 임의 계산하지 않는다.',
            '모델 설명의 생성보다 수치·구성·비교 보류와 근거 추적을 우선한다.']}
