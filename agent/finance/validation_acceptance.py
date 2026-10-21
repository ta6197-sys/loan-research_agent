"""상신브레이크 보관 사례의 회귀 수용 판정. 금융 검증 전체 완료와 구분한다."""
from collections import Counter
from datetime import date
from decimal import Decimal, DecimalException

from .extraction_service import ACCOUNT_RULES
from .composition_review import RULE as COMPOSITION_REVIEW_RULE, affected_reviews, calculation_composition_usage
from .borrowing_details import RULE as DETAIL_RULE, assess_detail
from .verification_levels import review_levels, confirmed_caption_link
from .borrowing_composition import RULE as COMPOSITION_RULE, LINK_RULE, verify_three_caption_subtotal, total
from .account_equivalence import verified_alias_pair
from .document_extractor import normalize_label
from .scope_observations import RULE as SCOPE_OBSERVATION_RULE, unchanged_scope_support
from .consolidation_checks import RULE as SCOPE_CHANGE_RULE, blocking_changes
from .xbrl_borrowing import valid_augmentation,replay_native_details

VERSION='stage04-acceptance-1.14'
PERIODS=('2023FY','2024FY','2025FY','2025H1','2026H1')
SCOPES=('OFS','CFS')
FLOW_ACCOUNTS={'revenue','operating_profit','net_profit','operating_cashflow'}
MANDATORY_CHECKS=('V01','V02','V03','V04','V05','V06','V07','V13','V18')
COMPARISONS={'V11_change_previous_year_end','V11_change_same_period','V11_change_prior_same_date_supplementary'}
BORROWING_SUBTOTALS={'V12_three_borrowing_captions_subtotal','V12_current_borrowing_captions_subtotal'}
KNOWN_MISSING_ORIGINAL_RECEIPTS={'20240315000633','20250314001195'}




def _linked_composition(composition, by_validation, by_fact):
    if not composition or composition.get('rule_id')!=COMPOSITION_RULE or composition.get('status')!='pass':return False
    data=composition.get('data',{});subtotal=data.get('subtotal',{})
    note=by_validation.get(data.get('note_validation_id'),{})
    nd=note.get('data',{})
    if note.get('rule_id')!='V12_note_amounts' or note.get('status')!='pass':return False
    if any(nd.get(k)!=data.get(k) for k in ('source_id','scope','period')):return False
    try:
        expected=verify_three_caption_subtotal(subtotal.get('components',{}),data.get('lease_exclusion_proof'))
        if any(subtotal.get(k)!=expected[k] for k in ('amount','components','lease_included','guarantee_included','total_financial_liabilities')):return False
        for account,amount in subtotal['components'].items():
            if not Decimal(amount).is_finite():return False
            check=nd.get('account_checks',{}).get(account,{})
            ids=data.get('account_fact_ids',{}).get(account,[])
            if check.get('status')!='pass' or not ids or set(ids)!=set(check.get('fact_ids',[])):return False
            if not check.get('note_cells') or any(Decimal(c['value'])!=Decimal(amount) for c in check['note_cells']):return False
            for fid in ids:
                validation=by_fact.get(fid,{});fact_data=validation.get('data',{})
                if validation.get('status')!='pass' or fact_data.get('account')!=account:return False
                if any(fact_data.get(k)!=data.get(k) for k in ('scope','period')):return False
                if Decimal(fact_data.get('amount_comparison',{}).get('original_normalized'))!=Decimal(amount):return False
    except (ValueError,TypeError,KeyError,DecimalException):return False
    return True



def _linked_composition_group(link,by_validation,by_fact):
    if not link or link.get('rule_id')!=LINK_RULE or link.get('status')!='pass':return False
    data=link.get('data',{});targets=data.get('account_fact_ids',{});candidates=data.get('supporting_links',[])
    required={'short_term_borrowings','current_long_term_borrowings','long_term_borrowings'}
    if set(targets)!=required or not candidates or any(len(ids)!=1 for ids in targets.values()):return False
    if data.get('composition_validation_id') not in {c.get('composition_validation_id') for c in candidates}:return False
    for candidate in candidates:
        composition=by_validation.get(candidate.get('composition_validation_id'))
        if not _linked_composition(composition,by_validation,by_fact):return False
        cd=composition['data']
        if any(cd.get(k)!=data.get(k) for k in ('scope','period')) or set(candidate.get('accounts',{}))!=required:return False
        for account,refs in candidate['accounts'].items():
            if refs.get('target_fact_id')!=targets[account][0] or refs.get('supporting_fact_id') not in cd['account_fact_ids'][account]:return False
            target=by_fact.get(refs['target_fact_id'],{});support=by_fact.get(refs['supporting_fact_id'],{})
            a,b=target.get('data',{}),support.get('data',{})
            if target.get('status')!='pass' or support.get('status')!='pass':return False
            if a.get('source_id')!=data.get('source_id') or b.get('source_id')!=cd.get('source_id'):return False
            if any(a.get(k)!=b.get(k) for k in ('scope','period','currency','account','account_id')):return False
            if not a.get('account_id') or a.get('account')!=account or a.get('currency')!=data.get('currency'):return False
            if not a.get('original_account'):return False
            if normalize_label(a['original_account'])!=normalize_label(b.get('original_account','')):
                review_id=confirmed_caption_link(refs['target_fact_id'],refs['supporting_fact_id'],by_validation.values(),by_validation.values())
                if not review_id or refs.get('composition_review_id')!=review_id:return False
            if any(a.get(k)!=data.get(k) for k in ('scope','period')):return False
            try:
                if Decimal(a['amount_comparison']['original_normalized'])!=Decimal(b['amount_comparison']['original_normalized']):return False
            except (KeyError,TypeError,ValueError,DecimalException):return False
            if any(v.get('data',{}).get('checks',{}).get('V01',{}).get('status')!='pass' for v in (target,support)):return False
    return True



def _detail_valid(record,by_fact,native_replay=None):
    d=record.get('data',{})
    try:
        if not valid_augmentation(d):return False
        if 'source_observation' in d and (native_replay is None or native_replay.get(record['id'])!=d):return False
        checks=assess_detail(d)
        if checks!=d.get('checks'):return False
        states=[v['status'] for v in checks.values()]
        expected='fail' if 'fail' in states else 'pass' if checks['detail_total']['status']=='pass' else 'not_checked'
        if record.get('status')!=expected:return False
        if not d.get('components') and not d.get('unresolved'):return False
        if not d.get('scope_proof') or not d.get('date_proofs') or not d.get('unit_evidence'):return False
        locations=[d.get('table_location',{}),d.get('total_location',{})]
        locations += [c[k] for c in d['components'] for k in ('name_location','amount_location')]
        if not all(v.get('raw_span_verified') and v.get('raw_span_sha256') for v in locations):return False
        for account,fid in d['account_fact_ids'].items():
            v=by_fact.get(fid,{})
            if v.get('status')!='pass':return False
            a=v['data']
            if a.get('source_id')!=d.get('source_id') or a.get('account')!=account:return False
            if any(a.get(k)!=d.get(k) for k in ('scope','period','currency')):return False
            if Decimal(a['amount_comparison']['original_normalized'])!=Decimal(d['account_amounts'][account]):return False
        return True
    except (KeyError,ValueError,TypeError,DecimalException):return False


def _review_valid(record,by_validation,by_fact):
    d=record.get('data',{})
    try:
        if set(d['affected_fact_ids'])!={o['fact_id'] for o in d['observations']}:return False
        for o in d['observations']:
            v=by_fact.get(o['fact_id'],{})
            if v.get('status')!='pass' or v.get('id')!=o['numeric_validation_id']:return False
            a=v['data']
            if any(a.get(k)!=d.get(k) for k in ('scope','period','currency','account')):return False
            if any(a.get(k)!=o.get(other) for k,other in [('source_id','source_id'),('account_id','official_account_id'),('original_account','original_account')]):return False
            if Decimal(a['amount_comparison']['original_normalized'])!=Decimal(o['value']):return False
        levels=review_levels(record,by_validation.values())
        if levels!=d.get('verification_levels'):return False
        return (record['status']=='pass')==levels['usage_scope']['account_total_link']
    except (KeyError,ValueError,TypeError,DecimalException):return False


def required_comparison_keys():
    expected=set()
    for scope in SCOPES:
        for account in ACCOUNT_RULES:
            for current,previous in [('2024-12-31','2023-12-31'),('2025-12-31','2024-12-31')]:
                formula='V11_change_same_period' if account in FLOW_ACCOUNTS else 'V11_change_previous_year_end'
                expected.add((scope,account,current,previous,formula))
            if account in FLOW_ACCOUNTS:
                expected.add((scope,account,'2026-06-30','2025-06-30','V11_change_same_period'))
            else:
                for current,previous,kind in [('2025-06-30','2024-12-31','previous_year_end'),
                                               ('2026-06-30','2025-12-31','previous_year_end'),
                                               ('2026-06-30','2025-06-30','prior_same_date_supplementary')]:
                    expected.add((scope,account,current,previous,'V11_change_'+kind))
    return expected


def _comparison_coverage(calculations,by_fact):
    expected=required_comparison_keys();observed=set()
    for calculation in calculations:
        formula=calculation.get('formula_version','').split(':')[-1]
        if formula not in COMPARISONS:continue
        inputs=calculation.get('data',{}).get('inputs',{})
        a=by_fact.get(inputs.get('current',{}).get('fact_id'),{}).get('data',{})
        b=by_fact.get(inputs.get('previous',{}).get('fact_id'),{}).get('data',{})
        if not a.get('account') or any(a.get(k)!=b.get(k) for k in ('scope','account')):continue
        pa,pb=a.get('period',{}),b.get('period',{})
        if a['account'] in FLOW_ACCOUNTS:
            current,previous=pa.get('end'),pb.get('end')
            if not current or not previous or pa.get('start')!=current[:4]+'-01-01' or pb.get('start')!=previous[:4]+'-01-01':continue
        else:current,previous=pa.get('as_of'),pb.get('as_of')
        key=(a.get('scope'),a['account'],current,previous,formula)
        if key in expected:observed.add(key)
    missing=sorted(expected-observed)
    return {'expected_count':len(expected),'observed_count':len(expected & observed),
            'missing':[{'scope':s,'account':a,'current_end':c,'previous_end':p,'formula':f} for s,a,c,p,f in missing]}


def _completion_sections(batch, reference, gaps, failures):
    """필수 5기간·2범위와 추가 추출 결과를 분리한다. 검사 결과 자체는 바꾸지 않는다.

    필수 참조가 가리키는 근거는 문맥 오류가 있어도 필수에서 제외하지 않는다.
    범위를 판단할 문맥이 없는 결과와 전역 검사는 보수적으로 필수에 둔다.
    """
    ends = {p[:4] + ('-06-30' if p.endswith('H1') else '-12-31') for p in PERIODS}
    reference_facts = {fid for item in reference.get('items', []) for fid in item.get('fact_ids', [])}
    sections = {name: {'observed_result_count': 0, 'known_gaps': Counter(), 'failures': []}
                for name in ('required', 'additional')}

    def valid_date(value):
        try:
            return isinstance(value,str) and len(value)==10 and date.fromisoformat(value).isoformat()==value
        except ValueError:
            return False

    def context_section(data):
        if data.get('scope') not in SCOPES:
            return 'required'
        period = data.get('period', {})
        if period.get('kind') == 'instant' or 'as_of' in period:
            end = period.get('as_of')
            if not valid_date(end):
                return 'required'
            return 'required' if end in ends else 'additional'
        end, start = period.get('end'), period.get('start')
        if not valid_date(end):
            return 'required'
        # 현금 연결 등의 계산 문맥에는 종료일만 있는 경우가 있다.
        if start is None:
            return 'required' if end in ends else 'additional'
        if not valid_date(start) or start > end:
            return 'required'
        return 'required' if end in ends and start == end[:4] + '-01-01' else 'additional'

    linked_groups={(v['data'].get('source_id'),v['data'].get('scope'),v['data'].get('period',{}).get('as_of'))
                   for v in batch.get('validations',[]) if v.get('rule_id')==LINK_RULE and v.get('status')=='pass'}
    by_validation = {}
    for item in batch.get('validations', []):
        section = ('required' if item.get('fact_id') in reference_facts
                   else context_section(item.get('data', {})))
        if item.get('rule_id')=='V12_note_amounts' and item.get('status')=='not_checked':
            data=item.get('data',{})
            if (data.get('source_id'),data.get('scope'),data.get('period',{}).get('as_of')) in linked_groups:section='additional'
        by_validation[item.get('id')] = section
        sections[section]['observed_result_count'] += 1
    by_calculation = {}
    for item in batch.get('calculations', []):
        data = item.get('data', {})
        inputs = data.get('inputs', {})
        section = context_section(data)
        if inputs:
            section = ('required' if all(context_section(x) == 'required' for x in inputs.values())
                       else 'additional')
            if any(x.get('fact_id') in reference_facts for x in inputs.values()):
                # 같은 기간끼리의 비교는 양쪽이 필수 기간일 때 필수다.
                formula = item.get('formula_version', '').split(':')[-1]
                if formula not in COMPARISONS:
                    section = 'required'
        by_calculation[item.get('id')] = section
        sections[section]['observed_result_count'] += 1
    # gap 기록은 검증/계산 ID를 갖는다. 전역 미구현 등은 필수다.
    for gap in gaps:
        section = (by_validation.get(gap.get('validation_id'))
                   or by_calculation.get(gap.get('calculation_id')) or 'required')
        sections[section]['known_gaps'][gap['reason']] += 1
    for failure in failures:
        section = (by_validation.get(failure.get('validation_id'))
                   or by_calculation.get(failure.get('calculation_id')) or 'required')
        sections[section]['failures'].append(failure)
    for name, section in sections.items():
        present = section['observed_result_count'] > 0 or section['known_gaps'] or section['failures']
        section['known_gaps'] = dict(section['known_gaps'])
        section['status'] = ('incomplete' if section['known_gaps'] or section['failures'] else
                             ('complete' if present else 'not_present'))
        label = '필수 항목' if name == 'required' else '추가 항목'
        section['label'] = label + {'complete': ' 완료', 'incomplete': ' 미완료', 'not_present': ' 없음'}[section['status']]
    return sections


def assess_stage04(batch,reference,*,optional_balance_dates=(),store=None):
    """0: 정한 회귀 조건 통과(미검증 별도), 1: 오류/필수 누락/예상 밖 보류.

    이 판정은 자료 자체의 금융적 진실이나 조사 전체의 완료를 인증하지 않는다.
    """
    failures=[];gaps=Counter();gap_items=[]
    def gap(reason, *, validation_id=None, calculation_id=None):
        gaps[reason]+=1
        gap_items.append({'reason':reason,'validation_id':validation_id,'calculation_id':calculation_id})
    expected={(p,s,a) for p in PERIODS for s in SCOPES for a in ACCOUNT_RULES}
    items=reference.get('items',[])
    keys=[(r.get('period'),r.get('scope'),r.get('account')) for r in items]
    counts=Counter(keys)
    if set(keys)!=expected or any(n!=1 for n in counts.values()):
        failures.append({'reason':'required_reference_coverage_mismatch',
                         'missing_count':len(expected-set(keys)),'unexpected_count':len(set(keys)-expected),
                         'duplicate_count':sum(n-1 for n in counts.values())})
    validations=batch.get('validations',[])
    by_fact={v['fact_id']:v for v in validations if v.get('fact_id')}
    by_validation={v.get('id'):v for v in validations}
    native_replay={}
    if any('source_observation' in v.get('data',{}) for v in validations):
        if store is not None:
            try:native_replay=replay_native_details(store,batch)
            except (ValueError,KeyError,TypeError,DecimalException):
                failures.append({'reason':'native_borrowing_original_replay_failed'})
    if not by_fact:failures.append({'reason':'no_numeric_validations'})
    for item in items:
        identity={k:item.get(k) for k in ('period','scope','account')}
        if item.get('status')!='pass':
            failures.append({'reason':'required_reference_not_passed',**identity})
        refs=item.get('fact_ids',[])
        if not refs:failures.append({'reason':'required_reference_evidence_missing',**identity})
        for fid in refs:
            v=by_fact.get(fid);d=v.get('data',{}) if v else {}
            if not v or v.get('status')!='pass':
                failures.append({'reason':'required_reference_evidence_not_passed',**identity});continue
            period=item.get('period','');year=period[:4]
            end=year+('-06-30' if period.endswith('H1') else '-12-31')
            evidence_period=d.get('period',{})
            temporal=(evidence_period.get('start')==year+'-01-01' and evidence_period.get('end')==end
                      if item.get('account') in FLOW_ACCOUNTS else evidence_period.get('as_of')==end)
            if d.get('account')!=item.get('account') or d.get('scope')!=item.get('scope') or not temporal:
                failures.append({'reason':'reference_evidence_context_mismatch',**identity})
    missing_originals=Counter()
    for v in validations:
        status=v.get('status');d=v.get('data',{})
        if status=='fail':
            failures.append({'reason':'numeric_validation_failed','validation_id':v.get('id')});continue
        if v.get('rule_id')=='numeric_fact':
            if status!='pass':
                failures.append({'reason':'numeric_validation_not_passed','validation_id':v.get('id')})
            checks=d.get('checks',{})
            if any(checks.get(rule,{}).get('status')!='pass' for rule in MANDATORY_CHECKS):
                failures.append({'reason':'mandatory_numeric_check_not_passed','validation_id':v.get('id')})
            if any(x.get('status')=='fail' or x.get('status')=='not_checked' and rule!='V12'
                   for rule,x in checks.items()):
                failures.append({'reason':'unresolved_numeric_check','validation_id':v.get('id')})
            reviews=[r for r in validations if r.get('rule_id')==COMPOSITION_REVIEW_RULE
                     and r.get('data',{}).get('version') in {'composition-review-1.1','composition-review-1.2','composition-review-1.3'}
                     and v['fact_id'] in r['data'].get('affected_fact_ids',[])]
            if reviews or d.get('composition_verification_levels') is not None:
                if (len(reviews)!=1 or d.get('composition_verification_levels')!=reviews[0]['data'].get('verification_levels')
                        or d.get('investigation_use_status')!=reviews[0]['data'].get('use_status')):
                    failures.append({'reason':'numeric_verification_levels_missing_or_mismatched','validation_id':v.get('id')})
            if checks.get('V12',{}).get('status')=='pass':
                composition=by_validation.get(checks['V12'].get('composition_validation_id'))
                valid_composition=_linked_composition(composition,by_validation,by_fact)
                link_id=checks['V12'].get('cross_source_link_validation_id')
                if link_id:
                    link=by_validation.get(link_id)
                    valid_composition=valid_composition and _linked_composition_group(link,by_validation,by_fact)
                    valid_composition=valid_composition and v['fact_id'] in link['data']['account_fact_ids'].get(d.get('account'),[])
                else:
                    valid_composition=valid_composition and v['fact_id'] in composition['data']['account_fact_ids'].get(d.get('account'),[])
                if not valid_composition:failures.append({'reason':'borrowing_composition_link_missing_or_invalid','validation_id':v.get('id')})
            if checks.get('V12',{}).get('status')=='not_checked':gap('borrowing_note_composition',validation_id=v.get('id'))
        elif v.get('rule_id')==DETAIL_RULE:
            if not _detail_valid(v,by_fact,native_replay):failures.append({'reason':'borrowing_detail_evidence_invalid','validation_id':v.get('id')})
            elif status not in {'pass','not_checked'}:failures.append({'reason':'borrowing_detail_status_invalid','validation_id':v.get('id')})
        elif v.get('rule_id')==COMPOSITION_REVIEW_RULE:
            if d.get('version') in {'composition-review-1.1','composition-review-1.2','composition-review-1.3'} or status=='pass':
                if not _review_valid(v,by_validation,by_fact):failures.append({'reason':'independent_composition_verification_invalid','validation_id':v.get('id')})
            if status=='not_checked' and d.get('reason')=='bond_inclusion_caption_scope_requires_component_confirmation':
                gap('account_composition_requires_confirmation',validation_id=v.get('id'))
            elif status!='pass':failures.append({'reason':'unexpected_composition_review_status','validation_id':v.get('id')})
        elif v.get('rule_id')==LINK_RULE and status=='pass':
            if not _linked_composition_group(v,by_validation,by_fact):
                failures.append({'reason':'cross_source_composition_link_invalid','validation_id':v.get('id')})
        elif v.get('rule_id')==COMPOSITION_RULE and status=='pass':
            if not _linked_composition(v,by_validation,by_fact):
                failures.append({'reason':'borrowing_composition_evidence_invalid','validation_id':v.get('id')})
        elif v.get('rule_id') in {SCOPE_CHANGE_RULE,SCOPE_OBSERVATION_RULE} and status=='pass':
            continue
        elif v.get('rule_id')=='V12_note_amounts' and status=='pass':
            continue
        elif v.get('rule_id')=='V12_note_amounts' and status=='not_checked' and d.get('reason')=='borrowing_note_evidence_incomplete':
            gap('borrowing_note_amount_evidence_incomplete',validation_id=v.get('id'))
        elif (v.get('rule_id')=='V07_source_coverage' and status=='not_checked'
              and d.get('reason')=='matching_original_not_available'
              and d.get('receipt_number') in KNOWN_MISSING_ORIGINAL_RECEIPTS):
            missing_originals[d['receipt_number']]+=1;gap('matching_original_missing',validation_id=v.get('id'))
        else:
            failures.append({'reason':'unexpected_validation_result','validation_id':v.get('id')})
    if any(n>2 for n in missing_originals.values()):failures.append({'reason':'unexpected_missing_original_count'})
    calculations=batch.get('calculations',[])
    if not calculations:failures.append({'reason':'no_calculations'})
    required_calculations={(family,scope,end) for family in ('V08','V09','V10') for scope in SCOPES
                           for end in ('2025-06-30','2025-12-31','2026-06-30')}
    observed_calculations=set()
    for c in calculations:
        status=c.get('status');d=c.get('data',{});formula=c.get('formula_version','').split(':')[-1]
        if formula in COMPARISONS:
            pair=d.get('inputs',{});a=by_fact.get(pair.get('current',{}).get('fact_id'),{}).get('data',{})
            b=by_fact.get(pair.get('previous',{}).get('fact_id'),{}).get('data',{})
            equivalence=d.get('account_equivalence')
            if equivalence or (a.get('original_account') and b.get('original_account') and normalize_label(a['original_account'])!=normalize_label(b['original_account'])):
                expected_equivalence=verified_alias_pair(a.get('account_comparison_context'),b.get('account_comparison_context'))
                if expected_equivalence is None or equivalence!=expected_equivalence:
                    failures.append({'reason':'account_equivalence_evidence_missing_or_invalid','calculation_id':c.get('id')})
        material_reviews=affected_reviews(d.get('inputs',{}),validations)
        if (formula in COMPARISONS or formula in BORROWING_SUBTOTALS) and material_reviews:
            if (status!='not_checked' or d.get('deferred_reason')!='account_composition_review_required'
                    or set(d.get('account_composition_review_ids',[]))!={v['id'] for v in material_reviews}
                    or 'result' in d):
                failures.append({'reason':'account_composition_withholding_missing_or_invalid','calculation_id':c.get('id')})
                continue
        expected_usage=calculation_composition_usage(c,validations)
        if d.get('composition_usage')!=expected_usage:
            failures.append({'reason':'calculation_composition_usage_missing_or_invalid','calculation_id':c.get('id')})
        if status=='pass':
            if formula in BORROWING_SUBTOTALS:
                composition=by_validation.get(d.get('composition_validation_id'))
                expected_accounts=({'short_term_borrowings','current_long_term_borrowings','long_term_borrowings'}
                    if formula=='V12_three_borrowing_captions_subtotal' else {'short_term_borrowings','current_long_term_borrowings'})
                linked=_linked_composition(composition,by_validation,by_fact)
                try:
                    linked=linked and set(d.get('inputs',{}))==expected_accounts and set(d.get('included_accounts',[]))==expected_accounts
                    linked=linked and d.get('lease_included') is False and d.get('guarantee_included') is False and d.get('total_financial_liabilities') is False
                    link=by_validation.get(d.get('cross_source_link_validation_id'))
                    if d.get('cross_source_link_validation_id'):linked=linked and _linked_composition_group(link,by_validation,by_fact)
                    if linked:
                        for role,item in d['inputs'].items():
                            linked=linked and item.get('fact_id') in (link['data']['account_fact_ids'][role] if link else composition['data']['account_fact_ids'][role])
                            linked=linked and Decimal(item['value'])==Decimal(composition['data']['subtotal']['components'][role])
                        linked=linked and Decimal(d['result'])==total(item['value'] for item in d['inputs'].values())
                except (ValueError,TypeError,KeyError,DecimalException):linked=False
                if not linked:failures.append({'reason':'borrowing_subtotal_evidence_or_components_invalid','calculation_id':c.get('id')})
            if formula in COMPARISONS and any(x.get('scope')=='CFS' for x in d.get('inputs',{}).values()):
                proof=unchanged_scope_support(d.get('inputs',{}),validations)
                if proof is None or d.get('scope_review')!=proof:
                    failures.append({'reason':'consolidation_comparison_proof_missing_or_mismatched','calculation_id':c.get('id')})
            role={'V08_assets_equals_liabilities_plus_equity':'expected',
                  'V09_cf_end_equals_bs_cash':'cash_end','V10_cash_rollforward':'cash_begin'}.get(formula)
            if role:
                anchor=d.get('inputs',{}).get(role,{})
                period=anchor.get('period',{})
                observed_calculations.add((formula[:3],anchor.get('scope'),period.get('as_of',period.get('end'))))
            continue
        if status!='not_checked':
            failures.append({'reason':'calculation_not_passed','calculation_id':c.get('id')});continue
        if d.get('deferred_reason')=='account_composition_review_required':
            reviews=affected_reviews(d.get('inputs',{}),validations)
            if reviews and set(d.get('account_composition_review_ids',[]))=={v['id'] for v in reviews} and 'result' not in d:
                gap('comparison_or_subtotal_withheld_for_account_composition',calculation_id=c.get('id'))
                continue
            failures.append({'reason':'account_composition_withholding_evidence_invalid','calculation_id':c.get('id')})
            continue
        detected=blocking_changes(d.get('inputs',{}),validations)
        review=d.get('scope_review',{})
        if (formula in COMPARISONS and d.get('deferred_reason')=='disclosed_consolidation_change_comparison_withheld'
                and detected and review.get('status')=='checked_change_detected'
                and review.get('comparison_status')=='withheld' and review.get('same_scope_adjustment_available') is False
                and set(review.get('validation_ids',[]))=={v['id'] for v in detected} and 'result' not in d):
            continue
        if formula in BORROWING_SUBTOTALS and d.get('deferred_reason')=='borrowing_note_composition_required':
            gap('borrowing_subtotal_not_verified',calculation_id=c.get('id'))
        elif (formula in COMPARISONS and d.get('deferred_reason')=='consolidation_scope_review_required'
              and d.get('inputs') and all(x.get('scope')=='CFS' for x in d['inputs'].values())):
            gap('consolidation_comparison_not_verified',calculation_id=c.get('id'))
        elif (formula=='V09_cf_end_equals_bs_cash' and d.get('reason')=='missing_ambiguous_or_unverified_inputs'
              and d.get('scope') in SCOPES and d.get('period',{}).get('end') in {'2024-06-30',*optional_balance_dates}
              and d.get('missing_or_unverified_inputs')==['cash_at_same_end_date']):
            gap('cash_at_'+d['period']['end']+'_missing',calculation_id=c.get('id'))
        else:
            failures.append({'reason':'unexpected_calculation_gap','calculation_id':c.get('id')})
    if required_calculations-observed_calculations:
        failures.append({'reason':'required_calculation_coverage_missing',
                         'missing_count':len(required_calculations-observed_calculations)})
    scope=batch.get('summary',{}).get('rule_scope',{})
    for rule in ('V12','latest_correction_history','consolidation_scope_changes'):
        evidence_rule={'V12':('per_group_evidence',COMPOSITION_RULE),
                       'consolidation_scope_changes':('per_comparison_evidence',SCOPE_OBSERVATION_RULE)}.get(rule)
        evidence_available=(evidence_rule and scope.get(rule)==evidence_rule[0]
                            and any(v.get('rule_id')==evidence_rule[1] and v.get('status')=='pass' for v in validations))
        if scope.get(rule)!='checked' and not evidence_available:gap(rule+'_not_completed')
    completion=_completion_sections(batch,reference,gap_items,failures)
    comparison_coverage=None
    if scope.get('consolidation_scope_changes')=='per_comparison_evidence':
        comparison_coverage=_comparison_coverage(calculations,by_fact)
        if comparison_coverage['missing']:
            failure={'reason':'required_comparison_coverage_missing','missing_count':len(comparison_coverage['missing'])}
            failures.append(failure)
            completion=_completion_sections(batch,reference,gap_items,failures)
    passed=not failures
    return {'policy_version':VERSION,'regression_status':'pass' if passed else 'fail',
            'financial_validation_status':'incomplete' if gaps or failures else 'checked_within_test_scope',
            'stage4_complete':False if gaps or failures else True,
            'completion':completion,'required_comparison_coverage':comparison_coverage,
            'required_complete':completion['required']['status']=='complete',
            'additional_complete':(None if completion['additional']['status']=='not_present' else
                                   completion['additional']['status']=='complete'),
            'exit_code':0 if passed else 1,
            'required_reference_count':len(expected),'observed_reference_count':len(items),
            'reference_status_counts':dict(Counter(r.get('status','missing') for r in items)),
            'known_gaps':dict(gaps),'failures':failures}
