"""원문에 명시된 신규 연결·제외를 기록하고 관련 연결 비교를 보류한다.

변동표의 대시만으로 변경 없음으로 판단하지 않는다. 기업 수 동일도 근거가 아니다.
"""
from datetime import date
from uuid import uuid4
from .document_extractor import normalize_label
from .source_locations import RawLocations, raw_text

VERSION = 'consolidation-change-1.1'
RULE = 'V03_scope_change_detected'


def collect_disclosed_changes(source, content, identity, tables, raw_check):
    """회사·표지 기간을 확인한 호출자가 제공한 본문에서 명시된 변동만 수집한다."""
    start, end = identity.get('start'), identity.get('end')
    try:
        if date.fromisoformat(start) > date.fromisoformat(end):return []
    except (TypeError, ValueError):return []
    if not all(raw_check(content, identity.get('locations', {}).get(k)) for k in ('company','start','end')):
        return []
    index = RawLocations(content); results = []
    for fragment_id, table in tables:
        location = table['location']
        if not location.get('parser_text_matches_raw') or not raw_check(content, location):continue
        if '회사의개요' not in normalize_label('/'.join(table.get('section_path', []))):continue
        if not table['rows']:continue
        headers = table['rows'][0]['cells']
        if [normalize_label(x['text']) for x in headers] != ['구분','자회사','사유']:continue
        if not all(raw_check(content,c['location'],c['text']) for c in headers):continue
        # 표 직전의 '연결대상회사의 변동내용' 문단을 원문 바이트로 확인한다.
        previous = [(i,e) for i,e in enumerate(index.elements) if e.tag=='p' and e.end is not None
                    and e.end <= location['byte_start'] and location['byte_start']-e.end < 4096]
        if not previous:continue
        heading_index, _ = max(previous, key=lambda pair:pair[1].end)
        heading = raw_text(index.inner(heading_index)); heading_location=index.location(heading_index)
        if ('연결대상회사의변동내용' not in normalize_label(heading)
                or not raw_check(content,heading_location,heading)):continue
        active = None
        for row in table['rows'][1:]:
            cells={c['column']:c for c in row['cells']}
            if 1 in cells:
                category=cells[1];name=normalize_label(category['text'])
                active=(category,row['row']+category['rowspan']) if name in {'신규연결','연결제외'} else None
            if active is None or row['row'] >= active[1] or not all(k in cells for k in (2,3)):continue
            category=active[0];company=cells[2];reason=cells[3]
            if any(not x['text'].strip() or normalize_label(x['text']) in {'-','—','–','해당없음','없음','미해당','N/A'} for x in (company,reason)):continue
            if not any(word in normalize_label(reason['text']) for word in ('당기중','당반기중','당분기중')):continue
            if any(word in reason['text'] for word in ('예정','계획','검토','추진','이전')):continue
            if any(x.get('nested_table') or not raw_check(content,x['location'],x['text'])
                   for x in (category,company,reason)):continue
            results.append({'id':uuid4().hex,'fact_id':None,'rule_id':RULE,'status':'pass',
                'data':{'version':VERSION,'source_id':source['id'],'receipt_number':source['metadata']['receipt_number'],
                    'scope':'CFS','period':{'kind':'duration','start':start,'end':end},
                    'change_type':normalize_label(category['text']),'company_name':company['text'],
                    'disclosed_reason':reason['text'],'exact_change_date':None,
                    'fragment_id':fragment_id,'heading':{'text':heading,'location':heading_location},
                    'cells':{k:{'text':v['text'],'location':v['location']} for k,v in
                             [('category',category),('company',company),('reason',reason)]},
                    'identity_locations':identity['locations'],
                    'limitations':['변경 사실과 공시 대상 기간을 확인함. 정확한 편입일·동일 범위 조정 수치는 확인하지 않음']}})
    return results


def blocking_changes(inputs, changes):
    """기간이 겹치는 명시된 변동이 있으면 보수적으로 비교를 보류한다."""
    current, previous = inputs.get('current',{}), inputs.get('previous',{})
    if any(x.get('scope')!='CFS' for x in (current,previous)):return []
    cp,pp=current.get('period',{}),previous.get('period',{})
    try:
        current_end=date.fromisoformat(cp.get('as_of',cp.get('end')))
        previous_end=date.fromisoformat(pp.get('as_of',pp.get('end')))
        instant='as_of' in cp and 'as_of' in pp
        lower=previous_end if instant else min(date.fromisoformat(cp['start']),date.fromisoformat(pp['start']))
    except (TypeError,ValueError,KeyError):return []
    if previous_end >= current_end:return []
    result=[]
    for change in changes:
        if change.get('rule_id')!=RULE or change.get('status')!='pass':continue
        d=change.get('data',{});period=d.get('period',{})
        try:start,end=date.fromisoformat(period['start']),date.fromisoformat(period['end'])
        except (KeyError,ValueError,TypeError):continue
        if d.get('scope')!='CFS' or d.get('change_type') not in {'신규연결','연결제외'}:continue
        if not d.get('company_name') or not d.get('cells') or not d.get('identity_locations'):continue
        if start<=current_end and (end>lower if instant else end>=lower):result.append(change)
    return result
