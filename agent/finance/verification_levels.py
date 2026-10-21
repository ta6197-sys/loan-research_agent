"""차입 계정의 검증 다섯 축과 이용 범위를 독립적으로 관리한다."""
from decimal import Decimal
from itertools import combinations
from .borrowing_details import RULE as DETAIL_RULE, assess_detail, vector
from .xbrl_borrowing import native_profile_vector,valid_augmentation

VERSION='borrowing-verification-levels-1.3'
DIMENSIONS=('source_amount','detail_total','composition_identity','reclassification','lender_allocation')
STATES={'pass','fail','not_checked','not_applicable'}


def usage_scope(dimensions):
    if set(dimensions)!=set(DIMENSIONS) or any(d.get('status') not in STATES for d in dimensions.values()):
        raise ValueError('invalid_independent_verification_dimensions')
    identity=dimensions['composition_identity'];full=identity.get('all_disclosed_items');positive=identity.get('positive_balance_items')
    if full is not None or positive is not None:
        if not full or not positive or any(x.get('status') not in STATES for x in (full,positive)):
            raise ValueError('invalid_scoped_composition_identity')
        native=identity.get('native_disclosure_balance_items')
        if native and native.get('status') not in STATES:raise ValueError('invalid_native_identity')
        basis=('all_disclosed_items' if full['status']=='pass' else 'positive_balance_items' if positive['status']=='pass'
               else 'native_disclosure_balance_items' if native and native['status']=='pass' else 'none')
        identity_pass=basis!='none';all_items_match=full['status']=='pass'
    else:
        basis=None;identity_pass=identity['status']=='pass';all_items_match=identity_pass
    linked=all(dimensions[k]['status']=='pass' for k in DIMENSIONS[:2]) and identity_pass and dimensions['reclassification']['status'] in {'pass','not_applicable'}
    result={'verified_source_amount':dimensions['source_amount']['status']=='pass',
            'account_total_link':linked,
            'lender_maturity_analysis':linked and all_items_match and dimensions['lender_allocation']['status']=='pass'}
    if basis is not None:
        result.update(account_total_identity_basis=basis if linked else 'none',all_disclosed_items_match=all_items_match)
    return result



def review_levels(review,records):
    d=review['data'];native_policy=d.get('version')=='composition-review-1.3';balance_policy=d.get('version') in {'composition-review-1.2','composition-review-1.3'};account=d['account'];observations=d['observations'];details=[v for v in records if v.get('rule_id')==DETAIL_RULE]
    dimensions={k:{'status':'not_checked','evidence_ids':[]} for k in DIMENSIONS}
    dimensions['source_amount']={'status':'pass' if observations and all(o.get('numeric_validation_id') for o in observations) else 'not_checked',
                                 'evidence_ids':[o['numeric_validation_id'] for o in observations]}
    used=[];vectors=[];profile_vectors=[];effective_vectors=[];native_vectors=[];raw_missing=False;missing=False;identity_missing=False;detail_bad=False;reclass_bad=False;reclass_missing=False
    for observation in observations:
        candidates=[v for v in details if v['data'].get('source_id')==observation['source_id']
                    and v['data'].get('account_fact_ids',{}).get(account)==observation['fact_id']
                    and all(v['data'].get(k)==d.get(k) for k in ('company_code','run_id','scope','period','currency'))]
        if not candidates:missing=True;identity_missing=True;reclass_missing=True;continue
        for candidate in candidates:
            data=candidate['data'];checks=assess_detail(data);used.append(candidate['id'])
            detail_bad|=checks['detail_total']['status']=='fail'
            missing|=checks['detail_total']['status']=='not_checked'
            names=[c['name'] for c in data['components']]
            complete=bool(names) and not data.get('unresolved') and len(set(n for n,a in vector(data['components'])))==len(names) and valid_augmentation(data)
            identity_missing|=not complete
            if complete:
                original=data.get('source_observation',data)
                raw_missing|=bool(original.get('unresolved'))
                current_vector=vector(original['components']);vectors.append(current_vector);profile_vectors.append((candidate['id'],current_vector))
                effective_vectors.append((candidate['id'],vector(data['components'])))
            native_vectors.append((candidate['id'],native_profile_vector(data)))
            if Decimal(data['account_amounts'][account])!=Decimal(observation['value']):detail_bad=True
            if account!='short_term_borrowings':
                reclass_bad|=checks['reclassification']['status']=='fail'
                reclass_missing|=checks['reclassification']['status']!='pass'
    evidence=sorted(set(used))
    dimensions['detail_total']={'status':'fail' if detail_bad else ('not_checked' if missing else 'pass'),'evidence_ids':evidence}
    names_equal=bool(vectors) and all(v==vectors[0] for v in vectors)
    codes={o.get('official_account_id') for o in observations};values={Decimal(o['value']) for o in observations}
    equal=names_equal and len(codes)==1 and None not in codes and len(values)==1 and len({o['source_id'] for o in observations})>=2
    identity='not_checked' if identity_missing or raw_missing else ('pass' if equal else 'fail')
    differences=[]
    for (left,a),(right,b) in combinations(profile_vectors,2):
        if a==b:continue
        differences.append({'left_detail_id':left,'right_detail_id':right,
            'left_only':[{'name':n,'amount':str(v)} for n,v in a if (n,v) not in b],
            'right_only':[{'name':n,'amount':str(v)} for n,v in b if (n,v) not in a]})
    dimensions['composition_identity']={'status':identity,'differences':differences,
                                         'limitation':'정확한 명칭·금액의 일치 여부이며 대출의 실제 경제적 동일성/상이성을 추정한 판정은 아님','evidence_ids':evidence,'basis':'all_explicit_components_exact_names_and_amounts_same_context',
                                         'numeric_equality_alone_sufficient':False}
    if balance_policy:
        positive_vectors=[(identifier,[(name,value) for name,value in values if value>0]) for identifier,values in effective_vectors]
        nonnegative=all(value>=0 for identifier,values in profile_vectors for name,value in values)
        available=bool(positive_vectors) and all(values for identifier,values in positive_vectors) and nonnegative
        context_equal=len(codes)==1 and None not in codes and len(values)==1 and len({o['source_id'] for o in observations})>=2
        balance_equal=available and context_equal and all(v==positive_vectors[0][1] for identifier,v in positive_vectors)
        balance_status='not_checked' if identity_missing or not available else 'pass' if balance_equal else 'fail'
        zero_items=[{'detail_validation_id':candidate['id'],'source_id':candidate['data']['source_id'],**component}
                    for candidate in details if candidate['id'] in evidence for component in candidate['data']['components']
                    if Decimal(component['amount'])==0]
        balance_differences=[]
        for (left,a),(right,b) in combinations(positive_vectors,2):
            if a!=b:
                balance_differences.append({'left_detail_id':left,'right_detail_id':right,
                    'left_only':[{'name':n,'amount':str(v)} for n,v in a if (n,v) not in b],
                    'right_only':[{'name':n,'amount':str(v)} for n,v in b if (n,v) not in a]})
        dimension=dimensions['composition_identity']
        dimension['all_disclosed_items']={'status':identity,'evidence_ids':evidence,'differences':differences}
        dimension['positive_balance_items']={'status':balance_status,'evidence_ids':evidence,'differences':balance_differences,
            'zero_items_preserved':zero_items,'requires_complete_explicit_components':True,'missing_values_excluded':False}
        dimension['status']='pass' if identity=='pass' else balance_status
        dimension['basis']='all_disclosed_items' if identity=='pass' else 'positive_balance_items'
        dimension['limitation']='명시적 0원 항목을 원문과 함께 보존한다. 양수 잔액 일치를 계약 항목 전체·은행별 상환 일정의 일치로 확대하지 않는다.'
        if native_policy:
            available_native=bool(native_vectors) and not identity_missing and all(v for _,v in native_vectors)
            native_equal=available_native and context_equal and all(v==native_vectors[0][1] for _,v in native_vectors)
            native_status='not_checked' if not available_native else 'pass' if native_equal else 'fail'
            dimension['native_disclosure_balance_items']={'status':native_status,'evidence_ids':evidence,
                'basis':'same_context_complete_items_individually_bound_to_each_disclosure',
                'contract_or_bank_identity_verified':False}
            if dimension['status']!='pass' and native_status=='pass':
                dimension['status']='pass';dimension['basis']='native_disclosure_balance_items'
    dimensions['reclassification']={'status':'not_applicable' if account=='short_term_borrowings' else (
        'fail' if reclass_bad else 'not_checked' if reclass_missing else 'pass'),
        'evidence_ids':[] if account=='short_term_borrowings' else evidence,
        'reason':'short_term_account' if account=='short_term_borrowings' else 'gross_current_deduction_noncurrent_account_totals_only'}
    dimensions['lender_allocation']={'status':'not_applicable' if account=='short_term_borrowings' else 'not_checked',
                                     'evidence_ids':[],'reason':'long_term_current_noncurrent_allocation_not_applicable' if account=='short_term_borrowings' else 'per_lender_current_noncurrent_allocations_not_confirmed'}
    return {'version':VERSION if native_policy else 'borrowing-verification-levels-1.2' if balance_policy else 'borrowing-verification-levels-1.1','dimensions':dimensions,'usage_scope':usage_scope(dimensions)}


def confirmed_caption_link(a,b,reviews,records):
    for review in reviews:
        if review.get('rule_id')!='V12_account_composition_review' or review.get('status')!='pass':continue
        d=review['data']
        if {a,b}<=set(d.get('affected_fact_ids',[])):
            levels=review_levels(review,records)
            if levels==d.get('verification_levels') and levels['usage_scope']['account_total_link']:return review['id']
    return None
