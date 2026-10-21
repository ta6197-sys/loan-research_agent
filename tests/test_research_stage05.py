"""5단계 그래프·수치 보류·보고서 불변 버전과 잘못된 모델 출력의 차단을 검사한다."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from agent.finance.local_model import checked_draft
from agent.finance.research_stage05 import Stage05Service
from agent.finance.report_context import numeric_rows,svg_chart,charts,SECTIONS
from agent.finance.validation_acceptance import MANDATORY_CHECKS
from agent.research_models import FragmentInput,FactInput
from agent.research_store import ResearchStore,Conflict
from tests.test_research_storage import research_input,source_input


class FakeLocalModel:
    def __init__(self,forged=False):self.calls=0;self.forged=forged
    async def draft(self,evidence,*,before_request,**kwargs):
        await before_request();self.calls+=1
        output={'facts':[{'evidence_id':'F_cash','text':'합성 현금은 백 원이다.'}],
                'unknown_evidence_ids':['U1'],'questions':[{'question':'차입 구성 자료를 확인할 수 있습니까?','evidence_ids':['U1']}]}
        if self.forged:output['facts'][0]['text']='현금은 천 원이다.'
        return {'draft':output,'usage':{'model_calls':1}}


class Stage05WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.store=ResearchStore(self.root/'db.sqlite',self.root/'sources')
        research=self.store.create_research(research_input());self.run=research['runs'][0]['id'];self.research=research['id']
        raw=b'cash = 100'
        source=self.store.save_source(self.run,source_input(),raw)
        fragment=self.store.save_fragment(source['id'],FragmentInput(location={'byte_start':0,'byte_end':len(raw),
            'raw_span_verified':True,'raw_span_sha256':hashlib.sha256(raw).hexdigest()},text=raw.decode()))
        fact=self.store.save_fact(self.run,fragment['id'],FactInput(account='cash',original_account='현금',
            raw_value='100',exact_value='100',scope='OFS',period={'as_of':'2026-06-30'},currency='KRW',unit='원'))
        self.batch={'id':'numeric','run_id':self.run,'validations':[],'calculations':[]}
        self.mapping={'id':'mapping','run_id':self.run}
        with self.store.connection(write=True) as conn:
            for identifier in ('numeric','mapping'):
                conn.execute('INSERT INTO research_validation_batches VALUES (?,?,?,?,?,?,?)',
                    (identifier,self.run,identifier,'synthetic-test','{}','{}','2026-10-21'))
        self.context={'numeric_batch_id':'numeric','mapping_batch_id':'mapping',
            'sections':[{'id':sid,'title':title} for sid,title in SECTIONS],
            'financial_rows':[{'scope':'OFS','account':'cash','status':'verified','value':'100','fact_ids':[fact['id']]}],
            'charts':[],'model_evidence':[{'evidence_id':'F_cash','text':'합성 현금은 백 원이다.','state':'verified'},
                {'evidence_id':'U1','text':'구성 미확인','state':'withheld'}],
            'unknowns':[{'text':'구성 미확인'}],'limitations':['합성 시험만 수행함']}

    async def execute(self,key='one',model=None):
        with patch('agent.finance.research_stage05.NumericValidationService.validate_run',return_value=self.batch), \
                patch('agent.finance.research_stage05.XbrlMappingService.validate_run',return_value=self.mapping), \
                patch('agent.finance.research_stage05.prepare_context',return_value=deepcopy(self.context)):
            return await Stage05Service(self.store,model or FakeLocalModel()).run(self.run,key)

    async def test_initial_and_corrected_versions_preserve_contents_and_readback(self):
        first=await self.execute();before=deepcopy(first['content']);model=FakeLocalModel()
        same=await self.execute(model=model)
        self.assertEqual(first['id'],same['id']);self.assertEqual(0,model.calls)
        second=await self.execute(key='correction')
        self.assertEqual((1,2),(first['version'],second['version']))
        self.assertEqual('published',second['status']);self.assertEqual('building',second['stored_status'])
        reopened=ResearchStore(self.root/'db.sqlite',self.root/'sources')
        self.assertEqual(before,reopened.get_report(first['id'])['content'])
        self.assertEqual(['published','published'],[r['status'] for r in reopened.get_research(self.research)['reports']])
        with reopened.connection(write=True) as conn:
            with self.assertRaises(sqlite3.IntegrityError):conn.execute("UPDATE research_reports SET content_json='{}' WHERE id=?",(first['id'],))
            with self.assertRaises(sqlite3.IntegrityError):conn.execute("UPDATE research_report_publications SET content_hash='fake' WHERE report_id=?",(first['id'],))

    async def test_forged_model_fact_cannot_be_published(self):
        with self.assertRaises(ValueError):await self.execute(model=FakeLocalModel(forged=True))
        with self.store.connection() as conn:
            self.assertEqual(0,conn.execute('SELECT COUNT(*) FROM research_report_publications').fetchone()[0])

    async def test_exhausted_model_budget_publishes_verified_data_and_explicit_model_gap(self):
        with self.store.connection(write=True) as conn:
            budget=json.loads(conn.execute('SELECT budget_json FROM research_runs WHERE id=?',(self.run,)).fetchone()[0])
            budget['model_calls']=0;budget['writing_reserve_calls']=0
            conn.execute('UPDATE research_runs SET budget_json=? WHERE id=?',(json.dumps(budget),self.run))
        model=FakeLocalModel();report=await self.execute(model=model)
        self.assertEqual(0,model.calls);self.assertEqual('published',report['status'])
        self.assertEqual('unavailable_or_rejected',report['content']['model_status'])
        self.assertTrue(report['content']['confirmed_facts']);self.assertTrue(report['content']['unknowns'])

    async def test_stopped_or_simultaneous_run_is_not_started(self):
        for status in ('stopped','running'):
            with self.store.connection(write=True) as conn:conn.execute('UPDATE research_runs SET status=? WHERE id=?',(status,self.run))
            with self.assertRaises(Conflict):await self.execute()

    async def test_api_can_read_both_published_versions_without_model_call(self):
        first=await self.execute();second=await self.execute('second')
        import server
        from fastapi.testclient import TestClient
        with patch.object(server.app.state,'research_store',self.store,create=True):
            client=TestClient(server.app,base_url='http://127.0.0.1:8100')
            try:
                self.assertEqual('published',client.get('/reports/'+first['id']).json()['status'])
                self.assertEqual(2,client.get('/reports/'+second['id']).json()['version'])
            finally:client.close()


class ReportDataTests(unittest.TestCase):
    def validation(self,account='cash',value='100',**extra):
        data={'account':account,'scope':'OFS','currency':'KRW','period':{'kind':'instant','as_of':'2026-06-30'},
            'source_id':'source','amount_comparison':{'original_normalized':value},
            'checks':{k:{'status':'pass'} for k in (*MANDATORY_CHECKS,'V12')},**extra}
        return {'id':'validation'+value,'fact_id':'fact'+value,'rule_id':'numeric_fact','status':'pass','data':data}

    def test_borrowing_composition_hold_never_becomes_display_value(self):
        v=self.validation('short_term_borrowings',investigation_use_status='withheld_pending_component_confirmation')
        rows=numeric_rows({'validations':[v]})
        row=next(r for r in rows if r['scope']=='OFS' and r['account']=='short_term_borrowings' and r['end']=='2026-06-30')
        self.assertEqual('withheld',row['status']);self.assertNotIn('value',row);self.assertEqual(130,len(rows))

    def test_quarter_is_not_mixed_with_cumulative_and_missing_does_not_become_zero(self):
        cumulative=self.validation('revenue',period={'kind':'duration','start':'2026-01-01','end':'2026-06-30'})
        quarter=self.validation('revenue','40',period={'kind':'duration','start':'2026-04-01','end':'2026-06-30'})
        row=next(r for r in numeric_rows({'validations':[cumulative,quarter]})
            if r['scope']=='OFS' and r['account']=='revenue' and r['end']=='2026-06-30')
        self.assertEqual('100',row['value'])

    def test_svg_escapes_text_and_keeps_exact_large_decimal(self):
        value='9007199254740993.123456789'
        svg=svg_chart('<script>',[{'status':'verified','value':value,'series_index':0,'end':'2026','label':'현금'}])
        self.assertNotIn('<script>',svg);self.assertIn('&lt;script&gt;',svg);self.assertIn(value,svg)

    def test_unconfirmed_taxonomy_bridge_blocks_interim_chart(self):
        rows=numeric_rows({'validations':[]})
        mapping={'manifest':{'concept_links':[{'scope':'OFS','account':'cash','reason':'namespace_version_differs_official_bridge_missing'}]}}
        result=charts(rows,{'calculations':[]},mapping)
        chart=next(c for c in result if c['id']=='interim-cash')
        self.assertEqual('withheld',chart['status']);self.assertNotIn('svg',chart)
