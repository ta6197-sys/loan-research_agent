"""회사 입력·보관 자료 재사용·출처 재검증 경계. 합성 공시만 사용한다."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from agent.finance.cached_case import CachedCaseLibrary,CachedStartInput,make_manifest,COMPANY,WINDOW
from agent.research_models import ResearchInput,SourceInput
from agent.research_store import ResearchStore,Conflict,IntegrityFailure,safe_payload


class CachedCaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.archive=self.root/'data/research/validation_stage04_integrated/synthetic';self.archive.mkdir(parents=True)
        self.original=ResearchStore(self.archive/'session.db',self.archive/'sources')
        research=self.original.create_research(ResearchInput(company=COMPANY,purpose='합성 원본',public_cutoff='2026-10-20',
            comparison_window=WINDOW,request_key='original'));self.old_run=research['runs'][0]['id']
        def source(op,content,**period):
            return self.original.save_source(self.old_run,SourceInput(kind='dart',title='합성 '+op,publisher='합성 시험',
                retrieved_at='2026-10-21',rights_status='public_disclosure',reporting_period={'operation':op,**period}),content)
        first=source('document',b'synthetic zip')
        second=source('document_member',b'<DOCUMENT><COMPANY-NAME AREGCIK="00129642">synthetic</COMPANY-NAME></DOCUMENT>',archive_source_id=first['id'])
        source('financials',json.dumps({'list':[{'corp_code':'00129642'}]}).encode(),corp_code='00129642')
        source('taxonomy_reference',b'<schema/>',referring_source_id=second['id'])
        self.manifest_path=self.root/'case.json';self.manifest=make_manifest(self.root,self.archive)
        self.manifest_path.write_text(safe_payload(self.manifest));self.library=CachedCaseLibrary(self.root,self.manifest_path)
        self.store=ResearchStore(self.root/'new.db',self.root/'new-sources')
        self.before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.archive.rglob('*') if p.is_file()}

    def value(self,**changes):
        return CachedStartInput(**({'company':'상신브레이크','purpose':'합성 회사 입력 시험','request_key':'input-one'}|changes))

    def start(self,**changes):return self.library.receive(self.store,self.value(**changes))

    def running(self,run_id):
        with self.store.connection(write=True) as conn:conn.execute("UPDATE research_runs SET status='running' WHERE id=?",(run_id,))

    def test_company_input_creates_new_research_and_queue_atomically(self):
        result=self.start();run=self.store.get_run(result['run_id'])
        self.assertNotEqual(self.old_run,run['id']);self.assertEqual('queued',run['status'])
        self.assertEqual('cached_case',run['trigger']['kind']);self.assertEqual('cached-case-initial',run['trigger']['job_request_key'])
        self.assertEqual(0,run['usage']['external_requests']);self.assertEqual(0,run['usage']['model_calls'])
        self.assertEqual(0,run['budget']['external_requests'])
        self.assertEqual(1,len(self.store.get_research(result['research_id'])['runs']))
        self.assertEqual(1,len(self.store.events(result['research_id'])))

    def test_duplicate_input_reuses_ids_but_changed_purpose_conflicts(self):
        first=self.start();self.assertEqual(first,self.start())
        self.assertEqual(first['research_id'],self.start(company='00129642')['research_id'])
        with self.assertRaises(Conflict):self.start(purpose='다른 목적')
        with self.assertRaises(Conflict):self.store.create_research(ResearchInput(company=COMPANY,purpose=self.value().purpose,
            public_cutoff='2026-10-20',comparison_window=WINDOW,request_key='input-one',budget=self.value().budget))

    def test_new_catalogue_keeps_old_pinned_execution_recoverable(self):
        receipt=self.start();self.running(receipt['run_id'])
        previous=self.root/'data/research/case_library/sangsin-20261020.json';previous.parent.mkdir(parents=True)
        previous.write_text(self.manifest_path.read_text())
        updated=deepcopy(self.manifest);updated['scope']='new_catalogue_test'
        newer=previous.with_name('sangsin-20261020-content-v2.json');newer.write_text(safe_payload(updated))
        current=CachedCaseLibrary(self.root,newer)
        result=current.prepare(self.store,receipt['run_id'],lambda:None)
        self.assertEqual(len(self.manifest['sources']),result['source_count'])
        self.assertEqual(self.library.fingerprint,self.store.get_run(receipt['run_id'])['trigger']['case_manifest_hash'])

    def test_foreign_company_cutoff_and_excess_budget_rejected_before_creation(self):
        with self.assertRaises(ValueError):self.start(company='다른 회사')
        with self.assertRaises(ValueError):self.start(public_cutoff='2026-10-21')
        with self.assertRaises(ValueError):self.value(budget={'model_calls':21})
        with self.assertRaises(ValueError):self.value(budget={'external_requests':1})
        self.assertEqual([],self.store.list_researches())

    def test_import_remaps_source_links_and_never_imports_old_flags(self):
        result=self.start();run=result['run_id'];self.running(run)
        imported=self.library.prepare(self.store,run,lambda:None)
        self.assertEqual(4,imported['source_count']);self.assertFalse(imported['validation_flags_copied'])
        with self.store.connection() as conn:
            rows=list(conn.execute('SELECT * FROM research_sources WHERE run_id=?',(run,)))
            self.assertEqual(0,conn.execute('SELECT COUNT(*) FROM research_facts').fetchone()[0])
            self.assertEqual(0,conn.execute('SELECT COUNT(*) FROM research_reports').fetchone()[0])
            self.assertEqual(0,conn.execute('SELECT COUNT(*) FROM research_validations').fetchone()[0])
        mapping={json.loads(r['metadata_json'])['reporting_period']['cached_original_source_id']:r['id'] for r in rows}
        for row in rows:
            meta=json.loads(row['metadata_json']);period=meta['reporting_period']
            original=next(s for s in self.manifest['sources'] if s['id']==period['cached_original_source_id'])
            for field in ('archive_source_id','referring_source_id'):
                if field in original['metadata']['reporting_period']:
                    self.assertEqual(mapping[original['metadata']['reporting_period'][field]],period[field])
            self.assertEqual(original['content_hash'],hashlib.sha256(self.store.source_content(row['id'])).hexdigest())
            self.assertFalse(meta['allow_external_model']);self.assertFalse(meta['allow_external_trace'])
        self.library.prepare(self.store,run,lambda:None)
        with self.store.connection() as conn:self.assertEqual(4,conn.execute('SELECT COUNT(*) FROM research_sources').fetchone()[0])
        self.assertTrue(all(Path(p).exists() and hashlib.sha256(Path(p).read_bytes()).hexdigest()==h for p,h in self.before.items()))

    def test_interrupted_import_reuses_already_registered_source(self):
        result=self.start();run=result['run_id'];self.running(run);count=0
        def interrupt():
            nonlocal count
            count+=1
            if count==3:raise OSError('synthetic interruption')
        with self.assertRaises(OSError):self.library.prepare(self.store,run,interrupt)
        self.library.prepare(self.store,run,lambda:None)
        with self.store.connection() as conn:
            rows=list(conn.execute('SELECT metadata_json FROM research_sources'))
        origins=[json.loads(r[0])['reporting_period']['cached_original_source_id'] for r in rows]
        self.assertEqual(4,len(origins));self.assertEqual(4,len(set(origins)))

    def test_tampered_archive_or_manifest_is_not_silently_reused(self):
        first=self.manifest['sources'][0]
        path=self.archive/'sources'/first['storage_key'];path.write_bytes(b'changed')
        with self.assertRaises(IntegrityFailure):self.start()
        self.assertEqual([],self.store.list_researches())
        self.manifest_path.write_text('{}')
        with self.assertRaises(IntegrityFailure):self.library.verify()

    def test_unexpected_source_and_changed_origin_metadata_are_rejected(self):
        result=self.start();run=result['run_id'];self.running(run)
        self.library.prepare(self.store,run,lambda:None)
        self.store.save_source(run,SourceInput(kind='user',title='추가 미검증',publisher='시험',retrieved_at='2026-10-21',rights_status='user_authorized'),b'unverified')
        with self.assertRaises(IntegrityFailure):self.library.prepare(self.store,run,lambda:None)

    def test_wrong_manifest_pinned_to_run_is_rejected(self):
        result=self.start();run=result['run_id'];self.running(run)
        with self.store.connection(write=True) as conn:
            trigger=self.store.get_run(run)['trigger'];trigger['case_manifest_hash']='fake'
            conn.execute('UPDATE research_runs SET trigger_json=? WHERE id=?',(json.dumps(trigger),run))
        with self.assertRaises(IntegrityFailure):self.library.prepare(self.store,run,lambda:None)

    def test_offline_api_search_and_single_input_enqueue_without_network(self):
        import server
        class Runtime:
            scheduled=0
            def schedule(self):self.scheduled+=1
        runtime=Runtime()
        with patch.object(server.app.state,'case_library',self.library,create=True), \
             patch.object(server.app.state,'research_store',self.store,create=True), \
             patch.object(server.app.state,'mode','research',create=True), \
             patch.object(server.app.state,'research_runtime',runtime,create=True):
            client=TestClient(server.app,base_url='http://127.0.0.1')
            try:
                response=client.get('/case-companies',params={'query':'상신'})
                self.assertEqual('00129642',response.json()['items'][0]['company']['corp_code'])
                self.assertEqual([],client.get('/case-companies',params={'query':'없는 회사'}).json()['items'])
                response=client.post('/researches/start',json=self.value().model_dump(mode='json'))
                self.assertEqual(202,response.status_code);self.assertEqual('queued',response.json()['status'])
                duplicate=client.post('/researches/start',json=self.value().model_dump(mode='json'))
                self.assertEqual(response.json()['run_id'],duplicate.json()['run_id'])
                self.assertEqual(2,runtime.scheduled)
                self.assertEqual(422,client.post('/researches/start',json={**self.value().model_dump(mode='json'),'source_directory':'/arbitrary'}).status_code)
            finally:client.close()
