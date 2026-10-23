"""단계 0~1 수용 시험. 합성 자료만 사용하며 금융 정확성을 평가하지 않는다."""
import concurrent.futures
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from fastapi.testclient import TestClient
from pydantic import ValidationError

from agent.research_models import (
    BasisInput, FactInput, FragmentInput, ReportInput, ResearchInput, SourceInput, Usage,
)
from agent.research_store import Conflict, IntegrityFailure, ResearchStore
from agent.session_store import SqliteSessionStore


ROOT = Path(__file__).resolve().parents[1]


def research_input(key="request-1"):
    return ResearchInput(
        company={"name": "합성 제조기업"}, purpose="운전자금 사전조사 저장 시험",
        public_cutoff="2026-10-20", comparison_window={
            "annual_years": [2023, 2024, 2025],
            "interim_periods": ["2025-H1", "2026-H1"],
            "balance_comparison": "previous_year_end", "flow_comparison": "same_period",
        }, request_key=key,
    )


def source_input(**changes):
    values = dict(kind="synthetic", title="직접 만든 시험 자료", publisher="저장 시험",
                  retrieved_at="2026-10-20T12:00:00+00:00", rights_status="synthetic")
    values.update(changes)
    return SourceInput(**values)


def fact_input(**changes):
    values = dict(account="cash", original_account="시험 현금", raw_value="0",
                  exact_value="0", scope="OFS", period={"as_of": "2026-06-30"},
                  currency="KRW", unit="원")
    values.update(changes)
    return FactInput(**values)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.db = self.directory / "session.db"
        self.sources = self.directory / "sources"
        session = SqliteSessionStore(self.db)
        session.append("conversation-only", "user", "기존 대화 보존")
        session.close()
        self.store = ResearchStore(self.db, self.sources)
        self.research = self.store.create_research(research_input())
        self.run_id = self.research["runs"][0]["id"]

    def make_fact(self, store=None, run_id=None):
        store, run_id = store or self.store, run_id or self.run_id
        source = store.save_source(run_id, source_input(), b"row 1: cash = 0\n")
        fragment = store.save_fragment(source["id"], FragmentInput(
            location={"line": 1, "column": "cash"}, text="cash = 0",
            context={"unit": "KRW", "scope": "OFS"},
            extraction_warnings=["synthetic fixture; not financial verification"],
        ))
        fact = store.save_fact(run_id, fragment["id"], fact_input())
        return source, fragment, fact

    def test_migration_preserves_messages_and_reopens(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            before = conn.execute("SELECT rowid,* FROM messages").fetchall()
            schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='messages'").fetchone()
        reopened = ResearchStore(self.db, self.sources)
        with closing(sqlite3.connect(self.db)) as conn, conn:
            self.assertEqual(before, conn.execute("SELECT rowid,* FROM messages").fetchall())
            self.assertEqual(schema, conn.execute("SELECT sql FROM sqlite_master WHERE name='messages'").fetchone())
            self.assertEqual([(1,), (2,), (3,), (4,), (5,), (6,)], conn.execute("SELECT version FROM research_migrations ORDER BY version").fetchall())
            self.assertEqual([], conn.execute("PRAGMA foreign_key_check").fetchall())
        self.assertEqual(self.research, reopened.get_research(self.research["id"]))
        session = SqliteSessionStore(self.db)
        self.addCleanup(session.close)
        self.assertEqual([{"role": "user", "content": "기존 대화 보존"}], session.load("conversation-only"))
        self.assertEqual([], session.load(self.research["id"]))

    def test_creation_is_idempotent_and_concurrent(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(lambda _: self.store.create_research(research_input("same"))["id"], range(8)))
        self.assertEqual(1, len(set(ids)))
        changed = research_input("same").model_copy(update={"purpose": "다른 목적"})
        with self.assertRaises(Conflict):
            self.store.create_research(changed)
        self.assertEqual(1, len(self.store.events(ids[0])))

    def test_basis_versions_preserve_old_run_and_reject_stale_write(self):
        basis_input = BasisInput.model_validate(research_input().model_dump(exclude={"company", "request_key", "budget"}))
        basis_input = basis_input.model_copy(update={"purpose": "수정한 목적"})
        old = self.research["bases"][0]
        new = self.store.create_basis(self.research["id"], basis_input, expected_version=1)
        self.assertEqual(2, new["version"])
        self.assertEqual(old["id"], self.store.get_run(self.run_id)["basis_id"])
        with self.assertRaises(Conflict):
            self.store.create_basis(self.research["id"], basis_input, expected_version=1)
        self.assertEqual(old, self.store.get_research(self.research["id"])["bases"][0])
        with self.store.connection(write=True) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE research_bases SET version=9 WHERE id=?", (old["id"],))

    def test_snapshots_usage_and_events_are_atomic(self):
        saved = self.store.save_snapshot(self.run_id, expected_revision=0, stage="source_saved",
            state={"input": {"message_id": "m1"}, "plan": ["identify", "collect"], "next": "extract"},
            usage=Usage(external_requests=3, model_calls=1, execution_seconds=23))
        self.assertEqual(1, saved["revision"])
        count = len(self.store.events(self.research["id"]))
        for revision, usage in [(0, Usage(external_requests=3)), (1, Usage())]:
            with self.assertRaises(Conflict):
                self.store.save_snapshot(self.run_id, expected_revision=revision, stage="invalid",
                                         state={}, usage=usage)
        reopened = ResearchStore(self.db, self.sources)
        run = reopened.get_run(self.run_id)
        self.assertEqual(3, run["usage"]["external_requests"])
        self.assertEqual([saved], run["snapshots"])
        self.assertEqual(count, len(reopened.events(self.research["id"])))
        events = reopened.events(self.research["id"], after=1)
        self.assertEqual([2], [event["sequence"] for event in events])
        followup = reopened.create_run(self.research["bases"][0]["id"], previous_run_id=self.run_id)
        self.assertEqual(0, followup["usage"]["external_requests"])
        self.assertEqual(3, reopened.get_research(self.research["id"])["cumulative_usage"]["external_requests"])

    def test_raw_source_location_exact_decimal_and_missing_zero(self):
        source, fragment, fact = self.make_fact()
        self.assertEqual(b"row 1: cash = 0\n", self.store.source_content(source["id"]))
        self.assertEqual(hashlib.sha256(b"row 1: cash = 0\n").hexdigest(), source["content_hash"])
        self.assertEqual({"line": 1, "column": "cash"}, fact["fragment"]["data"]["location"])
        self.assertEqual("candidate", fact["status"])
        self.assertEqual("0", fact["data"]["exact_value"])
        exact = self.store.save_fact(self.run_id, fragment["id"], fact_input(
            raw_value="9007199254740993.123456789", exact_value="9007199254740993.123456789"))
        self.assertEqual("9007199254740993.123456789", exact["data"]["exact_value"])
        missing = self.store.save_fact(self.run_id, fragment["id"], fact_input(
            raw_value=None, exact_value=None, missing_reason="미공시 시험"))
        self.assertIsNone(missing["data"]["exact_value"])
        with self.assertRaises(ValueError):
            self.store.save_fact(self.run_id, fragment["id"], fact_input(exact_value=None))
        with self.assertRaises(ValidationError):
            fact_input(exact_value=0.1)
        with self.assertRaises(ValidationError):
            fact_input(exact_value="NaN")
        with self.store.connection() as conn:
            key = conn.execute("SELECT storage_key FROM research_sources WHERE id=?", (source["id"],)).fetchone()[0]
        (self.sources / key).write_bytes(b"modified")
        with self.assertRaises(IntegrityFailure):
            self.store.source_content(source["id"])

    def test_database_and_source_files_are_owner_only(self):
        source, _, _ = self.make_fact()
        with self.store.connection() as conn:
            key = conn.execute("SELECT storage_key FROM research_sources WHERE id=?", (source["id"],)).fetchone()[0]
        raw = self.sources / key
        self.assertEqual(0o600, stat.S_IMODE(self.db.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(self.sources.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(raw.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(raw.parent.stat().st_mode))
        os.chmod(self.db, 0o644)
        os.chmod(raw, 0o644)
        os.chmod(raw.parent, 0o755)
        ResearchStore(self.db, self.sources)
        self.assertEqual(0o600, stat.S_IMODE(self.db.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(raw.stat().st_mode))
        self.assertEqual(0o700, stat.S_IMODE(raw.parent.stat().st_mode))

    def test_different_research_or_basis_cannot_mix_evidence(self):
        _, fragment, fact = self.make_fact()
        second = self.store.create_research(research_input("other"))
        with self.assertRaises(Conflict):
            self.store.save_fact(second["runs"][0]["id"], fragment["id"], fact_input())
        report = ReportInput(request_key="bad", evidence_ids=[fact["id"]], limitations=["시험"])
        with self.assertRaises(Conflict):
            self.store.save_report(second["runs"][0]["id"], report)
        with self.assertRaises(Conflict):
            self.store.create_run(second["bases"][0]["id"], previous_run_id=self.run_id)

    def test_report_versions_are_immutable_and_idempotent(self):
        _, _, fact = self.make_fact()
        first = ReportInput(request_key="report1", evidence_ids=[fact["id"]],
                            unknowns=[{"text": "검증은 이후 단계"}], limitations=["합성 자료 저장 시험"])
        v1 = self.store.save_report(self.run_id, first)
        self.assertEqual(v1, self.store.save_report(self.run_id, first))
        second = first.model_copy(update={"request_key": "report2", "questions": [{"text": "후속 질문 저장"}]})
        v2 = self.store.save_report(self.run_id, second)
        self.assertEqual(2, v2["version"])
        self.assertEqual("building", v1["status"])
        self.assertEqual(v1["content"], self.store.get_report(v1["id"])["content"])
        self.assertEqual([fact["id"]], self.store.get_report(v1["id"])["evidence_ids"])
        with self.assertRaises(Conflict):
            self.store.save_report(self.run_id, first.model_copy(update={"limitations": ["다른 내용"]}))
        with self.assertRaises(ValueError):
            self.store.save_report(self.run_id, first.model_copy(update={"confirmed_facts": [{"text": "미검증"}]}))
        with self.store.connection(write=True) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE research_reports SET status='building' WHERE id=?", (v1["id"],))

    def test_credentials_and_authentication_urls_are_rejected(self):
        for url in ("https://example.test/data?crtfc_key=should-not-save",
                    "https://user:password@example.test/data"):
            with self.assertRaises(ValueError):
                self.store.save_source(self.run_id, source_input(url=url), b"test")
        with self.assertRaises(ValueError):
            self.store.save_snapshot(self.run_id, expected_revision=0, stage="test",
                                     state={"api_key": "should-not-save"}, usage=Usage())
        with patch.dict(os.environ, {"OPENDART_API_KEY": "synthetic-test-secret"}):
            with self.assertRaises(ValueError):
                self.store.save_snapshot(self.run_id, expected_revision=0, stage="test",
                                         state={"note": "synthetic-test-secret"}, usage=Usage())
        self.assertEqual(1, len(self.store.events(self.research["id"])))

    def test_transaction_failure_rolls_back_record_and_event_together(self):
        with patch.object(self.store, "_event", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(RuntimeError):
                self.store.create_research(research_input("failed-transaction"))
        self.assertEqual(1, len(self.store.list_researches()))
        self.assertEqual(1, len(self.store.events(self.research["id"])))

    def test_failed_source_write_removes_unreferenced_file(self):
        with patch.object(self.store, "_event", side_effect=RuntimeError("synthetic failure")):
            with self.assertRaises(RuntimeError):
                self.store.save_source(self.run_id, source_input(), b"not committed")
        self.assertEqual([], list(self.sources.rglob("*.bin")))
        with self.store.connection() as conn:
            self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM research_sources").fetchone()[0])

    def test_known_secret_in_raw_source_and_raw_prompt_are_rejected(self):
        with patch.dict(os.environ, {"OPENDART_API_KEY": "synthetic-test-secret"}):
            with self.assertRaises(ValueError):
                self.store.save_source(self.run_id, source_input(), b"synthetic-test-secret")
        with self.assertRaises(ValueError):
            self.store.save_snapshot(self.run_id, expected_revision=0, stage="test",
                                     state={"last_prompt": "private prompt"}, usage=Usage())

    def test_default_mode_does_not_initialize_model_or_ops_mcp(self):
        with patch.dict(os.environ, {"APP_MODE": "research", "APP_DB_PATH": str(self.db),
                                     "APP_SOURCE_ROOT": str(self.sources)}):
            import server
            with patch.object(server, "MCPClient", side_effect=AssertionError("학습용 MCP가 시작되면 안 됩니다.")), \
                    patch.object(server, "_build_llms", side_effect=AssertionError("LLM should not start")):
                with TestClient(server.app, base_url="http://127.0.0.1:8100") as client:
                    self.assertEqual("disabled", client.get("/health").json()["llm_backend"])

    def test_external_model_requires_separate_setting(self):
        import server
        with patch.dict(os.environ, {"LLM_BACKEND": "openai", "APP_ALLOW_EXTERNAL_MODEL": "false"}):
            with self.assertRaises(RuntimeError):
                server._build_llms()

    def test_failed_migration_does_not_destroy_existing_database(self):
        path = self.directory / "future.db"
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("CREATE TABLE research_migrations (version INTEGER)")
            conn.execute("INSERT INTO research_migrations VALUES (999)")
        before = path.read_bytes()
        with self.assertRaises(IntegrityFailure):
            ResearchStore(path, self.sources)
        self.assertEqual(before, path.read_bytes())

    def test_http_storage_mode_and_request_boundaries(self):
        with patch.dict(os.environ, {"APP_MODE": "research", "APP_DB_PATH": str(self.db),
                                     "APP_SOURCE_ROOT": str(self.sources)}):
            from server import app
            with TestClient(app, base_url="http://127.0.0.1:8100") as client:
                self.assertFalse(client.get("/health").json()["execution_available"])
                self.assertEqual(409, client.post("/chat", json={"message": "test"}).status_code)
                self.assertEqual(403, client.post("/researches", headers={"Origin": "https://outside.test"},
                                                 json=research_input("bad-origin").model_dump(mode="json")).status_code)
                self.assertEqual(400, client.get("/health", headers={"Host": "outside.test"}).status_code)
                self.assertEqual(404, client.get("/researches/does-not-exist").status_code)
                result = client.post("/researches", json=research_input("api").model_dump(mode="json"))
                self.assertEqual(201, result.status_code)
                self.assertEqual(result.json(), client.get("/researches/" + result.json()["id"]).json())
                invalid = client.post("/researches", json={"api_key": "synthetic-secret"})
                self.assertEqual(422, invalid.status_code)
                self.assertNotIn("synthetic-secret", invalid.text)


class ProcessRestartTests(unittest.TestCase):
    def test_actual_http_process_kill_restart_retains_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            db, sources = directory / "session.db", directory / "sources"
            session = SqliteSessionStore(db)
            session.append("old-thread", "user", "대화는 독립 보존")
            session.close()
            env = {**os.environ, "APP_MODE": "research", "APP_DB_PATH": str(db),
                   "APP_SOURCE_ROOT": str(sources), "LANGCHAIN_TRACING_V2": "false",
                   "LANGSMITH_TRACING": "false", "APP_ALLOW_EXTERNAL_TRACE": "false"}
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            base = f"http://127.0.0.1:{port}"
            def request(path, body=None):
                data = None if body is None else json.dumps(body).encode()
                req = Request(base + path, data=data, headers={"Content-Type": "application/json"})
                with urlopen(req, timeout=3) as response:
                    content = response.read()
                    return json.loads(content) if response.headers.get_content_type() == "application/json" else content
            processes = []
            with (directory / "server.log").open("wb") as log:
                def start():
                    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1",
                                                "--port", str(port), "--log-level", "warning"],
                                               cwd=ROOT, env=env, stdout=log, stderr=log)
                    processes.append(process)
                    for _ in range(100):
                        if process.poll() is not None:
                            raise AssertionError("server startup failed: " + (directory / "server.log").read_text())
                        try:
                            if request("/health")["status"] == "ok":
                                return process
                        except (OSError, HTTPError):
                            time.sleep(0.05)
                    raise AssertionError("server readiness timeout")
                try:
                    first_process = start()
                    research = request("/researches", research_input("http-process").model_dump(mode="json"))
                    run_id = research["runs"][0]["id"]
                    store = ResearchStore(db, sources)
                    source = store.save_source(run_id, source_input(), b"synthetic original\n")
                    fragment = store.save_fragment(source["id"], FragmentInput(location={"line": 1}, text="0"))
                    fact = store.save_fact(run_id, fragment["id"], fact_input())
                    store.save_snapshot(run_id, expected_revision=0, stage="evidence_stored",
                                        state={"source_ids": [source["id"]], "next_stage": "validate"},
                                        usage=Usage(external_requests=2, model_calls=1, execution_seconds=7))
                    report = store.save_report(run_id, ReportInput(request_key="process-report",
                        evidence_ids=[fact["id"]], unknowns=[{"text": "미검증"}], limitations=["합성 자료만 사용"] ))
                    paths = ["/researches/" + research["id"], "/runs/" + run_id,
                             "/reports/" + report["id"], "/evidence/" + fact["id"],
                             "/researches/" + research["id"] + "/events", "/sources/" + source["id"] + "/view"]
                    before = {path: request(path) for path in paths}
                    # 비정상 종료 후 새 OS 프로세스에서 같은 DB를 연다.
                    first_process.kill()
                    first_process.wait(timeout=10)
                    second_process = start()
                    after = {path: request(path) for path in paths}
                    self.assertEqual(before, after)
                    self.assertEqual(2, after["/runs/" + run_id]["usage"]["external_requests"])
                    repeated = request("/researches", research_input("http-process").model_dump(mode="json"))
                    self.assertEqual(before["/researches/" + research["id"]], repeated)
                    second_process.terminate()
                    second_process.wait(timeout=10)
                    session = SqliteSessionStore(db)
                    try:
                        self.assertEqual("대화는 독립 보존", session.load("old-thread")[0]["content"])
                    finally:
                        session.close()
                finally:
                    for process in processes:
                        if process.poll() is None:
                            process.terminate()
                            process.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
