"""수집한 전체 공시 목록에서 대상 보고서의 정정 후보를 검사한다. 추정 연결은 하지 않는다."""
from collections import defaultdict
import calendar
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from lxml import etree

from .dart_protocol import DartFailure, zip_members

VERSION='disclosure-audit-1.0'


def analysis_receipt_policy(audit, target_reports):
    """전체 목록에서 개별 보고서 상태가 확정된 경우에만 통과 접수건을 반환한다."""
    expected={item.get('rcept_no') for item in target_reports}
    reviewed=audit.get('targets', [])
    receipts=[item.get('receipt_number') for item in reviewed]
    if (not expected or None in expected or len(expected)!=len(target_reports)
            or len(receipts)!=len(target_reports) or set(receipts)!=expected
            or audit.get('reason')!='checked_within_collected_official_history'
            or not audit.get('evidence')):
        raise ValueError('complete_disclosure_history_required')
    accepted=[];withheld=[]
    for item in reviewed:
        if item.get('status')=='pass':
            accepted.append(item['receipt_number'])
        elif (item.get('status')=='not_checked'
              and item.get('reason')=='correction_candidate_requires_original_relation_review'):
            withheld.append(item['receipt_number'])
        else:
            raise ValueError('disclosure_history_status_unresolved')
    if not accepted or (audit.get('status')=='pass') != (not withheld):
        raise ValueError('disclosure_history_status_inconsistent')
    return {'status':'partial_withheld' if withheld else 'pass',
            'allowed_receipts':sorted(accepted), 'withheld_receipts':sorted(withheld),
            'reason_code':'correction_relation_unconfirmed' if withheld else None}


def apply_relation_evidence(store, audit, policy):
    """Resolve a held receipt only against a separately preserved, rechecked official original."""
    accepted=set(policy['allowed_receipts'])
    remaining=[];confirmed=[]
    for corrected in policy['withheld_receipts']:
        path=store.source_root/'audits'/'disclosure_relations'/(corrected+'.json')
        if not path.is_file():
            remaining.append(corrected);continue
        try:
            proof=json.loads(path.read_bytes())
            original=proof['original_receipt']
            target=next(t for t in audit['targets'] if t['receipt_number']==corrected)
            if (proof['status']!='relation_supported' or proof['public_cutoff']!=audit['public_cutoff']
                    or target['status']!='not_checked'
                    or target['reason']!='correction_candidate_requires_original_relation_review'
                    or set(target['candidates'])!={original,corrected}
                    or len(target['candidates'])!=2 or original>=corrected
                    or not all(proof['checks'].values())):
                raise ValueError('relation_manifest_mismatch')
            listing_source=store.get_source(proof['official_list_source_id'])
            corrected_source=store.get_source(proof['corrected_source_id'])
            listing=json.loads(store.source_content(listing_source['id']))
            corrected_raw=store.source_content(corrected_source['id'])
            if (listing_source['content_hash']!=proof['official_list_hash']
                    or corrected_source['content_hash']!=proof['corrected_source_hash']
                    or corrected_source['metadata']['receipt_number']!=corrected
                    or listing_source['id'] not in {e['source_id'] for e in audit['evidence']}):
                raise ValueError('stored_source_mismatch')
            rows=[r for r in listing['list'] if r.get('rcept_no') in {original,corrected}]
            if (len(rows)!=2 or sorted(rows,key=lambda r:r['rcept_no'])!=proof['official_list_pair']):
                raise ValueError('official_pair_mismatch')
            original_row=next(r for r in rows if r['rcept_no']==original)
            corrected_row=next(r for r in rows if r['rcept_no']==corrected)
            family=re.search(r'(사업보고서|반기보고서|분기보고서)\s*\((20\d{2})\.(\d{2})\)',original_row['report_nm'])
            if ({r['corp_code'] for r in rows}!={proof['company_code']}
                    or len([r for r in listing['list'] if r.get('corp_code')==proof['company_code']
                            and r.get('rcept_dt')==original[:8]
                            and r.get('report_nm')==original_row['report_nm']])!=1
                    or not family or family.group(0) not in corrected_row['report_nm']
                    or '정정' not in corrected_row['report_nm']
                    or corrected_row['rcept_dt']!=corrected[:8]
                    or original_row['rcept_dt']!=original[:8]):
                raise ValueError('official_history_mismatch')
            relative=Path(proof['original_archive_path'])
            if relative.is_absolute() or relative.parts[:3]!=('data','research','audits'):
                raise ValueError('archive_path_mismatch')
            archive=(store.source_root/Path(*relative.parts[2:])).resolve()
            if not archive.is_relative_to((store.source_root/'audits').resolve()):
                raise ValueError('archive_path_mismatch')
            if (not archive.is_file() or hashlib.sha256(archive.read_bytes()).hexdigest()!=proof['original_archive_hash']):
                raise ValueError('archive_mismatch')
            members=zip_members(archive.read_bytes(),max_expanded_bytes=256*1024*1024,max_members=100)
            raw=next(raw for name,raw in members if name==proof['original_member_name']==original+'.xml')
            if hashlib.sha256(raw).hexdigest()!=proof['original_member_hash']:
                raise ValueError('original_hash_mismatch')
            def excerpt(data):
                if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                    raise ValueError('unsafe_document')
                root=etree.fromstring(data,etree.XMLParser(resolve_entities=False,no_network=True,recover=True))
                return ' '.join([' '.join(t.split()) for t in root.itertext() if t and t.strip()][:30])
            start=excerpt(raw);correction=excerpt(corrected_raw)
            original_date=date.fromisoformat(original[:8])
            named_date=f'{original_date.year}년 {original_date.month}월 {original_date.day}일'
            year,month=int(family.group(2)),int(family.group(3))
            end_day=calendar.monthrange(year,month)[1]
            if (original_row['corp_name'] not in start
                    or not any(f'{year}년 {mm}월 {dd}일' in start
                               for mm in (str(month),f'{month:02d}')
                               for dd in (str(end_day),f'{end_day:02d}'))
                    or '정정대상 공시서류의 최초제출일 : '+named_date not in correction):
                raise ValueError('document_identity_mismatch')
        except (OSError, ValueError, KeyError, StopIteration, TypeError, AttributeError,
                IndexError, DartFailure, etree.XMLSyntaxError):
            remaining.append(corrected);continue
        accepted.add(corrected);confirmed.append({'receipt_number':corrected,'original_receipt':original,
                                                   'evidence_path':str(path.relative_to(store.source_root)),
                                                   'original_archive_hash':proof['original_archive_hash']})
    return {**policy,'status':'partial_withheld' if remaining else 'pass',
            'allowed_receipts':sorted(accepted),'withheld_receipts':sorted(remaining),
            'reason_code':'correction_relation_unconfirmed' if remaining else None,
            'confirmed_relations':confirmed}


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
