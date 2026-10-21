"""평가기가 보류 승격·누락·오류를 점수로 숨기지 않는지 확인한다."""
from copy import deepcopy
import unittest
from agent.finance.evaluation import grade_report,FixedSequenceGraph


class EvaluationTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        row={'scope':'OFS','end':'2026-06-30','account':'cash','value':'9007199254740993.1',
            'status':'verified','currency':'KRW','unit':'원','fact_ids':['f'],'validation_ids':['v'],'source_ids':['s']}
        ref={'rows':[{**row,'eligibility':'verified'}],'unknown_ids':['U1'],'section_ids':['finance'],
            'narrative_checks':[{'id':'note','section':'finance','needles':['소송'],'source_reference':'synthetic'}]}
        report={'content':{'context':{'financial_rows':[row],'sections':[{'id':'finance','source_fragments':[]}]},
            'unknowns':[{'evidence_id':'U1'}],'questions':[{'question':'자료?','reason':'부족','requested_information':'명세',
                'evidence_ids':['U1'],'period_scope':'2026-06-30'}]}}
        return report,ref

    async def test_exact_numeric_success_does_not_claim_full_acceptance_or_narrative_review(self):
        report,ref=self.fixture();r=grade_report(report,ref)
        self.assertEqual('pass',r['numeric_status']);self.assertEqual('not_demonstrated',r['overall_acceptance'])
        self.assertEqual('not_demonstrated',r['narrative_coverage'][0]['status'])

    async def test_wrong_values_scope_dates_units_duplicates_and_omissions_are_failures(self):
        for kind in ('value','scope','end','unit','duplicate','missing','withheld','unknown','evidence'):
            report,ref=self.fixture();row=report['content']['context']['financial_rows'][0]
            if kind in ('value','scope','end','unit'):row[kind]='wrong'
            elif kind=='duplicate':report['content']['context']['financial_rows'].append(deepcopy(row))
            elif kind=='missing':report['content']['context']['financial_rows']=[]
            elif kind=='withheld':row['status']='withheld';row.pop('value')
            elif kind=='unknown':report['content']['unknowns']=[]
            else:row['fact_ids']=[]
            self.assertEqual('fail',grade_report(report,ref)['numeric_status'],kind)

    async def test_safety_hold_is_not_public_coverage_and_promotion_is_rejected(self):
        report,ref=self.fixture();ref['rows'][0]['eligibility']='withheld'
        self.assertEqual('fail',grade_report(report,ref)['numeric_status'])
        row=report['content']['context']['financial_rows'][0];row['status']='withheld';row.pop('value')
        r=grade_report(report,ref);self.assertEqual(0,r['matched_eligible_count']);self.assertEqual(1,r['withheld_count'])

    async def test_keyword_excerpt_is_not_semantic_verification(self):
        report,ref=self.fixture();report['content']['context']['sections'][0]['source_fragments']=[{'snippet':'소송'}]
        self.assertEqual('candidate_excerpt_requires_review',grade_report(report,ref)['narrative_coverage'][0]['status'])

    async def test_linear_control_rejects_different_plan_and_executes_each_node_once(self):
        graph=FixedSequenceGraph();called=[]
        for name in graph.ORDER:
            async def node(state,name=name):called.append(name);return {name:True}
            graph.add_node(name,node)
        for a,b in zip(('__start__',*graph.ORDER),(*graph.ORDER,'__end__')):graph.add_edge(a,b)
        self.assertTrue((await graph.compile().ainvoke({}))['write']);self.assertEqual(list(graph.ORDER),called)
        graph.edges.pop()
        with self.assertRaises(ValueError):graph.compile()
