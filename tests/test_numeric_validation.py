"""직접 만든 자료의 검증 규칙 시험. 금융 실자료 정확성 평가와 구분한다."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from agent.finance.numeric_validation import (
    NumericValidationService, api_field, comparison_policy, decimal, exact_sum, match_amount,
)
from agent.research_models import ResearchInput, SourceInput
from agent.research_store import IntegrityFailure, ResearchStore
from agent.session_store import SqliteSessionStore
from test_document_extraction import document


def covered_document():
    cover=b'<COMPANY-NAME AREGCIK="00129642">Synthetic</COMPANY-NAME><TU AUNIT="PERIODFROM" AUNITVALUE="20260101">2026-01-01</TU><TU AUNIT="PERIODTO" AUNITVALUE="20260630">2026-06-30</TU>'
    return document().replace(b'<DOCUMENT>',b'<DOCUMENT>'+cover)


class NumericRulesTests(unittest.TestCase):
    def test_exact_zero_missing_and_negative(self):
        self.assertEqual('exact',match_amount('0','0','원')['match'])
        self.assertEqual('not_checked',match_amount(None,'0','원')['status'])
        self.assertEqual('fail',match_amount('-1','1','원')['status'])
        self.assertEqual('-1000',match_amount('-1','-1000','천원')['original_normalized'])

    def test_rounding_requires_explicit_confirmed_method(self):
        self.assertEqual('not_checked',match_amount('10','10499999','백만원')['status'])
        rule={'evidence_confirmed':True,'method':'half_up','decimal_places':0}
        self.assertEqual('rounding',match_amount('10','10499999','백만원',rule)['match'])
        self.assertNotEqual('pass',match_amount('10','10500000','백만원',rule)['status'])
        self.assertEqual('rounding',match_amount('-11','-10500000','백만원',rule)['match'])
        rule['evidence_confirmed']=False
        self.assertNotEqual('pass',match_amount('10','10499999','백만원',rule)['status'])
        rule.update(evidence_confirmed=True,method='truncate')
        self.assertEqual('rounding',match_amount('-10','-10999999','백만원',rule)['match'])

    def test_no_float_or_default_decimal_precision_loss(self):
        huge='1234567890123456789012345678901234567890'
        self.assertEqual('1',str(exact_sum([huge,str(decimal(huge).copy_negate()),'1'])))
        for value in (1.0,'NaN','Infinity','1e999999'):
            with self.assertRaises(ValueError):decimal(value)

    def test_statement_specific_interim_api_columns(self):
        q={'bsns_year':'2026','reprt_code':'11012'}
        p={'kind':'duration','start':'2026-01-01','end':'2026-06-30'}
        self.assertEqual('thstrm_add_amount',api_field(p,'CIS',q))
        self.assertEqual('thstrm_amount',api_field(p|{'end':'2026-03-31','column_kind':'three_months'},'CIS',q|{'reprt_code':'11013'}))
        self.assertEqual('thstrm_amount',api_field(p,'CF',q))
        self.assertEqual('thstrm_amount',api_field(p|{'start':'2026-04-01'},'CIS',q))
        self.assertIsNone(api_field(p|{'start':'2026-04-01'},'CF',q))
        self.assertEqual('frmtrm_q_amount',api_field(p|{'start':'2025-01-01','end':'2025-06-30'},'CF',q))
        self.assertEqual('frmtrm_amount',api_field({'kind':'instant','as_of':'2025-12-31'},'BS',q))
        self.assertIsNone(api_field({'kind':'instant','as_of':'2025-06-30'},'BS',q))

    def test_roman_cashflow_labels(self):
        from agent.finance.document_extractor import normalize_label
        for roman in ('Ⅵ','Ⅶ','Ⅷ','VI','VII'):
            self.assertEqual('기초현금및현금성자산',normalize_label(roman+'.기초현금및현금성자산'))

    def test_annual_and_noncalendar_fields(self):
        q={'bsns_year':'2025','reprt_code':'11011'}
        self.assertEqual('bfefrmtrm_amount',api_field({'kind':'duration','start':'2023-01-01','end':'2023-12-31'},'CIS',q))
        self.assertIsNone(api_field({'kind':'duration','start':'2025-04-01','end':'2026-03-31'},'CIS',q))

    def test_comparison_policy_disallows_annual_interim_scope_composition_mix(self):
        a={'scope':'OFS','currency':'KRW','account':'revenue','original_account':'매출액',
           'period':{'kind':'duration','start':'2026-01-01','end':'2026-06-30'}}
        b=a|{'period':{'kind':'duration','start':'2025-01-01','end':'2025-06-30'}}
        self.assertEqual('same_period',comparison_policy(a,b))
        self.assertEqual('incomparable_durations',comparison_policy(a,b|{'period':b['period']|{'end':'2025-12-31'}}))
        self.assertEqual('scope_currency_or_account_mismatch',comparison_policy(a,b|{'scope':'CFS'}))
        self.assertEqual('account_composition_unconfirmed',comparison_policy(a,b|{'original_account':'다른 매출액'}))
        cash=a|{'account':'cash','period':{'kind':'instant','as_of':'2026-06-30'}}
        self.assertEqual('previous_year_end',comparison_policy(cash,cash|{'period':{'kind':'instant','as_of':'2025-12-31'}}))


class ValidationFixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=ResearchStore(self.root/'session.db',self.root/'sources')
        r=self.store.create_research(ResearchInput(company={'name':'합성 기업','corp_code':'00129642'},
            purpose='수치 검사',public_cutoff='2026-10-20',comparison_window={},request_key='numeric-test'))
        self.run_id=r['runs'][0]['id'];self.service=NumericValidationService(self.store)

    def save(self,content,media='application/xml',receipt='20260813000874',supersedes=None):
        query={'operation':'document_member'} if media!='application/json' else {
            'operation':'financials','corp_code':'00129642','bsns_year':'2026','reprt_code':'11012','fs_div':'OFS'}
        return self.store.save_source(self.run_id,SourceInput(kind='synthetic',title='직접 만든 자료',publisher='시험',
            retrieved_at='2026-10-20',published_at=receipt[:4]+'-'+receipt[4:6]+'-'+receipt[6:8],
            receipt_number=receipt,media_type=media,rights_status='synthetic',reporting_period=query,supersedes_id=supersedes),content)

    def setup_data(self,**changes):
        original=self.save(covered_document())
        record={'corp_code':'00129642','rcept_no':'20260813000874','bsns_year':'2026','reprt_code':'11012',
            'sj_div':'CIS','account_id':'ifrs-full_Revenue','account_nm':'매출액','currency':'KRW',
            'thstrm_amount':'106461983869','thstrm_add_amount':'197724953870',
            'frmtrm_q_amount':'96257111900','frmtrm_add_amount':'191738976251'}
        record.update(changes)
        api=self.save(json.dumps({'status':'000','list':[record]}).encode(),'application/json')
        return original,api


    def _facts_for_source(self,source_id):
        with self.store.connection() as c:
            return c.execute('SELECT f.id FROM research_facts f JOIN research_fragments g ON f.fragment_id=g.id WHERE g.source_id=?',(source_id,)).fetchall()


class ValidationStorageTests(ValidationFixture):
    def test_pairs_are_exact_immutable_idempotent_and_reopen(self):
        self.setup_data();first=self.service.validate_run(self.run_id)
        self.assertEqual({'pass':4},first['summary']['validation_counts'])
        self.assertEqual(4,len(first['validations']))
        self.assertTrue(all(v['data']['amount_comparison']['match']=='exact' for v in first['validations']))
        self.assertTrue(first['calculations'])
        reopened=NumericValidationService(ResearchStore(self.store.db_path,self.store.source_root))
        self.assertEqual(first['id'],reopened.validate_run(self.run_id)['id'])
        self.assertTrue(reopened.validate_run(self.run_id)['reused'])
        with self.store.connection() as c:
            self.assertEqual([],c.execute('PRAGMA foreign_key_check').fetchall())
            self.assertEqual({'candidate'},{r[0] for r in c.execute('SELECT status FROM research_facts')})
        with self.assertRaises(sqlite3.IntegrityError),self.store.connection(write=True) as c:
            c.execute("UPDATE research_validations SET status='pass'")

    def test_equal_wrong_corporation_never_passes(self):
        self.setup_data(corp_code='99999999');result=self.service.validate_run(self.run_id)
        self.assertEqual({'fail':4},result['summary']['validation_counts'])
        self.assertEqual([],result['calculations'])

    def test_missing_wrong_value_and_unknown_account_id(self):
        for changes,status in [({'thstrm_add_amount':'0'},'fail'),({'account_id':'-표준계정코드 미사용-'},'not_checked'),
                               ({'thstrm_add_amount':''},'not_checked')]:
            with self.subTest(changes=changes):
                self.setUp();self.setup_data(**changes);result=self.service.validate_run(self.run_id)
                current=[v for v in result['validations'] if v['data']['period'].get('start')=='2026-01-01'][0]
                self.assertEqual(status,current['status'])

    def test_cutoff_and_supersession_never_force_pass(self):
        original,api=self.setup_data()
        self.save(covered_document(),receipt='20261019000001',supersedes=original['id'])
        result=self.service.validate_run(self.run_id)
        old_fact_ids={r[0] for r in self._facts_for_source(original['id'])}
        old=[v for v in result['validations'] if v['fact_id'] in old_fact_ids]
        self.assertTrue(all(v['status']=='not_checked' for v in old))


    def test_api_only_and_empty_run_are_not_success(self):
        result=self.service.validate_run(self.run_id)
        self.assertEqual({'not_checked':1},result['summary']['validation_counts'])
        self.save(b'{"list":[]}',media='application/json')
        result=self.service.validate_run(self.run_id)
        self.assertGreaterEqual(result['summary']['validation_counts']['not_checked'],2)

    def test_changed_source_hash_stops_cache_reuse(self):
        original,api=self.setup_data();self.service.validate_run(self.run_id)
        with self.store.connection() as c:
            key=c.execute('SELECT storage_key FROM research_sources WHERE id=?',(original['id'],)).fetchone()[0]
        (self.store.source_root/key).write_bytes(b'changed')
        with self.assertRaises(IntegrityFailure):self.service.validate_run(self.run_id)

    def test_policy_change_keeps_both_histories(self):
        self.setup_data();first=self.service.validate_run(self.run_id)
        with patch('agent.finance.numeric_validation.POLICY_VERSION','numeric-validation-test-2'):
            second=self.service.validate_run(self.run_id)
        self.assertNotEqual(first['id'],second['id'])
        self.assertEqual(2,len(self.service.history(self.run_id)))
        self.assertEqual(first['validations'],self.service.batch(first['id'])['validations'])

    def test_failed_write_is_atomic(self):
        self.setup_data()
        with self.store.connection(write=True) as c:
            c.execute("CREATE TRIGGER injected BEFORE INSERT ON research_validations BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.service.validate_run(self.run_id)
        self.assertEqual([],self.service.history(self.run_id))

    def test_http_reads_persisted_results_without_finance_model(self):
        from fastapi import FastAPI
        from agent.research_api import router
        self.setup_data();result=self.service.validate_run(self.run_id)
        app=FastAPI();app.state.research_store=self.store;app.include_router(router)
        with TestClient(app) as client:
            self.assertEqual(result['id'],client.get(f'/runs/{self.run_id}/validations').json()['items'][0]['id'])
            self.assertEqual(result['summary'],client.get(f'/validation-batches/{result["id"]}').json()['summary'])

    def test_v1_migration_preserves_original_rows_and_chat(self):
        db=self.root/'old.db'
        session=SqliteSessionStore(db);session.append('chat','user','보존');session.close()
        c=sqlite3.connect(db);c.executescript(Path('agent/research_schema.sql').read_text())
        c.execute("INSERT INTO research_migrations VALUES (1,'before')");c.commit();c.close()
        ResearchStore(db,self.root/'other_sources')
        session=SqliteSessionStore(db);self.addCleanup(session.close)
        self.assertEqual([{'role':'user','content':'보존'}],session.load('chat'))
        c=sqlite3.connect(db)
        self.assertEqual([(1,),(2,),(3,)],c.execute('SELECT version FROM research_migrations ORDER BY version').fetchall());c.close()

class RegressionAndCalculationTests(ValidationFixture):
    def test_current_asset_raw_whitespace_and_inventory_alias(self):
        xml=covered_document().replace(b'</DOCUMENT>', '''<TABLE-GROUP ACLASS="{XBRL}BS_S"><TITLE>별도 재무상태표</TITLE>
<TABLE BORDER="0"><TR><TE>제 52 기 반기말 2026.06.30 현재</TE></TR><TR><TE>(단위 : 원)</TE></TR></TABLE>
<TABLE BORDER="1"><TR><TH></TH><TH>제 52 기 반기말</TH></TR><TR><TE>유동 자산</TE><TE></TE></TR>
<TR><TE>매출채권및기타채권</TE><TE>100</TE></TR><TR><TE>유동재고자산</TE><TE>20</TE></TR></TABLE></TABLE-GROUP></DOCUMENT>'''.encode())
        self.save(xml)
        records=[{'corp_code':'00129642','rcept_no':'20260813000874','bsns_year':'2026','reprt_code':'11012',
            'sj_div':'BS','account_id':aid,'account_nm':name,'currency':'KRW','thstrm_amount':value}
            for aid,name,value in [('ifrs-full_TradeAndOtherCurrentReceivables','매출채권및기타채권','100'),
                                   ('ifrs-full_Inventories','유동재고자산','20')]]
        self.save(json.dumps({'list':records}).encode(),'application/json')
        result=self.service.validate_run(self.run_id)
        selected=[v for v in result['validations'] if v['data'].get('account') in ('receivables','inventory')]
        self.assertEqual(2,len(selected));self.assertTrue(all(v['status']=='pass' for v in selected))

    def test_wrapper_json_pointers_preserve_full_original(self):
        self.save(covered_document())
        record={'corp_code':'00129642','rcept_no':'20260813000874','bsns_year':'2026','reprt_code':'11012',
            'sj_div':'CIS','account_id':'ifrs-full_Revenue','account_nm':'매출액','currency':'KRW',
            'thstrm_amount':'106461983869','thstrm_add_amount':'197724953870',
            'frmtrm_q_amount':'96257111900','frmtrm_add_amount':'191738976251'}
        raw=json.dumps({'query':{'corp_code':'00129642','bsns_year':'2026','reprt_code':'11012','fs_div':'OFS'},
                        'response':{'list':[record]}}).encode()
        source=self.save(raw,'application/json');result=self.service.validate_run(self.run_id)
        self.assertEqual(4,result['summary']['validation_counts']['pass'])
        fragment=self.service.extraction.fragment(result['validations'][0]['data']['api_fragment_id'])
        self.assertEqual('/response/list/0',fragment['data']['location']['json_pointer'])
        self.assertEqual(raw,self.store.source_content(source['id']))

    def test_post_cutoff_and_recovery(self):
        original,api=self.setup_data()
        r=self.store.create_research(ResearchInput(company={'name':'합성','corp_code':'00129642'},purpose='과거',
            public_cutoff='2026-08-12',comparison_window={},request_key='past'))
        previous=self.run_id;self.run_id=r['runs'][0]['id'];self.setup_data()
        result=self.service.validate_run(self.run_id)
        self.assertTrue(all(v['status']=='fail' for v in result['validations'] if v['fact_id']))
        self.run_id=previous
        self.assertEqual(4,self.service.validate_run(previous)['summary']['validation_counts']['pass'])

    def test_declared_truncation_is_separate_and_not_used_for_exact_calculations(self):
        xml=covered_document().replace('(단위 : 원)'.encode(),'(단위 : 백만원)'.encode())
        for old,new in [('197,724,953,870','197724'),('106,461,983,869','106461'),
                        ('96,257,111,900','96257'),('191,738,976,251','191738')]:
            xml=xml.replace(old.encode(),new.encode())
        xml=xml.replace(b'<TABLE BORDER="1">','<P>백만원 미만 절사</P><TABLE BORDER="1">'.encode())
        # API는 정확값, 원문은 명시된 절사 값인 직접 만든 사례.
        _,api=self.setup_data()
        r=self.store.create_research(ResearchInput(company={'name':'합성','corp_code':'00129642'},purpose='반올림',
            public_cutoff='2026-10-20',comparison_window={},request_key='rounding'))
        self.run_id=r['runs'][0]['id'];self.save(xml)
        self.save(self.store.source_content(api['id']),'application/json')
        result=self.service.validate_run(self.run_id)
        self.assertEqual({'pass':4},result['summary']['validation_counts'])
        self.assertTrue(all(v['data']['amount_comparison']['match']=='rounding' for v in result['validations']))
        self.assertEqual([],result['calculations'])

    def test_rounding_declaration_is_read_from_verified_original(self):
        content=covered_document().replace(b'<TABLE BORDER="1">','<P>백만원 미만 사사오입</P><TABLE BORDER="1">'.encode())
        content=content.replace('(단위 : 원)'.encode(),'(단위 : 백만원)'.encode())
        from agent.finance.document_extractor import DocumentExtractor
        table=DocumentExtractor(content).extract()['tables'][1]
        rule=self.service._declared_rounding(content,table)
        self.assertEqual('half_up',rule['method'])
        self.assertTrue(rule['evidence_confirmed'])
        table['preamble'][-1]['location']['raw_span_sha256']='changed'
        self.assertIsNone(self.service._declared_rounding(content,table))

    def test_duplicate_sources_preserve_all_references_and_order_independent_comparison(self):
        pairs=[]
        for suffix,caption,when,value in (
            ('current_a','장기차입금','2026-06-30','100'),
            ('previous_b','비유동차입금','2025-12-31','90'),
            ('current_b','비유동차입금','2026-06-30','100'),
            ('previous_a','장기차입금','2025-12-31','90')):
            fact={'id':suffix,'source':{'id':suffix},'data':{'account':'long_term_borrowings',
                'original_account':caption,'scope':'OFS','currency':'KRW',
                'period':{'kind':'instant','as_of':when}}}
            v={'id':'v-'+suffix,'fact_id':suffix,'status':'pass','data':{
                'amount_comparison':{'match':'exact','original_normalized':value}}}
            pairs.append((fact,v))
        outputs=[]
        for order in (pairs,list(reversed(pairs))):
            facts,vs=map(list,zip(*order));calculations=self.service._calculations(facts,vs)
            comparisons=[c for c in calculations if 'V11_change' in c['formula_version']]
            self.assertEqual(1,len(comparisons))
            self.assertEqual(2,len(comparisons[0]['data']['supporting_fact_ids']['current']))
            outputs.append(comparisons[0]['data']['result'])
        self.assertEqual(['10','10'],outputs)

    def test_arithmetic_failure_missing_adjustment_and_scope_isolation(self):
        def prepared(scope,account,value,period):
            i=scope+account
            fact={'id':i,'source':{'id':'original'},'data':{'account':account,'original_account':account,
                'scope':scope,'currency':'KRW','period':period}}
            validation={'id':'validation-'+i,'fact_id':i,'status':'pass','data':{
                'amount_comparison':{'match':'exact','original_normalized':value}}}
            return fact,validation
        bs={'kind':'instant','as_of':'2026-06-30'};cf={'kind':'duration','start':'2026-01-01','end':'2026-06-30'}
        pairs=[prepared('OFS',a,v,bs) for a,v in [('assets','100'),('liabilities','60'),('equity','39'),('cash','4')]]
        pairs += [prepared('OFS',a,v,cf) for a,v in [('cash_begin','10'),('operating_cashflow','-5'),
                   ('investing_cashflow','-2'),('financing_cashflow','0'),('cash_fx','0'),('cash_end','4')]]
        facts,validations=map(list,zip(*pairs));calculations=self.service._calculations(facts,validations)
        def find(rule):return next(c for c in calculations if rule in c['formula_version'])
        self.assertEqual('fail',find('V08')['status']);self.assertEqual('-1',find('V08')['data']['difference'])
        self.assertEqual('fail',find('V10')['status']);self.assertEqual('3',find('V10')['data']['result'])
        self.assertEqual('pass',find('V09')['status'])
        fx=next(f for f in facts if f['data']['account']=='cash_fx');fx['data']['scope']='CFS'
        calculations=self.service._calculations(facts,validations)
        self.assertEqual('not_checked',find('V10')['status'])
        self.assertIn('cash_fx',find('V10')['data']['missing_or_unverified_inputs'])
