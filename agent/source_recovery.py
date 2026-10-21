"""원문 저장 저널과 프로세스 간 잠금. 확인된 파일만 등록하고 불명 파일은 보존한다."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4


class SourceRecovery:
    def _safe_source_path(self,key):
        from .research_store import IntegrityFailure
        relative=Path(key)
        if relative.is_absolute() or any(p in ('..','.') for p in relative.parts):
            raise IntegrityFailure('unsafe_source_write_path')
        path=self.source_root/relative
        if not path.resolve().is_relative_to(self.source_root):raise IntegrityFailure('unsafe_source_write_path')
        cursor=self.source_root
        for part in relative.parts:
            cursor=cursor/part
            if cursor.is_symlink():raise IntegrityFailure('source_write_symlink_not_allowed')
        return path

    @contextmanager
    def _source_lock(self,source_id,blocking=False):
        if not re.fullmatch(r'[0-9a-f]{32}',source_id):raise ValueError('invalid_source_write_id')
        lock=self._safe_source_path('_source_locks/'+source_id+'.lock')
        lock.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(lock,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        acquired=False
        try:
            try:
                fcntl.flock(fd,fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB));acquired=True
            except BlockingIOError:pass
            yield acquired
        finally:
            if acquired:fcntl.flock(fd,fcntl.LOCK_UN)
            os.close(fd)

    @staticmethod
    def _sync_directory(path):
        fd=os.open(path,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)

    def _write_source(self,run_id,metadata_json,content):
        from .research_store import digest, now
        source_id=uuid4().hex;content_hash=digest(content);metadata=json.loads(metadata_json)
        with self._source_lock(source_id,blocking=True):
            with self.connection(write=True) as conn:
                _,basis=self._context(conn,run_id)
                if metadata.get('supersedes_id'):
                    parent=self._one(conn,'research_sources',metadata['supersedes_id'])
                    _,other=self._context(conn,parent['run_id'])
                    if other['research_id']!=basis['research_id']:raise ValueError('source_research_mismatch')
                category='official' if metadata['kind'] in ('dart','official_ir','official_standard') else 'provided'
                relative=Path(basis['research_id'])/category/source_id/(content_hash+'.bin')
                temporary=relative.parent/('.pending-'+source_id)
                stamp=now()
                conn.execute('INSERT INTO research_source_writes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    (source_id,run_id,metadata_json,content_hash,len(content),relative.as_posix(),temporary.as_posix(),
                     digest(str(self.source_root).encode()),'pending',None,stamp,stamp))
                self._event(conn,basis['research_id'],basis['id'],run_id,'source_write_started',{'source_id':source_id})
            try:
                target=self._safe_source_path(relative);temp=self._safe_source_path(temporary)
                target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists():raise ValueError('source_write_target_already_exists')
                with temp.open('xb') as stream:
                    stream.write(content);stream.flush();os.fsync(stream.fileno())
                self._sync_directory(temp.parent)
                os.replace(temp,target);self._sync_directory(target.parent)
                with self.connection() as conn:
                    entry=dict(self._one(conn,'research_source_writes',source_id))
                self._finish_source_write(entry)
            except BaseException:
                try:self._review_source_write(source_id,'source_registration_not_completed')
                except Exception:pass
                raise
        return self.get_source(source_id)

    def _check_write_descriptor(self,entry):
        from .research_models import SourceInput
        from .research_store import IntegrityFailure, digest, safe_payload
        if entry['root_digest']!=digest(str(self.source_root).encode()):raise IntegrityFailure('source_root_mismatch')
        metadata=json.loads(entry['metadata_json']);value=SourceInput.model_validate(metadata)
        if safe_payload(value.model_dump(mode='json'))!=entry['metadata_json']:raise IntegrityFailure('source_write_metadata_mismatch')
        with self.connection() as conn:
            _,basis=self._context(conn,entry['run_id'])
        category='official' if metadata['kind'] in ('dart','official_ir','official_standard') else 'provided'
        expected=Path(basis['research_id'])/category/entry['id']/(entry['content_hash']+'.bin')
        if entry['storage_key']!=expected.as_posix() or entry['temporary_key']!=(expected.parent/('.pending-'+entry['id'])).as_posix():
            raise IntegrityFailure('source_write_path_mismatch')
        return metadata

    def _file_matches_write(self,path,entry):
        if not path.is_file() or path.is_symlink() or path.stat().st_size!=entry['byte_size']:return False
        h=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
        return h.hexdigest()==entry['content_hash']

    def _finish_source_write(self,entry,recovered=False):
        from .research_store import IntegrityFailure, now
        metadata=self._check_write_descriptor(entry)
        path=self._safe_source_path(entry['storage_key'])
        if not self._file_matches_write(path,entry):raise IntegrityFailure('source_write_content_mismatch')
        with self.connection(write=True) as conn:
            current=self._one(conn,'research_source_writes',entry['id'])
            existing=conn.execute('SELECT * FROM research_sources WHERE id=?',(entry['id'],)).fetchone()
            _,basis=self._context(conn,entry['run_id'])
            if existing:
                if any(existing[k]!=entry[k] for k in ('run_id','metadata_json','content_hash','byte_size','storage_key')):
                    raise IntegrityFailure('source_write_existing_record_mismatch')
            else:
                if current['state']=='committed':raise IntegrityFailure('source_write_committed_record_missing')
                if metadata.get('supersedes_id'):
                    parent=self._one(conn,'research_sources',metadata['supersedes_id'])
                    _,other=self._context(conn,parent['run_id'])
                    if other['research_id']!=basis['research_id']:raise IntegrityFailure('source_research_mismatch')
                conn.execute('INSERT INTO research_sources VALUES (?,?,?,?,?,?,?)',
                    (entry['id'],entry['run_id'],entry['metadata_json'],entry['content_hash'],entry['byte_size'],entry['storage_key'],now()))
                self._event(conn,basis['research_id'],basis['id'],entry['run_id'],'source_stored',
                    {'source_id':entry['id'],'content_hash':entry['content_hash'],'recovered':recovered})
            if current['state']!='committed':
                conn.execute("UPDATE research_source_writes SET state='committed',reason=NULL,updated_at=? WHERE id=?",(now(),entry['id']))
                self._event(conn,basis['research_id'],basis['id'],entry['run_id'],'source_write_completed',
                    {'source_id':entry['id'],'recovered':recovered})

    def _review_source_write(self,source_id,reason):
        from .research_store import now
        with self.connection(write=True) as conn:
            entry=self._one(conn,'research_source_writes',source_id)
            if entry['state']=='committed' or (entry['state']=='needs_review' and entry['reason']==reason):return
            conn.execute("UPDATE research_source_writes SET state='needs_review',reason=?,updated_at=? WHERE id=?",(reason,now(),source_id))
            _,basis=self._context(conn,entry['run_id'])
            self._event(conn,basis['research_id'],basis['id'],entry['run_id'],'source_write_needs_review',
                        {'source_id':source_id,'reason':reason})

    def recover_source_writes(self):
        from .research_store import IntegrityFailure
        with self.connection() as conn:
            entries=[dict(r) for r in conn.execute("SELECT * FROM research_source_writes WHERE state!='committed'")]
        summary={'recovered':[],'active':[],'needs_review':[]}
        for entry in entries:
            try:
                with self._source_lock(entry['id']) as acquired:
                    if not acquired:summary['active'].append(entry['id']);continue
                    self._check_write_descriptor(entry)
                    target=self._safe_source_path(entry['storage_key']);temporary=self._safe_source_path(entry['temporary_key'])
                    if not target.exists():
                        if not temporary.exists():raise IntegrityFailure('source_write_file_missing')
                        if not self._file_matches_write(temporary,entry):raise IntegrityFailure('source_write_incomplete_temporary')
                        os.replace(temporary,target);self._sync_directory(target.parent)
                    self._finish_source_write(entry,recovered=True)
                    summary['recovered'].append(entry['id'])
            except (ValueError,OSError,sqlite3.Error) as error:
                # 예외 전문/경로/입력값은 이벤트에 복사하지 않는다.
                reason=str(error) if isinstance(error,IntegrityFailure) else 'source_write_recovery_not_completed'
                try:self._review_source_write(entry['id'],reason)
                except Exception:pass
                summary['needs_review'].append({'source_id':entry['id'],'reason':reason})
        return summary

    def source_write_status(self):
        from .research_store import row_data
        with self.connection() as conn:
            writes=[row_data(r) for r in conn.execute("SELECT * FROM research_source_writes WHERE state!='committed' ORDER BY created_at")]
            known={r[0] for r in conn.execute('SELECT storage_key FROM research_sources')}
            for row in conn.execute('SELECT storage_key,temporary_key FROM research_source_writes'):known.update(row)
            research_ids=[r[0] for r in conn.execute('SELECT id FROM research_researches')]
        unregistered=[]
        for research_id in research_ids:
            for category in ('official','provided'):
                directory=self._safe_source_path(Path(research_id)/category)
                if not directory.exists():continue
                for path in directory.glob('*/*'):
                    relative=path.relative_to(self.source_root).as_posix()
                    if relative not in known and (path.is_file() or path.is_symlink()):unregistered.append(relative)
        # 내부 저장 키는 외부에서 지정할 수 없으며 목록에는 상대 위치만 반환한다.
        for item in writes:
            for key in ('metadata','root_digest','temporary_key'):item.pop(key,None)
        return {'pending_writes':writes,'unregistered_files':sorted(unregistered),
                'startup_recovery':getattr(self,'recovery_summary',None)}
