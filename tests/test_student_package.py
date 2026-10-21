"""실제 자격증명 없이 합성 파일로 ZIP 유출 방지·원본 보존을 확인한다."""
import hashlib
from pathlib import Path
import tempfile
import unittest
import zipfile

from sh.package_student import NAME, make_archive


class StudentPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'project'
        self.root.mkdir()

    def write(self, relative, content=b'synthetic-only'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_private_files_excluded_and_code_example_and_synthetic_fixtures_kept(self):
        private = (
            '.env', '.env.local', '.env.backup', 'nested/.env.example', 'nested/.env',
            'output/pdf/report.pdf', 'tmp/pdfs/page.png', 'session.db', 'session.db.research-worker.lock', 'nested/custom.sqlite3', 'custom.db-wal', 'custom.sqlite-shm',
            'private.key', 'private.pem', 'previous.zip', '.aws/credentials', '.git/config',
            '.codex/config.toml', '.agents/local.txt', 'data/research/raw.xml',
            'doc/finance/cases/company/raw.xml', 'doc/finance/evaluation/actual.json',
            'doc/finance/evaluation/EXPECTED_RESULTS.md', 'doc/image_usecase/screen.png',
        )
        public = ('.env.example', 'server.py', 'doc/README.md',
                  'doc/finance/evaluation/synthetic/followup_cases.json')
        for name in private + public:
            self.write(name)
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob('*') if p.is_file()}
        output = self.base / 'safe.zip'
        self.assertEqual(len(public), make_archive(self.root, output))
        with zipfile.ZipFile(output) as archive:
            self.assertEqual({f'{NAME}/{name}' for name in public}, set(archive.namelist()))
            self.assertIsNone(archive.testzip())
        self.assertTrue(all(p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == digest
                            for p, digest in before.items()))

    def test_symlink_files_directories_and_examples_cannot_export_outside_data(self):
        outside = self.base / 'outside'
        outside.mkdir()
        secret = outside / 'secret.txt'
        secret.write_bytes(b'synthetic-only-secret')
        (self.root / '.env.example').symlink_to(secret)
        (self.root / 'linked.txt').symlink_to(secret)
        (self.root / 'linked-directory').symlink_to(outside, target_is_directory=True)
        self.write('server.py')
        output = self.base / 'safe.zip'
        make_archive(self.root, output)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual([f'{NAME}/server.py'], archive.namelist())
        self.assertEqual(b'synthetic-only-secret', secret.read_bytes())

    def test_existing_zip_and_output_symlink_are_preserved(self):
        self.write('server.py')
        output = self.base / 'existing.zip'
        output.write_bytes(b'existing archive')
        with self.assertRaises(FileExistsError):
            make_archive(self.root, output)
        self.assertEqual(b'existing archive', output.read_bytes())
        link = self.base / 'linked.zip'
        link.symlink_to(output)
        with self.assertRaises(FileExistsError):
            make_archive(self.root, link)
        self.assertTrue(link.is_symlink())

    def test_output_within_source_does_not_include_staging_archive(self):
        self.write('server.py')
        output = self.root / 'safe.zip'
        make_archive(self.root, output)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual([f'{NAME}/server.py'], archive.namelist())
        self.assertEqual([], list(self.root.glob('.student-package-*')))
