"""사채 포함 표현의 구성 불명확성을 숫자 일치와 분리하고 관련 계산을 보류한다."""
from collections import defaultdict
from uuid import uuid4
from .document_extractor import normalize_label
from .verification_levels import review_levels

VERSION='composition-review-1.1'
RULE='V12_account_composition_review'
ACCOUNTS={'short_term_borrowings','current_long_term_borrowings','long_term_borrowings'}


def composition_reviews(facts,validations,detail_records=()):
    supported={v['fact_id']:v for v in validations if v.get('fact_id') and v['status']=='pass'}
    groups=defaultdict(list)
    for fact in facts:
        d=fact['data']
        if fact['id'] not in supported or d['account'] not in ACCOUNTS or d['period'].get('kind')!='instant':continue
        groups[(fact['company']['corp_code'],fact['run_id'],d['scope'],d['account'],d['period']['as_of'],d['currency'])].append(fact)
    results=[]
    for (company,run_id,scope,account,as_of,currency),items in groups.items():
        included={'사채포함' in normalize_label(f['data']['original_account']) for f in items}
        if included!={False,True}:continue
        observations=[]
        for fact in items:
            v=supported[fact['id']]
            observations.append({'fact_id':fact['id'],'numeric_validation_id':v['id'],'source_id':fact['source']['id'],
                'receipt_number':fact['source'].get('metadata',{}).get('receipt_number'),
                'original_account':fact['data']['original_account'],'official_account_id':v['data'].get('account_id'),
                'value':v['data']['amount_comparison']['original_normalized'],
                'row_label_location':fact['fragment']['data']['context']['row_label_location'],
                'source_content_hash':fact['source'].get('content_hash'),
                'source_specific_composition_check':v['data'].get('checks',{}).get('V12')})
        results.append({'id':uuid4().hex,'fact_id':None,'rule_id':RULE,'status':'not_checked','data':{
            'version':VERSION,'reason':'bond_inclusion_caption_scope_requires_component_confirmation',
            'company_code':company,'run_id':run_id,'scope':scope,'account':account,'period':{'kind':'instant','as_of':as_of},'currency':currency,
            'affected_fact_ids':[f['id'] for f in items],'observations':observations,
            'numeric_equality_is_not_composition_equality':True,
            'use_status':'withheld_pending_component_confirmation',
            'limitation':'표현 차이가 실제 구성 변경이라는 확정도, 같은 구성이라는 확정도 아님. 원문별 숫자는 보존하며 조사 사용·합산·관련 비교를 보류'}})
    for review in results:
        levels=review_levels(review,detail_records)
        review['data']['verification_levels']=levels
        if levels['usage_scope']['account_total_link']:
            review['status']='pass'
            review['data']['reason']='exact_detail_composition_and_required_reclassification_confirmed'
            review['data']['use_status']='account_totals_only'
            review['data']['limitation']='상세 대출 구성과 필요한 계정 재분류만 확인함. 은행별 상환 배분·사채 부재 전체를 확인한 것으로 확대하지 않음'
    return results


def affected_reviews(inputs,reviews):
    ids={item.get('fact_id') for item in inputs.values()}
    return [v for v in reviews if v.get('rule_id')==RULE and v.get('status')=='not_checked'
            and ids & set(v.get('data',{}).get('affected_fact_ids',[]))]


def withhold_affected_calculations(calculations,reviews):
    for calculation in calculations:
        formula=calculation['formula_version'].split(':')[-1]
        if not (formula.startswith('V11_change_') or formula.startswith('V12_')):continue
        data=calculation['data'];affected=affected_reviews(data.get('inputs',{}),reviews)
        if not affected:continue
        calculation['status']='not_checked'
        if 'result' in data:data['unverified_arithmetic_result']=data.pop('result')
        if data.get('deferred_reason'):data.setdefault('other_deferred_reasons',[]).append(data['deferred_reason'])
        data['deferred_reason']='account_composition_review_required'
        data['account_composition_review_ids']=sorted(v['id'] for v in affected)
        data['investigation_use_status']='withheld_pending_component_confirmation'
