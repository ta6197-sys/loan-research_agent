"""차입 계정의 검증 다섯 축과 이용 범위를 독립적으로 관리한다."""
from decimal import Decimal
from itertools import combinations
from .borrowing_details import RULE as DETAIL_RULE, assess_detail, vector

VERSION='borrowing-verification-levels-1.1'
DIMENSIONS=('source_amount','detail_total','composition_identity','reclassification','lender_allocation')
STATES={'pass','fail','not_checked','not_applicable'}


def usage_scope(dimensions):
    if set(dimensions)!=set(DIMENSIONS) or any(d.get('status') not in STATES for d in dimensions.values()):
        raise ValueError('invalid_independent_verification_dimensions')
    linked=all(dimensions[k]['status']=='pass' for k in DIMENSIONS[:3]) and dimensions['reclassification']['status'] in {'pass','not_applicable'}
    return {'verified_source_amount':dimensions['source_amount']['status']=='pass',
            'account_total_link':linked,
            'lender_maturity_analysis':linked and dimensions['lender_allocation']['status']=='pass'}


def review_levels(review,records):
    d=review['data'];account=d['account'];observations=d['observations'];details=[v for v in records if v.get('rule_id')==DETAIL_RULE]
    dimensions={k:{'status':'not_checked','evidence_ids':[]} for k in DIMENSIONS}
    dimensions['source_amount']={'status':'pass' if observations and all(o.get('numeric_validation_id') for o in observations) else 'not_checked',
                                 'evidence_ids':[o['numeric_validation_id'] for o in observations]}
    used=[];vectors=[];profile_vectors=[];missing=False;identity_missing=False;detail_bad=False;reclass_bad=False;reclass_missing=False
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
            complete=bool(names) and not data.get('unresolved') and len(set(n for n,a in vector(data['components'])))==len(names)
            identity_missing|=not complete
            if complete:
                current_vector=vector(data['components']);vectors.append(current_vector);profile_vectors.append((candidate['id'],current_vector))
            if Decimal(data['account_amounts'][account])!=Decimal(observation['value']):detail_bad=True
            if account!='short_term_borrowings':
                reclass_bad|=checks['reclassification']['status']=='fail'
                reclass_missing|=checks['reclassification']['status']!='pass'
    evidence=sorted(set(used))
    dimensions['detail_total']={'status':'fail' if detail_bad else ('not_checked' if missing else 'pass'),'evidence_ids':evidence}
    names_equal=bool(vectors) and all(v==vectors[0] for v in vectors)
    codes={o.get('official_account_id') for o in observations};values={Decimal(o['value']) for o in observations}
    equal=names_equal and len(codes)==1 and None not in codes and len(values)==1 and len({o['source_id'] for o in observations})>=2
    identity='not_checked' if identity_missing else ('pass' if equal else 'fail')
    differences=[]
    for (left,a),(right,b) in combinations(profile_vectors,2):
        if a==b:continue
        differences.append({'left_detail_id':left,'right_detail_id':right,
            'left_only':[{'name':n,'amount':str(v)} for n,v in a if (n,v) not in b],
            'right_only':[{'name':n,'amount':str(v)} for n,v in b if (n,v) not in a]})
    dimensions['composition_identity']={'status':identity,'differences':differences,
                                         'limitation':'정확한 명칭·금액의 일치 여부이며 대출의 실제 경제적 동일성/상이성을 추정한 판정은 아님','evidence_ids':evidence,'basis':'all_explicit_components_exact_names_and_amounts_same_context',
                                         'numeric_equality_alone_sufficient':False}
    dimensions['reclassification']={'status':'not_applicable' if account=='short_term_borrowings' else (
        'fail' if reclass_bad else 'not_checked' if reclass_missing else 'pass'),
        'evidence_ids':[] if account=='short_term_borrowings' else evidence,
        'reason':'short_term_account' if account=='short_term_borrowings' else 'gross_current_deduction_noncurrent_account_totals_only'}
    dimensions['lender_allocation']={'status':'not_applicable' if account=='short_term_borrowings' else 'not_checked',
                                     'evidence_ids':[],'reason':'long_term_current_noncurrent_allocation_not_applicable' if account=='short_term_borrowings' else 'per_lender_current_noncurrent_allocations_not_confirmed'}
    return {'version':VERSION,'dimensions':dimensions,'usage_scope':usage_scope(dimensions)}


def confirmed_caption_link(a,b,reviews,records):
    for review in reviews:
        if review.get('rule_id')!='V12_account_composition_review' or review.get('status')!='pass':continue
        d=review['data']
        if {a,b}<=set(d.get('affected_fact_ids',[])):
            levels=review_levels(review,records)
            if levels==d.get('verification_levels') and levels['usage_scope']['account_total_link']:return review['id']
    return None
