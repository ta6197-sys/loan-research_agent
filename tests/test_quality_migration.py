import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent.research_store import ResearchStore


class QualityMigrationTests(unittest.TestCase):
    def test_new_store_has_v6_review_table_and_all_migrations(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ResearchStore(Path(directory) / 'research.db', Path(directory) / 'sources')
            with store.connection() as conn:
                versions = [row[0] for row in conn.execute('SELECT version FROM research_migrations ORDER BY version')]
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual([1, 2, 3, 4, 5, 6], versions)
            self.assertIn('research_quality_review_events', tables)

    def test_v5_database_upgrades_without_replacing_prior_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); db = root / 'research.db'
            store = ResearchStore(db, root / 'sources')
            with store.connection(write=True) as conn:
                conn.execute('DROP TABLE research_quality_review_events')
                conn.execute('DELETE FROM research_migrations WHERE version=6')
            upgraded = ResearchStore(db, root / 'sources')
            with upgraded.connection() as conn:
                self.assertEqual(6, conn.execute('SELECT MAX(version) FROM research_migrations').fetchone()[0])
