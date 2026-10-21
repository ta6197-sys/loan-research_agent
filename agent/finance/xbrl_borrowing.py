"""승인된 동일 공시의 명시적0 근거와 공시 항목 대응을 제한적으로 연결한다.

본문/기존 후보/과거 검증은 수정하지 않는다. 새 검증의 source_observation에
기존 상세를 보존하고 유효 구성과 원문 상태를 구분한다. 은행 법인·계약 동일성은 판정하지 않는다.
"""
from copy import deepcopy
from decimal import Decimal
import hashlib
from .document_extractor import normalize_label
from .borrowing_details import assess_detail
from agent.research_store import encode

VERSION='xbrl-borrowing-evidence-1.0'
LONG_LOCAL='LongTermBorrowingsOfDisclosureOfLongTermBorrowingDetailsLineItemsOfDisclosureOfLongTermBorrowingDetailsTableOfItems'


def display_names(candidate):
    values=[]
    for item in candidate.get('member_labels',[]):
        if item.get('language','').lower().startswith('ko'):
            text=item['text']
            for suffix in (' [구성요소]',' [member]'):
                if text.endswith(suffix):text=text[:-len(suffix)]
            values.append(normalize_label(text))
    return set(values)


def eligible(candidate,data):
    c=candidate.get('context') or {};u=candidate.get('unit') or {}
    d=candidate.get('concept') or {};m=candidate.get('member_definition') or {}
    if (not c.get('valid') or c.get('entity')!=data['company_code']
            or c.get('scope')!=data['scope'] or c.get('period')!=data['period']
            or u.get('currency')!=data['currency'] or candidate.get('other_dimensions')
            or candidate.get('typed_dimension_count') or candidate.get('nil')
            or candidate.get('exact_value') is None or d.get('period_type')!='instant'
            or d.get('abstract') or d.get('type')!='{http://www.xbrl.org/2003/instance}monetaryItemType'):
        return False
    member_id=m.get('schema_id','')
    if not member_id.startswith('entity'+data['company_code']+'_') or not m.get('declaration'):
        return False
    if data['family']=='short':
        return d.get('schema_id')=='ifrs-full_ShorttermBorrowings'
    return d.get('schema_id')=='entity'+data['company_code']+'_'+LONG_LOCAL


def match_component(name,value,pool):
    matching=[c for c in pool if normalize_label(name) in display_names(c)]
    if not matching:return None
    identities={(c['concept']['expanded_name'],c['borrowing_member'],c['exact_value']) for c in matching}
    if len(identities)!=1 or any(Decimal(c['exact_value'])!=Decimal(value) for c in matching):return None
    return matching[0]


def binding_valid(component,data):
    c=component.get('xbrl_binding')
    return bool(c and eligible(c,data) and normalize_label(component['name']) in display_names(c)
                and Decimal(c['exact_value'])==Decimal(component['amount'])
                and c.get('receipt_number')==data.get('receipt_number')
                and c.get('source_hash_verified') and c.get('location',{}).get('content_hash')
                and c.get('location',{}).get('xpath'))


def native_profile_vector(data):
    """전체 구성이 확인된 경우에만 공시 구성요소 ID로 잔액을 대조한다."""
    components=data.get('components',[]);pool=data.get('xbrl_profile_pool')
    if not components or pool is None or data.get('unresolved'):return None
    if any(not binding_valid(c,data) for c in components):return None
    if assess_detail(data)['detail_total']['status']!='pass':return None
    keys=[c['xbrl_binding']['member_definition']['schema_id'] for c in components]
    if len(keys)!=len(set(keys)):return None
    # XBRL 쪽 양수 항목도 모두 포함한다. 합계 일치로 누락이나 다른 은행을 숨기지 않는다.
    native_positive={(c['member_definition']['schema_id'],Decimal(c['exact_value'])) for c in pool
                     if eligible(c,data) and Decimal(c['exact_value'])>0}
    observed_positive={(c['xbrl_binding']['member_definition']['schema_id'],Decimal(c['amount'])) for c in components
                       if Decimal(c['amount'])>0}
    if native_positive!=observed_positive:return None
    if any(not eligible(c,data) or not c.get('source_hash_verified') for c in pool):return None
    values={}
    for c in pool:
        key=c['member_definition']['schema_id'];values.setdefault(key,set()).add(Decimal(c['exact_value']))
    if any(len(v)!=1 for v in values.values()):return None
    prefix=encode({k:data[k] for k in ('company_code','scope','period','currency','family')})
    return sorted((prefix+':'+key,Decimal(c['amount'])) for key,c in zip(keys,components))


def valid_augmentation(data):
    original=data.get('source_observation')
    if original is None:return True
    if data.get('xbrl_evidence_policy')!=VERSION:return False
    components=data.get('components',[]);raw_components=original.get('components',[])
    if len(components)<len(raw_components):return False
    for before,after in zip(raw_components,components):
        if any(after.get(k)!=v for k,v in before.items()):return False
        if after.get('xbrl_binding') and not binding_valid(after,data):return False
    resolved=data.get('resolved_unresolved_items',[])
    if len(components)!=len(raw_components)+len(resolved):return False
    indices=[r['original_index'] for r in resolved]
    if len(indices)!=len(set(indices)):return False
    original_missing=original.get('unresolved',[])
    if data.get('unresolved')!=[r for i,r in enumerate(original_missing) if i not in indices]:return False
    for item,component in zip(resolved,components[len(raw_components):]):
        i=item['original_index']
        if not 0<=i<len(original_missing) or item['original_item']!=original_missing[i]:return False
        if (component.get('amount_origin')!='same_report_xbrl_explicit_zero'
                or Decimal(component['amount'])!=0 or component['name']!=item['original_item'].get('name')
                or not binding_valid(component,data) or not component['xbrl_binding'].get('explicit_zero')):
            return False
    return True


def attach_evidence(detail_records,reports,sources_by_id):
    for record in detail_records:
        data=record['data'];source=sources_by_id[data['source_id']]
        receipt=source['metadata'].get('receipt_number')
        report=reports.get(receipt)
        if not report:continue
        probe={**data,'receipt_number':receipt}
        pool=[]
        for candidate in report.get('borrowing_note_candidates',[]):
            if eligible(candidate,probe):
                pool.append({**candidate,'receipt_number':receipt,'source_hash_verified':True})
        if not pool:continue
        original={'components':deepcopy(data['components']),'unresolved':deepcopy(data['unresolved'])}
        effective=deepcopy(data['components'])
        for component in effective:
            match=match_component(component['name'],component['amount'],pool)
            if match:component['xbrl_binding']=deepcopy(match)
        pending=[];resolved=[]
        for i,item in enumerate(data['unresolved']):
            locations=item.get('locations') or ([item['location']] if item.get('location') else [])
            match=match_component(item.get('name',''),'0',pool)
            if (not match or not match['explicit_zero'] or not locations
                    or not item.get('name_location') or not item.get('purpose_location',item.get('name_location'))):
                pending.append(item);continue
            effective.append({'name':item['name'],'amount':'0','name_location':item['name_location'],
                'purpose_location':item.get('purpose_location',item['name_location']),
                'amount_location':locations[0],'amount_origin':'same_report_xbrl_explicit_zero',
                'original_document_value_still_missing':True,'xbrl_binding':deepcopy(match)})
            resolved.append({'original_index':i,'original_item':deepcopy(item)})
        data.update(source_observation=original,components=effective,unresolved=pending,
            xbrl_profile_pool=pool,receipt_number=receipt,xbrl_evidence_policy=VERSION,
            resolved_unresolved_items=resolved)
        data['checks']=assess_detail(data)
        if not valid_augmentation(data):raise ValueError('invalid_native_detail_augmentation')
        states=[x['status'] for x in data['checks'].values()]
        record['status']='fail' if 'fail' in states else 'pass' if data['checks']['detail_total']['status']=='pass' else 'not_checked'


def native_usage_counts(detail_records):
    return {'native_bound_detail_count':sum(native_profile_vector(v['data']) is not None for v in detail_records),
            'explicit_zero_supplement_count':sum(len(v['data'].get('resolved_unresolved_items',[])) for v in detail_records)}
