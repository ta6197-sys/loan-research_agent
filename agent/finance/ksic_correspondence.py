"""공식 KSIC 차수 근거를 적용하되 공장과 법인의 동일성은 판정하지 않는다."""

from copy import deepcopy


KSIC_11_10_SOURCE = {
    'source_url': 'https://mods.go.kr/boardDownload.es?bid=107&list_no=430619&seq=7',
    'source_sha256': 'a4e3485c7404bfae5d7f208b61123a3b8399cd151b789c038da7aaa6ce7ec0f3',
    'source_sheet': '구신연계표',
    'source_row': 492,
    'old_version': '10',
    'old_code': '30392',
    'old_name': '자동차용 신품 제동장치 제조업',
    'new_version': '11',
    'new_code': '30392',
    'new_name': '자동차용 신품 제동장치 제조업',
}


def reconcile_30392_candidate(candidate):
    """기존 후보의 차수별 업종 의미를 새 판정에 복사한다."""
    if candidate.get('corp_code') is not None or candidate.get('next_stage_allowed') is True:
        raise ValueError('candidate_identity_must_be_unverified')
    result = deepcopy(candidate)
    if result.get('industry_code') != '30392':
        raise ValueError('unexpected_industry_code')
    version = result.get('industry_classification_version')
    if version == '10':
        result['ksic_correspondence_evidence'] = dict(KSIC_11_10_SOURCE)
        result['ksic11_30392_meaning'] = 'supported'
        result['cohort_candidate_status'] = 'identity_review_required'
    elif version == '11':
        result['ksic11_30392_meaning'] = 'direct_source'
        result['cohort_candidate_status'] = 'identity_review_required'
    elif version == '9':
        result['ksic11_30392_meaning'] = 'not_equivalent'
        result['cohort_candidate_status'] = 'excluded_ksic9_30392_electrical_equipment'
        result['exclusion_reason'] = 'ksic9_30392_is_automotive_electrical_equipment'
    else:
        result['ksic11_30392_meaning'] = 'unverified'
        result['cohort_candidate_status'] = 'ksic_revision_review_required'
    # 업종 의미의 대응만으로 법인 식별이나 법인 업종을 통과 처리하지 않는다.
    result['corp_code'] = None
    result['next_stage_allowed'] = False
    return result
