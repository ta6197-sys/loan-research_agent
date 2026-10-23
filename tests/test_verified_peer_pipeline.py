"""합성 원문·검증 배치를 실제 SQLite에 저장하는 통합 시험. 실제 기업 성능이 아니다."""
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4
from agent.research_store import ResearchStore, digest, encode, IntegrityFailure, Conflict
from agent.research_models import Company, FragmentInput, FactInput
from agent.finance.cohort_registry import CohortRegistry
from agent.finance.analytics_dataset_service import AnalyticsDatasetService
from agent.finance.validation_acceptance import MANDATORY_CHECKS
from tests.test_research_storage import research_input, source_input


class VerifiedPeerPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = ResearchStore(Path(self.tmp.name)/'db', Path(self.tmp.name)/'sources')
        self.members, self.reports = [], []
        for code in ('00000001', '00000002'): self.add_company(code)
        self.policy = {'industry_system': 'synthetic-industry', 'industry_version': 'test-1', 'industry_code': 'C29',
                       'accounting_standard': 'K-IFRS', 'scope': 'OFS', 'public_cutoff': '2026-10-20',
                       'policy_version': 'synthetic-test-1', 'data_origin': 'synthetic_test_only',
                       'periods': [{'start': '2025-01-01','end': '2025-12-31','kind': 'duration'}], 'members': self.members}

    def add_company(self, code, unmapped_account=None):
        r = research_input(code).model_copy(update={'company': Company(name='합성 기업',corp_code=code)})
        research = self.store.create_research(r); run = research['runs'][0]['id']; batch = uuid4().hex
        identity = self.store.save_source(run, source_input(kind='dart', rights_status='public_disclosure', reporting_period={'operation':'company'}),
                                          encode({'corp_code': code, 'induty_code': 'C29'}).encode())
        raw = '한국채택국제회계기준 합성 시험 수치'.encode()
        source = self.store.save_source(run, source_input(kind='dart',rights_status='public_disclosure',published_at='2026-03-30',receipt_number='20260330000001'), raw)
        location = {'byte_start':0,'byte_end':len(raw),'raw_span_verified':True,'raw_span_sha256':digest(raw)}
        fragment = self.store.save_fragment(source['id'], FragmentInput(location=location,text=raw.decode()))
        self.members.append({'corp_code':code,'identity_source_id':identity['id'],'accounting_standard_evidence':{'source_id':source['id'],'location':location}})
        rows = []; validations = []
        amounts = {'revenue':'100','operating_profit':'10','net_profit':'8','operating_cashflow':'9','assets':'200','liabilities':'80','inventory':'40','receivables':'30'}
        for account, value in amounts.items():
            period = {'kind':'duration','start':'2025-01-01','end':'2025-12-31'} if account in ('revenue','operating_profit','net_profit','operating_cashflow') else {'kind':'instant','as_of':'2025-12-31'}
            fact = self.store.save_fact(run,fragment['id'],FactInput(account=account,original_account=account,raw_value=value,exact_value=value,scope='OFS',period=period,currency='KRW',unit='원'))
            vid=uuid4().hex
            data={'checks':{rule:{'status':'pass'} for rule in MANDATORY_CHECKS},'account':account,'scope':'OFS','period':period,'amount_comparison':{'original_normalized':value}}
            if account == unmapped_account: data['checks']['V06'] = {'status':'not_checked'}
            validations.append((vid,batch,fact['id'],'numeric_fact','pass',encode(data),'2026-10-20'))
            rows.append({'account':account,'scope':'OFS','end':'2025-12-31','period':period,'status':'verified','value':value,'fact_ids':[fact['id']],'validation_ids':[vid],'source_ids':[source['id']]})
        report_id=uuid4().hex; content={'context':{'financial_rows':rows,'public_cutoff':'2026-10-20'}}; hashed=digest(encode(content).encode())
        with self.store.connection(write=True) as conn:
            conn.execute('INSERT INTO research_validation_batches VALUES (?,?,?,?,?,?,?)',(batch,run,'synthetic-test','synthetic-test','{}','{}','2026-10-20'))
            conn.executemany('INSERT INTO research_validations VALUES (?,?,?,?,?,?,?)',validations)
            conn.execute('INSERT INTO research_reports VALUES (?,?,?,?,?,?,?,?,?)',(report_id,research['id'],run,1,report_id,hashed,encode(content),'building','2026-10-20'))
            conn.execute('INSERT INTO research_report_publications VALUES (?,?,?,?,?)',(report_id,batch,hashed,'{}','2026-10-20'))
        self.reports.append(report_id)

    def test_unmapped_account_is_never_promoted_to_a_feature(self):
        self.add_company('00000003', unmapped_account='revenue')
        cohort=CohortRegistry(self.store).register(self.policy)
        payload=AnalyticsDatasetService(self.store).build(cohort['cohort_id'], self.reports)
        row=next(row for row in payload['snapshot']['rows'] if row['corp_code']=='00000003')
        self.assertNotIn('operating_margin',row['features'])
        self.assertNotIn('net_margin',row['features'])
        self.assertIn('liabilities_to_assets',row['features'])

    def test_multiple_verified_companies_flow_into_snapshot_and_frozen_api(self):
        cohort=CohortRegistry(self.store).register(self.policy)
        service=AnalyticsDatasetService(self.store); payload=service.build(cohort['cohort_id'],self.reports)
        self.assertEqual(2,payload['snapshot']['row_count'],payload['snapshot']['build_exclusions'])
        self.assertEqual('not_trained_insufficient_data',payload['ml_readiness']['training_status'])
        self.assertEqual(payload['artifact_id'],service.build(cohort['cohort_id'],list(reversed(self.reports)))['artifact_id'])
        service.bind(self.reports[0],payload['artifact_id'])
        self.assertEqual(payload,service.for_report(self.reports[0]))
        from agent.research_api import _financial_analysis_payload
        api=_financial_analysis_payload(self.store,self.reports[0])
        self.assertEqual(payload['artifact_id'],api['dataset_artifact_id'])
        self.assertTrue(api['statistical_analysis']['trend_analysis'])
        self.assertTrue(all(p['status']=='insufficient_peer_data' for p in api['statistical_analysis']['peer_comparisons']))
        with self.store.connection(write=True) as conn:
            import sqlite3
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('UPDATE analytics_snapshots SET payload=? WHERE id=?', ('{}', payload['artifact_id']))

    def test_frozen_snapshot_detects_tampered_source_on_read(self):
        cohort=CohortRegistry(self.store).register(self.policy)
        service=AnalyticsDatasetService(self.store)
        payload=service.build(cohort['cohort_id'], self.reports)
        source_id=self.members[0]['accounting_standard_evidence']['source_id']
        with self.store.connection() as conn:
            key=conn.execute('SELECT storage_key FROM research_sources WHERE id=?',(source_id,)).fetchone()[0]
        (self.store.source_root/key).write_bytes(b'tampered')
        with self.assertRaises(IntegrityFailure): service.get(payload['artifact_id'])

    def test_industry_mismatch_is_excluded_and_original_tamper_rejected(self):
        self.policy['industry_code']='C30'
        cohort=CohortRegistry(self.store).register(self.policy)
        payload=AnalyticsDatasetService(self.store).build(cohort['cohort_id'],self.reports)
        self.assertEqual(0,payload['snapshot']['row_count'])
        self.assertTrue(payload['snapshot']['build_exclusions'])
        with self.assertRaisesRegex(Conflict, 'snapshot_report_has_no_verified_observation'):
            AnalyticsDatasetService(self.store).bind(self.reports[0],payload['artifact_id'])
        source=self.store.get_source(self.members[0]['identity_source_id'])
        with self.store.connection() as conn:
            key=conn.execute('SELECT storage_key FROM research_sources WHERE id=?',(source['id'],)).fetchone()[0]
        (self.store.source_root/key).write_bytes(b'tampered')
        with self.assertRaises(IntegrityFailure): AnalyticsDatasetService(self.store).build(cohort['cohort_id'],self.reports)
