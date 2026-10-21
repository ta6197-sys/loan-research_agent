"""평가 전 고정한 기준과 게시 결과를 비교한다. 미실행/보류는 성공이 아니다."""
from collections import Counter
from decimal import Decimal,InvalidOperation


KEY=('scope','end','account')


def row_key(row):
    return tuple(row.get(k) for k in KEY)


def grade_report(report,reference):
    content=report['content'];context=content['context'];errors=[]
    expected={row_key(r):r for r in reference['rows']}
    if len(expected)!=len(reference['rows']):raise ValueError('duplicate_reference_row')
    rows=context['financial_rows'];counts=Counter(row_key(r) for r in rows)
    for key,count in counts.items():
        if count!=1:errors.append({'kind':'duplicate_row','key':key})
    published=confirmed=held=missing=0
    for row in rows:
        key=row_key(row);target=expected.get(key)
        if target is None:errors.append({'kind':'unexpected_row','key':key});continue
        if row['status']=='verified':
            published+=1
            if target['eligibility']!='verified':errors.append({'kind':'unsafe_hold_promotion','key':key})
            try:equal=Decimal(row['value'])==Decimal(target['value'])
            except (KeyError,InvalidOperation,TypeError):equal=False
            if not equal or row.get('currency')!='KRW' or row.get('unit')!='원':
                errors.append({'kind':'value_or_unit_error','key':key})
            elif target['eligibility']=='verified':confirmed+=1
            if not row.get('fact_ids') or not row.get('validation_ids') or not row.get('source_ids'):
                errors.append({'kind':'missing_numeric_evidence','key':key})
        elif row['status']=='withheld':
            held+=1
            if 'value' in row:errors.append({'kind':'withheld_has_value','key':key})
            if target['eligibility']=='verified':missing+=1;errors.append({'kind':'public_numeric_omission','key':key})
        else:errors.append({'kind':'invalid_row_state','key':key})
    for key,target in expected.items():
        if key not in counts:
            missing+=1;errors.append({'kind':'missing_row','key':key})
    required_unknowns=set(reference['unknown_ids'])
    unknowns=[u.get('evidence_id') for u in content.get('unknowns',[])]
    if set(unknowns)!=required_unknowns or len(unknowns)!=len(required_unknowns):errors.append({'kind':'unknown_omission_or_duplicate'})
    sections=context.get('sections',[])
    if [s.get('id') for s in sections]!=reference['section_ids']:errors.append({'kind':'section_omission_or_order'})
    question_issues=[]
    questions=content.get('questions',[])
    cited=set()
    for q in questions:
        cited.update(q.get('evidence_ids',[]))
        if not all(q.get(k) for k in ('question','reason','requested_information','evidence_ids','period_scope')):
            question_issues.append('missing_question_context')
    if not required_unknowns<=cited:question_issues.append('missing_issue_question')
    # 검색 발췌에 특정 용어가 있는 것은 사실 주장/전체 문맥 검토의 증명이 아니다.
    narrative=[]
    for item in reference.get('narrative_checks',[]):
        section=next((s for s in sections if s['id']==item['section']),{})
        snippets=' '.join(str(f.get('snippet','')) for f in section.get('source_fragments',[]))
        matches=[v for v in item['needles'] if v in snippets]
        finding=next((f for f in context.get('public_findings',[]) if f['id']==item['id'] and f['status']=='verified_excerpt'),None)
        quoted=' '.join(q.get('quote','') for q in (finding or {}).get('quotes',[]))
        if item['id']=='business_structure':
            quoted=' '.join(f.get('quote',{}).get('quote','') for f in context.get('business_review',{}).get('items',[]) if f.get('status')=='verified_excerpt')
        quoted_matches=[v for v in item['needles'] if v in quoted]
        quoted_complete=bool(item['needles']) and len(quoted_matches)==len(item['needles'])
        narrative.append({'id':item['id'],'section':item['section'],
            'status':'quoted_source_rows_require_original_check' if quoted_complete else 'candidate_excerpt_requires_review' if matches else 'not_demonstrated',
            'matched_needles':quoted_matches if quoted_complete else matches,'source_reference':item['source_reference']})
    return {'numeric_status':'pass' if not errors else 'fail','numeric_errors':errors,
        'reference_row_count':len(expected),'published_numeric_count':published,'matched_eligible_count':confirmed,
        'withheld_count':held,'public_numeric_omission_count':missing,
        'financial_confirmation':'incomplete','unknown_count':len(unknowns),'section_count':len(sections),
        'question_count':len(questions),'question_structure_errors':question_issues,
        'question_semantic_quality':'not_independently_evaluated',
        'narrative_coverage':narrative,'overall_acceptance':'not_demonstrated',
        'limitation':'숫자 대조/질문 필드 검사는 전체 원인 설명·공개 항목 충족·질문 유용성을 인증하지 않는다.'}


class FixedSequenceGraph:
    """평가 전용 순차 실행기. 같은 노드를 한 번씩 실행하며 재계획하지 않는다.

    비교는 같은 최초 조사 로직의 실행 방식 차이로 한정한다. 별도 조사 지능을
    구현한 독립 기준선이 아니며 고정 검색 코드/검증기를 두 군이 공유한다.
    """
    ORDER=('plan','research','write','validate_and_publish')

    def __init__(self,*args):self.nodes={};self.edges=[]
    def add_node(self,name,node):self.nodes[name]=node
    def add_edge(self,source,target):self.edges.append((source,target))
    def compile(self):
        if tuple(self.nodes)!=self.ORDER or self.edges!=list(zip(('__start__',*self.ORDER),(*self.ORDER,'__end__'))):
            raise ValueError('fixed_evaluation_workflow_changed')
        return self
    async def ainvoke(self,state):
        current=dict(state)
        for name in self.ORDER:current.update(await self.nodes[name](current))
        return current
