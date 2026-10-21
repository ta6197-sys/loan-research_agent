"""승인된 보관 공시를 읽기 전용으로 재사용해 새 회사 입력 조사를 준비한다.

원래 검증/보고서/사용자 입력은 가져오지 않는다. 원문은 새 출처로 저장해 다시 검증한다.
"""
from contextlib import closing
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

from pydantic import Field,field_validator
from agent.research_models import Contract,Budget,ResearchInput,SourceInput
from agent.research_store import Conflict,IntegrityFailure,digest,safe_payload

CASE_ID='sangsin-20261020'
COMPANY={'name':'상신브레이크','corp_code':'00129642','stock_code':'041650'}
WINDOW={'annual_years':[2023,2024,2025],'interim_periods':['2025-H1','2026-H1']}
OPERATIONS={'disclosures','financials','document','document_member','xbrl','xbrl_member','taxonomy_reference','official_reference'}


def preferred_manifest(root):
    directory=Path(root)/'data/research/case_library'
    updated=directory/'sangsin-20261020-content-v2.json'
    return updated if updated.exists() else directory/'sangsin-20261020.json'


class CachedStartInput(Contract):
    company: str = Field(min_length=1,max_length=200)
    purpose: str = Field(min_length=1,max_length=2000)
    request_key: str = Field(min_length=1,max_length=200)
    public_cutoff: date = date(2026,10,20)
    budget: Budget = Field(default_factory=lambda:Budget(external_requests=0))

    @field_validator('budget')
    @classmethod
    def bounded_budget(cls,value):
        if value.external_requests!=0 or value.model_calls>20 or value.execution_seconds>1800:
            raise ValueError('보관 자료 실행은 외부 요청0·모델20회·시간1800초 이내입니다.')
        return value


def read_connection(path):
    return closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True))


def raw_file(directory,key):
    root=directory/'sources';path=root/key
    if root.is_symlink():raise IntegrityFailure('cached_source_root_symlink')
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):raise IntegrityFailure('cached_source_path_invalid')
    cursor=path
    while cursor!=root:
        if cursor.is_symlink():raise IntegrityFailure('cached_source_symlink')
        cursor=cursor.parent
    return path


def make_manifest(project_root,directory):
    root=Path(project_root).resolve();directory=Path(directory).resolve()
    if not directory.is_relative_to(root/'data/research/validation_stage04_integrated'):
        raise ValueError('verified_analysis_directory_required')
    database=directory/'session.db'
    with read_connection(database) as conn:
        conn.row_factory=sqlite3.Row
        matches=conn.execute('SELECT r.id,b.data_json,c.company_json FROM research_runs r JOIN research_bases b ON b.id=r.basis_id '
            'JOIN research_researches c ON c.id=b.research_id').fetchall()
        matches=[r for r in matches if json.loads(r['company_json']).get('corp_code')==COMPANY['corp_code']
            and json.loads(r['data_json'])['public_cutoff']=='2026-10-20']
        if len(matches)!=1:raise ValueError('cached_case_run_ambiguous')
        run=matches[0];basis=json.loads(run['data_json'])
        if basis['comparison_window']!=WINDOW:raise ValueError('cached_case_period_mismatch')
        rows=[dict(r) for r in conn.execute('SELECT * FROM research_sources WHERE run_id=? ORDER BY id',(run['id'],))]
    if not rows:raise ValueError('cached_case_empty')
    records=[]
    for row in rows:
        meta=json.loads(row['metadata_json']);SourceInput.model_validate(meta)
        if (meta['kind'] not in {'dart','official_standard'} or meta['allow_external_model'] or meta['allow_external_trace']
                or meta['reporting_period'].get('operation') not in OPERATIONS):
            raise ValueError('cached_case_public_sources_only')
        raw=raw_file(directory,row['storage_key']).read_bytes()
        if len(raw)!=row['byte_size'] or digest(raw)!=row['content_hash']:raise IntegrityFailure('cached_original_hash_mismatch')
        if meta['reporting_period'].get('operation')=='financials':
            data=json.loads(raw);records_json=data.get('response',data).get('list',[])
            if not records_json or any(x.get('corp_code')!=COMPANY['corp_code'] for x in records_json):
                raise ValueError('cached_company_mismatch')
        records.append({k:row[k] for k in ('id','storage_key','content_hash','byte_size')}|{'metadata':meta})
    return {'version':'cached-case-1.0','case_id':CASE_ID,'company':COMPANY,'public_cutoff':'2026-10-20',
        'comparison_window':WINDOW,'source_directory':directory.relative_to(root).as_posix(),
        'source_run_id':run['id'],'database_hash':digest(database.read_bytes()),'sources':records,
        'scope':'archived_public_disclosures_not_latest_recollection','external_requests':0}


class CachedCaseLibrary:
    def __init__(self,project_root,manifest_path):
        self.root=Path(project_root).resolve();self.path=Path(manifest_path).resolve()
        self.serialized=self.path.read_bytes();self.manifest=json.loads(self.serialized)
        self.fingerprint=digest(self.serialized)
        if (self.manifest.get('version')!='cached-case-1.0' or self.manifest.get('case_id')!=CASE_ID
                or self.manifest.get('company')!=COMPANY or self.manifest.get('comparison_window')!=WINDOW
                or self.manifest.get('public_cutoff')!='2026-10-20'):
            raise ValueError('unsupported_cached_case')
        self.directory=(self.root/self.manifest['source_directory']).resolve()
        if not self.directory.is_relative_to(self.root/'data/research/validation_stage04_integrated'):
            raise ValueError('cached_directory_outside_archive')

    def verify(self):
        if digest(self.path.read_bytes())!=self.fingerprint:raise IntegrityFailure('cached_manifest_changed')
        actual=make_manifest(self.root,self.directory)
        if actual!=self.manifest:raise IntegrityFailure('cached_archive_changed')

    def search(self,query):
        values={COMPANY['name'],COMPANY['corp_code'],COMPANY['stock_code']}
        return {'items':[{'case_id':CASE_ID,'company':COMPANY,'public_cutoff':'2026-10-20',
            'availability':'archived','identity_status':'recheck_originals_during_run'}]
            if any(query.strip() in value for value in values) else [],'external_requests':0}

    def receive(self,store,value:CachedStartInput):
        if value.company.strip() not in {COMPANY['name'],COMPANY['corp_code'],COMPANY['stock_code']}:
            raise ValueError('cached_company_selection_required')
        if value.public_cutoff.isoformat()!='2026-10-20':raise ValueError('cached_public_cutoff_mismatch')
        self.verify()
        research=store.create_research(ResearchInput(company=COMPANY,purpose=value.purpose,public_cutoff=value.public_cutoff,
            comparison_window=WINDOW,request_key=value.request_key,budget=value.budget),initial_trigger={
                'kind':'cached_case','case_id':CASE_ID,'case_manifest_hash':self.fingerprint,'job_request_key':'cached-case-initial'})
        root_run=next(r for r in research['runs'] if r['previous_run_id'] is None)
        return {'research_id':research['id'],'run_id':root_run['id'],'status':root_run['status'],
            'case_id':CASE_ID,'source_policy':'archived_public_sources_revalidated','external_requests':0,
            'reports_url':'/researches/'+research['id']+'/reports','execution_url':'/runs/'+root_run['id']+'/execution'}

    def prepare(self,store,run_id,guard):
        run=store.get_run(run_id);trigger=run['trigger']
        if trigger.get('case_manifest_hash')!=self.fingerprint:
            # 새 목록은 새 조사에만 적용한다. 이전 승인 실행은 기존 고정 목록으로 복구한다.
            previous=self.root/'data/research/case_library/sangsin-20261020.json'
            if previous.exists() and previous.resolve()!=self.path:
                original=CachedCaseLibrary(self.root,previous)
                if trigger.get('case_manifest_hash')==original.fingerprint:
                    return original.prepare(store,run_id,guard)
        if trigger.get('case_id')!=CASE_ID or trigger.get('case_manifest_hash')!=self.fingerprint:
            raise IntegrityFailure('cached_case_manifest_mismatch')
        self.verify()
        with store.connection() as conn:
            _,basis=store._context(conn,run_id)
            company=json.loads(store._one(conn,'research_researches',basis['research_id'])['company_json'])
            b=json.loads(basis['data_json'])
            if company.get('corp_code')!=COMPANY['corp_code'] or b['public_cutoff']!='2026-10-20' or b['comparison_window']!=WINDOW:
                raise Conflict('다른 회사·기준의 보관 자료를 혼합하지 않습니다.')
        with store._source_lock(run_id,blocking=True):
            store.recover_source_writes()
            with store.connection() as conn:
                existing=[dict(r) for r in conn.execute('SELECT * FROM research_sources WHERE run_id=?',(run_id,))]
            old={}
            expected_ids={row['id'] for row in self.manifest['sources']}
            for row in existing:
                period=json.loads(row['metadata_json'])['reporting_period']
                origin=period.get('cached_original_source_id')
                if not origin or origin not in expected_ids:raise IntegrityFailure('unexpected_source_in_cached_run')
                if origin:
                    if origin in old:raise IntegrityFailure('duplicate_cached_import')
                    old[origin]=row
            mapping={};pending={row['id']:row for row in self.manifest['sources']}
            while pending:
                ready=[]
                for sid,row in pending.items():
                    meta=row['metadata'];period=meta['reporting_period']
                    deps=[period[k] for k in ('archive_source_id','referring_source_id') if period.get(k)]
                    if meta.get('supersedes_id'):deps.append(meta['supersedes_id'])
                    if all(dep in mapping for dep in deps):ready.append(sid)
                if not ready:raise IntegrityFailure('cached_reference_missing_or_cycle')
                for sid in ready:
                    guard();row=pending.pop(sid);meta=json.loads(json.dumps(row['metadata']))
                    for key in ('archive_source_id','referring_source_id'):
                        if meta['reporting_period'].get(key):meta['reporting_period'][key]=mapping[meta['reporting_period'][key]]
                    if meta.get('supersedes_id'):meta['supersedes_id']=mapping[meta['supersedes_id']]
                    meta['reporting_period'].update(cached_case_id=CASE_ID,cached_manifest_hash=self.fingerprint,
                        cached_original_source_id=sid,cached_original_hash=row['content_hash'])
                    value=SourceInput.model_validate(meta)
                    if sid in old:
                        copied=old[sid]
                        if (safe_payload(meta)!=copied['metadata_json'] or copied['content_hash']!=row['content_hash']
                                or digest(store.source_content(copied['id']))!=row['content_hash']):
                            raise IntegrityFailure('cached_import_changed')
                        mapping[sid]=copied['id']
                    else:
                        raw=raw_file(self.directory,row['storage_key']).read_bytes()
                        if digest(raw)!=row['content_hash']:raise IntegrityFailure('cached_source_changed')
                        copied=store.save_source(run_id,value,raw);mapping[sid]=copied['id']
            guard()
            with store.connection(write=True) as conn:
                current,basis=store._context(conn,run_id)
                if current['status']!='running':raise Conflict('자료 준비 중 조사가 중단됐습니다.')
                done=conn.execute("SELECT 1 FROM research_events WHERE run_id=? AND kind='cached_sources_prepared'",(run_id,)).fetchone()
                if not done:store._event(conn,basis['research_id'],basis['id'],run_id,'cached_sources_prepared',
                    {'case_id':CASE_ID,'manifest_hash':self.fingerprint,'source_count':len(mapping),
                     'external_requests':0,'validation_flags_copied':False})
        return {'case_id':CASE_ID,'source_count':len(mapping),'external_requests':0,'validation_flags_copied':False}
