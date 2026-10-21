"""자체 생성 원문을 쓰는 자식 프로세스를 SIGKILL해 저장 복구를 확인한다."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest

from agent.research_store import ResearchStore
from agent.research_models import ResearchInput,SourceInput

ROOT=Path(__file__).resolve().parents[1]
CHILD='''
from pathlib import Path
import os,signal,sys
from agent.research_store import ResearchStore
from agent.research_models import SourceInput
root=Path(sys.argv[1]);phase=sys.argv[2]
store=ResearchStore(root/'session.db',root/'sources')
def pause():
 (root/'ready').write_text('ready')
 signal.pause()
if phase=='after_temp':
 original=store._sync_directory
 def checkpoint(path):original(path);pause()
 store._sync_directory=checkpoint
elif phase=='after_file':
 def checkpoint(entry,recovered=False):pause()
 store._finish_source_write=checkpoint
elif phase=='after_commit':
 original=store._finish_source_write
 def checkpoint(entry,recovered=False):original(entry,recovered=recovered);pause()
 store._finish_source_write=checkpoint
store.save_source(sys.argv[3],SourceInput(kind='synthetic',title='직접 만든 복구 시험',publisher='시험',
 retrieved_at='2026-10-21',rights_status='synthetic'),b'original bytes for crash test')
'''


class SourceRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.store=ResearchStore(self.root/'session.db',self.root/'sources')
        r=self.store.create_research(ResearchInput(company={'name':'합성 기업'},purpose='복구 시험',
            public_cutoff='2026-10-20',comparison_window={},request_key='recovery'))
        self.run_id=r['runs'][0]['id']

    def child(self,phase):
        process=subprocess.Popen([sys.executable,'-c',CHILD,str(self.root),phase,self.run_id],cwd=ROOT,
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        deadline=time.monotonic()+10
        while not (self.root/'ready').exists():
            if process.poll() is not None:raise AssertionError('crash child failed to start')
            if time.monotonic()>deadline:raise AssertionError('crash checkpoint timeout')
            time.sleep(0.02)
        return process

    def reopen(self):return ResearchStore(self.root/'session.db',self.root/'sources')

    def entries(self):
        with self.store.connection() as c:return [dict(r) for r in c.execute('SELECT * FROM research_source_writes')]

    def test_actual_kill_after_file_before_db_completes_once(self):
        child=self.child('after_file');entry=self.entries()[0]
        child.kill();child.wait(timeout=5)
        recovered=self.reopen()
        self.assertEqual([entry['id']],recovered.recovery_summary['recovered'])
        self.assertEqual(b'original bytes for crash test',recovered.source_content(entry['id']))
        self.assertEqual('committed',self.entries()[0]['state'])
        before=self.store.events(self._research_id())
        self.reopen()
        self.assertEqual(before,self.store.events(self._research_id()))
        self.assertEqual(1,len([e for e in before if e['kind']=='source_stored']))

    def _research_id(self):
        with self.store.connection() as c:return c.execute('SELECT id FROM research_researches').fetchone()[0]

    def test_actual_kill_after_temp_can_finish_verified_file(self):
        child=self.child('after_temp');entry=self.entries()[0];child.kill();child.wait(timeout=5)
        self.assertFalse((self.store.source_root/entry['storage_key']).exists())
        store=self.reopen()
        self.assertEqual([entry['id']],store.recovery_summary['recovered'])
        self.assertEqual(b'original bytes for crash test',store.source_content(entry['id']))

    def test_active_writer_is_never_taken_over(self):
        child=self.child('after_file');entry=self.entries()[0]
        opened=self.reopen()
        self.assertEqual([entry['id']],opened.recovery_summary['active'])
        with opened.connection() as c:self.assertEqual(0,c.execute('SELECT COUNT(*) FROM research_sources').fetchone()[0])
        self.assertEqual('pending',self.entries()[0]['state'])
        child.kill();child.wait(timeout=5);self.assertEqual([entry['id']],self.reopen().recovery_summary['recovered'])

    def test_actual_kill_after_commit_does_not_duplicate(self):
        child=self.child('after_commit');entry=self.entries()[0];child.kill();child.wait(timeout=5)
        reopened=self.reopen()
        self.assertEqual([],reopened.recovery_summary['recovered'])
        with reopened.connection() as c:self.assertEqual(1,c.execute('SELECT COUNT(*) FROM research_sources').fetchone()[0])

    def test_changed_or_missing_file_is_preserved_and_not_registered(self):
        child=self.child('after_temp');entry=self.entries()[0];child.kill();child.wait(timeout=5)
        path=self.store.source_root/entry['temporary_key'];path.write_bytes(b'incomplete')
        reopened=self.reopen()
        self.assertEqual([],reopened.recovery_summary['recovered']);self.assertEqual(b'incomplete',path.read_bytes())
        self.assertEqual('needs_review',self.entries()[0]['state'])
        with reopened.connection() as c:self.assertEqual(0,c.execute('SELECT COUNT(*) FROM research_sources').fetchone()[0])
        path.unlink();self.reopen()
        self.assertEqual('source_write_file_missing',self.entries()[0]['reason'])

    def test_unknown_file_is_listed_without_automatic_registration_or_deletion(self):
        directory=self.store.source_root/self._research_id()/'provided'/'unknown';directory.mkdir(parents=True)
        path=directory/'unknown.bin';path.write_bytes(b'unknown original')
        reopened=self.reopen();status=reopened.source_write_status()
        self.assertIn(path.relative_to(self.store.source_root).as_posix(),status['unregistered_files'])
        self.assertEqual(b'unknown original',path.read_bytes())

    def test_db_registration_failure_preserves_original_then_recovers(self):
        with self.store.connection(write=True) as c:
            c.execute("CREATE TRIGGER inject_source_failure BEFORE INSERT ON research_sources BEGIN SELECT RAISE(ABORT,'injected'); END")
        value=SourceInput(kind='synthetic',title='합성 자료',publisher='시험',retrieved_at='2026-10-21',rights_status='synthetic')
        with self.assertRaises(sqlite3.IntegrityError):self.store.save_source(self.run_id,value,b'preserved')
        entry=self.entries()[0];path=self.store.source_root/entry['storage_key'];self.assertEqual(b'preserved',path.read_bytes())
        reopened=self.reopen();self.assertEqual([],reopened.recovery_summary['recovered'])
        with self.store.connection(write=True) as c:c.execute('DROP TRIGGER inject_source_failure')
        self.assertEqual([entry['id']],self.reopen().recovery_summary['recovered'])

    def test_different_source_root_and_symlinks_are_not_followed(self):
        child=self.child('after_file');entry=self.entries()[0];child.kill();child.wait(timeout=5)
        other=ResearchStore(self.root/'session.db',self.root/'wrong_sources')
        self.assertEqual([],other.recovery_summary['recovered']);self.assertEqual('source_root_mismatch',self.entries()[0]['reason'])
        path=self.store.source_root/entry['storage_key'];original=path.read_bytes();path.unlink()
        outside=self.root/'outside.bin';outside.write_bytes(original);path.symlink_to(outside)
        reopened=self.reopen();self.assertEqual([],reopened.recovery_summary['recovered']);self.assertEqual(original,outside.read_bytes())
