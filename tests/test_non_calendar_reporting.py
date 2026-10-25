import hashlib
import json
from pathlib import Path
import unittest

from agent.finance.change_analysis import _period_pairs as change_pairs
from agent.finance.financial_analytics import _period_pairs as analytics_pairs
from agent.finance.general_collection import declared_taxonomy_references
from agent.finance.report_context import fiscal_periods,numeric_rows,xbrl_report_gate,charts
from agent.finance.report_exports import tables
from agent.finance.research_stage05 import Stage05Service
from agent.finance.validation_acceptance import MANDATORY_CHECKS
from agent.finance.xbrl_mapping import current_period


def targets(annual_year,half_year):
    return [{'year':y,'report_code':'11011'} for y in (annual_year-1,annual_year)] + [
        {'year':y,'report_code':'11012'} for y in (half_year-1,half_year)]


def validation(identifier,period,*,value='100',account='revenue',status='pass'):
    return {'id':identifier,'fact_id':'fact-'+identifier,'rule_id':'numeric_fact','status':status,
        'data':{'account':account,'scope':'OFS','period':period,'source_id':'raw-'+identifier,
            'api_source_id':'api-'+identifier,'currency':'KRW',
            'amount_comparison':{'original_normalized':value,'api_value':value},
            'checks':{key:{'status':'pass'} for key in MANDATORY_CHECKS}}}


class FiscalReportingTests(unittest.TestCase):
    def test_periods_for_march_june_september_and_december(self):
        expected={
            3:(('2025-04-01','2026-03-31'),('2025-04-01','2025-09-30')),
            6:(('2025-07-01','2026-06-30'),('2025-07-01','2025-12-31')),
            9:(('2025-10-01','2026-09-30'),('2024-10-01','2025-03-31')),
            12:(('2025-01-01','2025-12-31'),('2026-01-01','2026-06-30')),
        }
        for month,(annual,half) in expected.items():
            with self.subTest(month=month):
                annual_year=int(annual[1][:4]);half_year=int(half[1][:4])
                periods=fiscal_periods(targets(annual_year,half_year),month)
                self.assertEqual(annual,(periods[1]['start'],periods[1]['end']))
                self.assertEqual(half,(periods[-1]['start'],periods[-1]['end']))
                self.assertEqual({'kind':'duration','start':annual[0],'end':annual[1]},
                    current_period(str(annual_year),'11011','CIS',month))
                self.assertEqual({'kind':'instant','as_of':half[1]},
                    current_period(str(half_year),'11012','BS',month))

    def test_period_start_mismatch_and_other_period_are_not_reported(self):
        periods=fiscal_periods(targets(2026,2025),6)
        valid=validation('correct',{'kind':'duration','start':'2025-07-01','end':'2026-06-30'},value='100')
        wrong_start=validation('wrong',{'kind':'duration','start':'2026-01-01','end':'2026-06-30'},value='900')
        wrong_end=validation('other',{'kind':'duration','start':'2025-01-01','end':'2025-12-31'},value='700')
        batch={'validations':[valid,wrong_start,wrong_end]}
        ends=tuple(p['end'] for p in periods)
        rows=numeric_rows(batch,ends,periods)
        current=next(r for r in rows if r['scope']=='OFS' and r['end']=='2026-06-30' and r['account']=='revenue')
        self.assertEqual(('verified','100',['correct']),
                         (current['status'],current['value'],current['validation_ids']))
        self.assertFalse(any('wrong' in r['validation_ids'] or 'other' in r['validation_ids'] for r in rows))

    def test_comparable_pairs_follow_period_not_calendar_suffix(self):
        periods=fiscal_periods(targets(2026,2025),6)
        rows=[{'scope':'OFS','account':'revenue','end':p['end'],'period':
            {'kind':'duration','start':p['start'],'end':p['end']},'status':'verified','value':'100'}
            for p in periods]
        self.assertIn(('2025-06-30','2026-06-30','annual'),change_pairs(rows))
        self.assertIn(('2024-12-31','2025-12-31','interim'),change_pairs(rows))
        index={(r['scope'],r['account'],r['end']):r for r in rows}
        self.assertIn(('2025-06-30','2026-06-30'),analytics_pairs(index,'OFS'))
        self.assertIn(('2024-12-31','2025-12-31'),analytics_pairs(index,'OFS'))
        rows[-1]['period']['start']='2025-01-01'
        self.assertNotIn(('2024-12-31','2025-12-31','interim'),change_pairs(rows))

    def test_xbrl_gate_requires_independent_account_period_and_amount(self):
        period={'kind':'duration','start':'2025-07-01','end':'2026-06-30','column_kind':'declared_period'}
        item=validation('v1',period)
        row={'scope':'OFS','end':'2026-06-30','account':'revenue','status':'verified',
             'validation_ids':['v1']}
        binding={'id':'x1','rule_id':'XBRL_STANDARD_CONCEPT','data':{
            'account':'revenue','scope':'OFS','period':{'kind':'duration','start':'2025-07-01','end':'2026-06-30'},
            'currency':'KRW','api_value':'100','concept_status':'pass','context_status':'pass','amount_status':'pass'}}
        batch={'validations':[item]};mapping={'validations':[binding]}
        self.assertEqual('pass',xbrl_report_gate([row],batch,mapping)['status'])
        for field,value in (('account','operating_profit'),('period',{'kind':'duration','start':'2025-01-01','end':'2026-06-30'}),
                            ('api_value','101'),('context_status','not_checked')):
            with self.subTest(field=field):
                changed=json.loads(json.dumps(binding));changed['data'][field]=value
                self.assertEqual('withheld',xbrl_report_gate([row],batch,{'validations':[changed]})['status'])
        service=object.__new__(Stage05Service)
        with self.assertRaisesRegex(ValueError,'independent_xbrl_period_account_amount_unconfirmed'):
            service._publish('run','key',{'context':{'comparison_periods':[{'end':'2026-06-30'}],
                'fiscal_end_month':6,'xbrl_report_gate':{'status':'withheld'}}},'batch')

    def test_chart_and_export_periods_come_from_fiscal_plan(self):
        periods=fiscal_periods(targets(2026,2025),6)
        ends=tuple(p['end'] for p in periods)
        rows=numeric_rows({'validations':[]},ends,periods)
        produced=charts(rows,{'calculations':[]},{},ends,periods)
        self.assertEqual(['2025-06-30','2026-06-30'],
            [p['end'] for p in produced[0]['points'][::2]])
        exported=tables({'financial_rows':rows,'comparison_periods':periods})
        self.assertEqual(list(ends[:2]),exported[0]['ends'])
        self.assertEqual(list(ends[2:]),exported[2]['ends'])

    def test_declared_dart_taxonomy_is_selected_only_from_instance_entity_schema(self):
        instance=(b'<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" '
            b'xmlns:link="http://www.xbrl.org/2003/linkbase" '
            b'xmlns:xlink="http://www.w3.org/1999/xlink"><link:schemaRef '
            b'xlink:href="entity.xsd"/></xbrli:xbrl>')
        schema=(b'<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">'
            b'<xs:import schemaLocation="http://dart.fss.or.kr/Resource/Taxonomy/ifrs/test.xsd"/>'
            b'<xs:import schemaLocation="https://example.org/other.xsd"/></xs:schema>')
        members=[{'name':'entity.xbrl','source_id':'instance'},{'name':'entity.xsd','source_id':'schema'}]
        self.assertEqual([('schema','http://dart.fss.or.kr/Resource/Taxonomy/ifrs/test.xsd')],
            declared_taxonomy_references(members,{'instance':instance,'schema':schema}.__getitem__))


class IndustryEvidenceTests(unittest.TestCase):
    def test_factory_records_do_not_become_legal_entity_members(self):
        root=Path(__file__).resolve().parents[1]
        dart=json.loads((root/'doc/finance/KSIC30392_OFFICIAL_CANDIDATES_20261025.json').read_text())
        factory=json.loads((root/'doc/finance/KSIC30392_FACTORY_CANDIDATES_20261026.json').read_text())
        prior=root/'doc/finance/COHORT_CANDIDATE_AUDIT_20261025.json'
        self.assertEqual(dart['prior_audit_sha256'],hashlib.sha256(prior.read_bytes()).hexdigest())
        self.assertEqual(13,len(dart['records']))
        self.assertTrue(all(r['candidate_status']=='ksic5_revision_review_required' for r in dart['records']))
        self.assertTrue(all(r['industry_scope']=='factory' and r['corp_code'] is None and
                            r['candidate_status'] in {'identity_review_required','ksic5_unverified'}
                            for r in factory['records']))


if __name__=='__main__':unittest.main()
