"""원문 명단·지분과 기간별 연결 변동표를 함께 대조한 공시 범위 관찰."""
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4
from .document_extractor import normalize_label, number
from .source_locations import raw_text
from .note_context import NoteContext

VERSION='scope-observation-1.0'
RULE='V03_scope_observation'


def _empty_change_table(context,tables):
    count=0;result=None
    for fragment_id,table in tables:
        loc=table['location']
        if not loc.get('parser_text_matches_raw') or not context.raw_check(context.content,loc):continue
        if not table['rows']:continue
        if [normalize_label(c['text']) for c in table['rows'][0]['cells']]!=['구분','자회사','사유']:continue
        index=context.index
        paragraphs=[i for i,e in enumerate(index.elements) if e.tag=='p' and e.end is not None
                    and e.end<=loc['byte_start'] and loc['byte_start']-e.end<4096]
        if not paragraphs:continue
        i=max(paragraphs,key=lambda i:index.elements[i].end)
        heading=raw_text(index.inner(i));heading_loc=index.location(i)
        if '연결대상회사의변동내용' not in normalize_label(heading) or not context.raw_check(context.content,heading_loc,heading):continue
        count+=1
        categories=set();valid=True;data_rows=0
        for row in table['rows']:
            if any(c.get('nested_table') or not context.raw_check(context.content,c['location'],c['text']) for c in row['cells']):valid=False;break
        for row in table['rows'][1:]:
            cells={c['column']:c for c in row['cells']}
            if 1 in cells:categories.add(normalize_label(cells[1]['text']))
            if not all(col in cells and cells[col]['text'].strip()=='-' for col in (2,3)):valid=False;break
            data_rows+=1
        if valid and data_rows and categories=={'신규연결','연결제외'}:
            result={'fragment_id':fragment_id,'location':loc,'heading':{'text':heading,'location':heading_loc},
                    'basis':'original_complete_change_table_with_unchanged_membership_required',
                    'not_a_numeric_zero':True}
    return result if count==1 else None


def observe_scope(source,content,identity,tables,facts,validations,raw_check):
    supported={v['fact_id']:v for v in validations if v.get('fact_id') and v['status']=='pass'}
    context=NoteContext(source,content,identity,facts,supported,raw_check)
    if not all(raw_check(content,identity.get('locations',{}).get(k)) for k in ('company','start','end')):return []
    candidates=[]
    for fragment_id,table in tables:
        if not table['location'].get('parser_text_matches_raw') or not raw_check(content,table['location']):continue
        if not table['rows'] or not table['rows'][0]['cells']:continue
        if normalize_label(table['rows'][0]['cells'][0]['text'])!='종속기업명':continue
        scope=context.scope(table)
        if not scope or scope['scope']!='CFS':continue
        current=[];previous=[];current_ok=True;previous_ok=True
        for row in table['rows'][1:]:
            company=next((c for c in row['cells'] if c['column']==1),None)
            if company is None:continue
            if not raw_check(content,company['location'],company['text']) or company.get('nested_table'):
                current_ok=previous_ok=False;break
            if not company['text'].strip():current_ok=previous_ok=False;break
            owners={'current':[],'previous':[]}
            for cell in row['cells']:
                headers=cell['column_headers'];labels={normalize_label(h['text']) for h in headers}
                if '소유지분율' not in labels:continue
                if not raw_check(content,cell['location'],cell['text']) or not all(raw_check(content,h['location'],h['text']) for h in headers):continue
                role='current' if labels & {'당기말','당반기말','당분기말'} else ('previous' if '전기말' in labels else None)
                if role is None:continue
                raw=cell['text'].strip();value=number(raw[:-1]) if raw.endswith('%') else None
                if value is None or not 0<Decimal(value)<=100:continue
                owners[role].append({'company_name':company['text'],'normalized_name':normalize_label(company['text']),
                    'ownership_percent':str(Decimal(value).normalize()),'name_location':company['location'],
                    'ownership_location':cell['location'],'headers':headers})
            for role,target in [('current',current),('previous',previous)]:
                if len(owners[role])==1:target.append(owners[role][0])
                elif role=='current':current_ok=False
                else:previous_ok=False
        if not current_ok or not current:continue
        if len({x['normalized_name'] for x in current})!=len(current):continue
        if not previous_ok or len({x['normalized_name'] for x in previous})!=len(previous):previous=[]
        signature=lambda rows:sorted((x['normalized_name'],x['ownership_percent']) for x in rows)
        unchanged=bool(previous) and signature(current)==signature(previous)
        change_table=_empty_change_table(context,tables) if unchanged else None
        candidates.append({'fragment_id':fragment_id,'scope_proof':scope,'current_members':current,
                           'previous_members':previous,'same_membership_and_ownership':unchanged,
                           'empty_change_table':change_table})
    if len(candidates)!=1:return []
    item=candidates[0];end=identity['end'];previous_end=str(int(end[:4])-1)+'-12-31'
    return [{'id':uuid4().hex,'fact_id':None,'rule_id':RULE,'status':'pass','data':{
        'version':VERSION,'source_id':source['id'],'receipt_number':source['metadata']['receipt_number'],'scope':'CFS',
        'period':{'kind':'duration','start':identity['start'],'end':end},
        'current_as_of':end,'previous_as_of':previous_end,**item,
        'absence_observed_within_reporting_period':bool(item['same_membership_and_ownership'] and item['empty_change_table']),
        'identity_locations':identity['locations'],
        'limitation':'명단·지분과 해당 기간의 변동표를 함께 대조한 공시 범위 관찰. 대시만으로 변경 없음이나 0을 확정하지 않음'}}]


def unchanged_scope_support(inputs, observations):
    current,previous=inputs.get('current',{}),inputs.get('previous',{})
    if any(x.get('scope')!='CFS' for x in (current,previous)):return None
    cp,pp=current.get('period',{}),previous.get('period',{})
    try:
        current_end=date.fromisoformat(cp.get('as_of',cp.get('end')));previous_end=date.fromisoformat(pp.get('as_of',pp.get('end')))
        if current_end<=previous_end:return None
        start=previous_end+timedelta(days=1) if 'as_of' in cp and 'as_of' in pp else min(date.fromisoformat(cp['start']),date.fromisoformat(pp['start']))
    except (TypeError,ValueError,KeyError):return None
    observed=[v for v in observations if v.get('rule_id')==RULE and v.get('status')=='pass']
    signature=lambda rows:tuple(sorted((r['normalized_name'],r['ownership_percent']) for r in rows))
    states={};evidence=[]
    for point in (previous_end,current_end):
        choices=[]
        for v in observed:
            d=v['data']
            for role in ('current','previous'):
                if d.get(role+'_as_of')==point.isoformat() and d.get(role+'_members'):
                    choices.append((signature(d[role+'_members']),v['id']))
        unique={key for key,_ in choices}
        if len(unique)!=1:return None
        states[point]=next(iter(unique));evidence.extend(i for _,i in choices)
    if states[previous_end]!=states[current_end]:return None
    cursor=start
    intervals=[]
    for v in observed:
        d=v['data']
        if not d.get('absence_observed_within_reporting_period'):continue
        if not (d.get('same_membership_and_ownership') and d.get('empty_change_table') and d.get('scope_proof') and d.get('identity_locations')):continue
        if signature(d['current_members'])!=states[current_end] or signature(d['previous_members'])!=states[current_end]:continue
        try:a,b=date.fromisoformat(d['period']['start']),date.fromisoformat(d['period']['end'])
        except (KeyError,ValueError):continue
        intervals.append((a,b,v['id']))
    for a,b,vid in sorted(intervals):
        if b<cursor:continue
        if a>cursor:return None
        evidence.append(vid);cursor=b+timedelta(days=1)
        if cursor>current_end:
            return {'status':'checked_no_change_found_in_collected_disclosures','validation_ids':sorted(set(evidence)),
                    'coverage_start':start.isoformat(),'coverage_end':current_end.isoformat(),
                    'basis':'same_original_members_and_ownership_plus_full_interval_change_observations'}
    return None
