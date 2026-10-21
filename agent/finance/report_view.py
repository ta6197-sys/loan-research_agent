"""게시된 같은 버전에서 화면·출력용 안전한 읽기 모델을 만든다."""
import re
from urllib.parse import urlsplit,parse_qsl

from agent.research_store import Conflict,IntegrityFailure,digest,safe_payload
from .numeric_validation import NumericValidationService
from .report_context import svg_chart

VIEW_VERSION='finance-view-1.2'


def text(value):
    value=str(value or '')
    value=re.sub(r'(?:file://)?/(?:Users|home|private|tmp|var|opt)/[^\s<>"\']+','[로컬 경로 생략]',value)
    value=re.sub(r'[A-Za-z]:\\[^\s<>"\']+','[로컬 경로 생략]',value)
    return ''.join(c for c in value if c in '\n\t' or ord(c)>=32)


def public_url(value):
    if not value:return None
    parsed=urlsplit(value)
    if (parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password
            or any(re.search(r'key|token|auth|session|secret|password|signature',k,re.I) for k,_ in parse_qsl(parsed.query))):return None
    return value


def location_label(location):
    parts=list(location.get('section_path',[]))
    for key,label in [('table_number','표'),('row','행'),('column','열'),('line','원문 줄')]:
        if location.get(key) is not None:parts.append(label+' '+str(location[key]))
    return ' / '.join(parts) or '원문 위치 확인 필요'


def user_observation(value):
    result={k:text(v) if k=='text' else v for k,v in value.items() if k!='attachments'}
    result['attachments']=[{**a,'name':text(a['name']),'text_preview':text(a.get('text_preview','')),
        'locations_preview':[{k:text(v) if isinstance(v,str) else v for k,v in p.items()} for p in a.get('locations_preview',[])]}
        for a in value.get('attachments',[])]
    return result


def build_view(store,report_id):
    report=store.get_report(report_id)
    if report['status']!='published':raise Conflict('게시된 보고서만 화면·출력에 사용합니다.')
    content=report['content'];context=content['context'];run=store.get_run(report['run_id'])
    for observation in context.get('user_observations',[]):
        for attachment in observation.get('attachments',[]):
            source=store.get_source(attachment['source_id'])
            source_run=store.get_run(source['run_id'])
            if (source_run['basis_id']!=run['basis_id'] or source['metadata']['kind']!='user'
                    or digest(store.source_content(source['id']))!=attachment['hash']):
                raise IntegrityFailure('report_attachment_original_mismatch')
    evidence={};raw={};rows=[]
    confirmed=set(report['evidence_ids'])
    for item in context['financial_rows']:
        row={k:v for k,v in item.items() if k not in {'fact_ids','validation_ids','source_ids'}}
        row['label']=text(row['label']);row['evidence_ids']=[]
        for fid in item['fact_ids']:
            if row['status']=='verified' and fid not in confirmed:raise IntegrityFailure('published_fact_reference_missing')
            if fid not in evidence:
                fact=store.get_evidence(fid)
                if fact['basis']['id']!=run['basis_id']:raise IntegrityFailure('report_view_basis_mismatch')
                source=fact['source'];sid=source['id'];location=fact['fragment']['data']['location']
                if sid not in raw:raw[sid]=store.source_content(sid)
                if not NumericValidationService._raw_check(raw[sid],location):raise IntegrityFailure('report_view_original_location_invalid')
                meta=source['metadata'];reference='E'+str(len(evidence)+1)
                evidence[fid]={'id':reference,'fact_id':fid,'source_id':sid,'fragment_id':fact['fragment']['id'],
                    'source_kind':meta['kind'],'title':text(meta['title']),'receipt_number':meta.get('receipt_number'),
                    'url':public_url(meta.get('url')),'scope':fact['data']['scope'],'period':fact['data']['period'],
                    'unit':text(fact['data']['unit']),'currency':text(fact['data']['currency']),
                    'raw_value':text(fact['data'].get('raw_value')),'excerpt':text(fact['fragment']['data']['text']),
                    'location':location_label(location),'content_hash':source['content_hash'],
                    'use_status':row['status'],'rights_status':meta['rights_status'],'usage_restrictions':[text(x) for x in meta.get('usage_restrictions',[])]}
            row['evidence_ids'].append(evidence[fid]['id'])
        rows.append(row)
    sections=[]
    for section in context['sections']:
        fragments=[]
        for fragment in section.get('source_fragments',[]):
            source=store.get_source(fragment['source_id']);sid=source['id']
            if sid not in raw:raw[sid]=store.source_content(sid)
            if source['content_hash']!=fragment['content_hash']:raise IntegrityFailure('section_source_changed')
            location_verified=NumericValidationService._raw_check(raw[sid],fragment.get('location',{}))
            fragments.append({'source_id':sid,'text':text(fragment.get('text',fragment.get('snippet','')))[:1800],
                'location':('' if location_verified else '위치 미확인 · ')+location_label(fragment.get('location',fragment.get('data',{}).get('location',{}))),
                'url':public_url(source['metadata'].get('url'))})
        sections.append({'id':section['id'],'title':text(section['title']),'status':'partial',
            'limitation':text(section.get('limitation','')),'search_count':len(section.get('searches',[])),
            'fragments':fragments,'followup_review':section.get('followup_review')})
    charts=[]
    for chart in context['charts']:
        charts.append({k:v for k,v in chart.items() if k!='svg'})
    public_findings=[];public_evidence=[]
    from .public_content import check_public_quote
    for finding in context.get('public_findings',[]):
        quotes=[]
        if finding['status']=='verified_excerpt':
            for quote in finding['quotes']:
                source=check_public_quote(store,quote,run['basis_id']);meta=source['metadata']
                reference='P'+str(sum(len(f['quotes']) for f in public_findings)+len(quotes)+1)
                location={'section_path':quote['section_path'],'table_number':quote['table_number'],
                    'row':quote['row'],'line':quote['cells'][0]['location'].get('line')}
                quotes.append({'id':reference,'text':text(quote['quote']),'source_id':source['id'],
                    'receipt_number':quote['receipt_number'],'content_hash':source['content_hash'],
                    'fragment_id':quote['fragment_id'],'location':location_label(location),
                    'url':public_url(meta.get('url')),'scope':quote['scope']})
                public_evidence.append({'id':reference,'source_id':source['id'],'fragment_id':quote['fragment_id'],
                    'source_kind':meta['kind'],'title':text(meta['title']),'receipt_number':quote['receipt_number'],
                    'url':public_url(meta.get('url')),'scope':quote['scope'],'period':{'public_disclosure_date':meta.get('published_at')},
                    'unit':'공시 원문 행','currency':'','raw_value':text(quote['quote']),'excerpt':text(quote['quote']),
                    'location':location_label(location),'content_hash':source['content_hash'],'use_status':'verified_excerpt',
                    'rights_status':meta['rights_status'],'usage_restrictions':[text(v) for v in meta.get('usage_restrictions',[])]})
        public_findings.append({'id':finding['id'],'title':text(finding['title']),'section':finding['section'],
            'status':finding['status'],'quotes':quotes,'limitation':text(finding['limitation'])})
    from .business_review import check_business_quote,minimum_review
    business_items=[]
    for item in context.get('business_review',{}).get('items',[]):
        projection={'id':item['id'],'title':text(item['title']),'status':item['status'],'limitation':text(item['limitation']),'quote':None}
        if item['status']=='verified_excerpt':
            quote=item['quote'];source=check_business_quote(store,quote,run['basis_id'],item['id']);meta=source['metadata']
            reference='B'+str(len(business_items)+1);loc={'section_path':quote['section_path'],'row':quote['row'],
                'line':quote['cells'][0]['location'].get('line')}
            projection['quote']={'id':reference,'text':text(quote['quote']),'location':location_label(loc),'scope':quote['scope']}
            public_evidence.append({'id':reference,'source_id':source['id'],'fragment_id':quote['fragment_id'],
                'source_kind':meta['kind'],'title':text(meta['title']),'receipt_number':quote['receipt_number'],
                'url':public_url(meta.get('url')),'scope':quote['scope'],'period':{'public_disclosure_date':meta.get('published_at')},
                'unit':'공시 원문 인용','currency':'','raw_value':text(quote['quote']),'excerpt':text(quote['quote']),
                'location':location_label(loc),'content_hash':source['content_hash'],'use_status':'verified_excerpt',
                'rights_status':meta['rights_status'],'usage_restrictions':[text(v) for v in meta.get('usage_restrictions',[])]})
        business_items.append(projection)
    view={'schema_version':VIEW_VERSION,'report_id':report_id,'research_id':report['research_id'],'version':report['version'],
        'report_hash':report['publication']['content_hash'],'published_at':report['publication']['published_at'],
        'company':{k:text(context['company'].get(k)) for k in ('name','corp_code','stock_code')},
        'purpose':text(context['purpose']),'public_cutoff':context['public_cutoff'],'financial_confirmation':'incomplete',
        'model_status':content['model_status'],'facts':[{'text':text(f['text']),'evidence_id':f['evidence_id']}
            for f in content.get('model',{}).get('draft',{}).get('facts',[])],
        'interpretations':[text(x) for x in content.get('interpretations',[])],
        'unknowns':[{'id':u['evidence_id'],'text':text(u['text']),'status':u.get('status','unresolved')} for u in context['unknowns']],
        'questions':[{**q,'question':text(q['question']),'reason':text(q.get('reason')),
            'requested_information':text(q.get('requested_information'))} for q in content['questions']],
        'user_observations':[user_observation(o) for o in context.get('user_observations',[])],
        'sections':sections,'public_findings':public_findings,'business_review':business_items,
        'minimum_review':minimum_review(context) if 'business_review' in context else [],
        'financial_rows':rows,'charts':charts,'evidence':list(evidence.values())+public_evidence,
        'limitations':[text(x) for x in content['limitations']]}
    view['view_hash']=digest(safe_payload(view).encode())
    return view


def chart_svg(view,chart_id):
    chart=next((c for c in view['charts'] if c['id']==chart_id),None)
    if chart is None:raise ValueError('없는 차트입니다.')
    if chart['status']=='withheld':raise Conflict('비교 근거가 부족해 그리기를 보류한 차트입니다.')
    return svg_chart(chart['topic'],chart['points'])


def compare_views(before,after):
    if before['research_id']!=after['research_id']:raise Conflict('같은 조사의 판끼리 비교해주세요.')
    changes=[]
    old={(r['scope'],r['end'],r['account']):r for r in before['financial_rows']}
    for row in after['financial_rows']:
        previous=old.get((row['scope'],row['end'],row['account']))
        if previous is None or (previous['status'],previous.get('value'))!=(row['status'],row.get('value')):
            changes.append({'scope':row['scope'],'end':row['end'],'label':row['label'],
                'before':previous.get('value') if previous and previous['status']=='verified' else '미확인',
                'after':row.get('value') if row['status']=='verified' else '미확인'})
    prior={u['id']:u for u in before['unknowns']}
    issues=[{'id':u['id'],'before':prior.get(u['id'],{}).get('status','unresolved'),'after':u['status'],'text':u['text']}
        for u in after['unknowns'] if prior.get(u['id'])!=u]
    return {'before_version':before['version'],'after_version':after['version'],'financial_changes':changes,
        'issue_changes':issues,'added_user_input_count':max(0,len(after['user_observations'])-len(before['user_observations']))}
