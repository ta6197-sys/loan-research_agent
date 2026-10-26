"""수집한 전체 공시 목록에서 대상 보고서의 정정 후보를 검사한다. 추정 연결은 하지 않는다."""
from collections import defaultdict
from datetime import date
import json
import re

VERSION='disclosure-audit-1.0'


def audit_disclosure_history(store,sources,company,basis,target_receipts):
    groups=defaultdict(list);cutoff=basis['data']['public_cutoff']
    required_begin=f'{date.fromisoformat(cutoff).year-3:04d}-01-01'
    for source in sources:
        meta=source['metadata'];q=meta.get('reporting_period',{})
        if q.get('operation')!='disclosures':continue
        if (q.get('corp_code')!=company.get('corp_code') or q.get('last_reprt_at')!='N'
                or q.get('bgn_de','9999')>required_begin or q.get('end_de')!=cutoff):continue
        payload=json.loads(store.source_content(source['id']))
        groups[(q['bgn_de'],q['end_de'],q.get('page_count'))].append((source,payload,q))
    complete=[];errors=[]
    for members in groups.values():
        pages={};total=None;count=None;bad=False
        for source,payload,q in members:
            try:
                page=int(payload['page_no']);n=int(payload['total_page']);size=int(payload['total_count'])
                if page!=int(q['page_no']) or total is not None and (total!=n or count!=size):bad=True;break
                total=n;count=size
                if page in pages and pages[page][1]['list']!=payload['list']:bad=True;break
                pages[page]=(source,payload)
            except (KeyError,TypeError,ValueError):bad=True;break
        if bad or not total or set(pages)!=set(range(1,total+1)):continue
        rows=[];proof=[]
        for page,(source,payload) in sorted(pages.items()):
            proof.append({'source_id':source['id'],'content_hash':source['content_hash'],'page':page})
            rows.extend(payload['list'])
        if len(rows)!=count or len({x.get('rcept_no') for x in rows})!=count:continue
        for row in rows:
            try:allowed=date.fromisoformat(row['rcept_dt']).isoformat()<=cutoff
            except (ValueError,KeyError):allowed=False
            if row.get('corp_code')!=company.get('corp_code') or not allowed:bad=True;break
        if not bad:complete.append((rows,proof))
    if not complete:return {'version':VERSION,'status':'not_checked','reason':'complete_history_not_available','targets':[],'evidence':[]}
    signatures={json.dumps(sorted(rows,key=lambda r:r['rcept_no']),ensure_ascii=False,sort_keys=True) for rows,proof in complete}
    if len(signatures)!=1:return {'version':VERSION,'status':'not_checked','reason':'history_snapshots_conflict','targets':[],'evidence':[]}
    rows,proof=complete[0];by_receipt={r['rcept_no']:r for r in rows};families=defaultdict(list)
    def family(row):
        match=re.search(r'(사업보고서|반기보고서|분기보고서)\s*\((20\d{2}\.\d{2})\)',row.get('report_nm',''))
        return tuple(match.groups()) if match else None
    for row in rows:
        if family(row):families[family(row)].append(row)
    targets=[]
    for receipt in sorted(target_receipts):
        row=by_receipt.get(receipt);status='pass';reason='no_correction_candidate_in_complete_cutoff_list'
        if row is None or family(row) is None:status='not_checked';reason='target_missing_from_complete_history'
        elif (len(families[family(row)])!=1 or '정정' in row.get('report_nm','') or '정' in row.get('rm','')):
            status='not_checked';reason='correction_candidate_requires_original_relation_review'
        targets.append({'receipt_number':receipt,'status':status,'reason':reason,
                        'candidates':[x['rcept_no'] for x in families.get(family(row),[])] if row else []})
    return {'version':VERSION,'status':'pass' if targets and all(t['status']=='pass' for t in targets) else 'not_checked',
            'reason':'checked_within_collected_official_history','public_cutoff':cutoff,'row_count':len(rows),
            'targets':targets,'evidence':proof,'limitation':'목록이 제공하지 않은 관계를 추정하지 않으며 기준일 이후 정보는 포함하지 않음'}
