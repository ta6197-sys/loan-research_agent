"""조사 저장소 1단계: 짧은 트랜잭션, 불변 이력, 원문 해시, 낙관적 버전 검사.

실제 수집/검증/실행 관리자는 이후 단계에서 연결한다. 이 모듈은 모델을 호출하지 않는다.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from .research_models import (
    BasisInput, Budget, FactInput, FragmentInput, ReportInput, ResearchInput,
    SourceInput, Usage,
)
from .session_store import DB_PATH
from .source_recovery import SourceRecovery


class NotFound(ValueError):
    pass


class Conflict(ValueError):
    pass


class IntegrityFailure(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def digest(content):
    return hashlib.sha256(content).hexdigest()


def safe_payload(value):
    """인증 필드/인증 URL/환경에 설정된 비밀값은 저장하지 않는다.

    자유 텍스트 전체의 비밀 탐지를 보장하는 DLP는 아니다. 키를 입력받는 API도 없다.
    """
    sensitive = re.compile(r"api.?key|crtfc.?key|access.?token|auth|secret|password|signature|credential", re.I)
    def walk(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if sensitive.search(str(key)) or str(key).lower() in {
                    "prompt", "last_prompt", "raw_prompt", "system_prompt",
                }:
                    raise ValueError("인증 정보 필드는 저장할 수 없습니다.")
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, str):
            for url in re.findall(r"https?://[^\s<>\"']+", item):
                parts = urlsplit(url)
                if parts.username or parts.password or any(
                    sensitive.search(key) or key.lower() in {"key", "token"}
                    for key, _ in parse_qsl(parts.query)
                ):
                    raise ValueError("인증 정보가 포함된 URL은 저장할 수 없습니다.")
    walk(value)
    rendered = encode(value)
    for key, secret in os.environ.items():
        if sensitive.search(key) and len(secret) >= 8 and secret in rendered:
            raise ValueError("설정된 비밀값은 저장할 수 없습니다.")
    return rendered


def row_data(row):
    result = dict(row)
    for key in list(result):
        if key.endswith("_json"):
            result[key[:-5]] = json.loads(result.pop(key))
    result.pop("request_hash", None)
    result.pop("storage_key", None)
    return result


class ResearchStore(SourceRecovery):
    def __init__(self, db_path=DB_PATH, source_root=None):
        self.db_path = Path(db_path).resolve()
        self.source_root = Path(source_root or DB_PATH.parent / "data/research").resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()
        self.recovery_summary = self.recover_source_writes()

    @contextmanager
    def connection(self, write=False):
        # 요청별 연결: SQLite 연결을 여러 스레드에서 동시에 사용하지 않는다.
        conn = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _migrate(self):
        with self.connection(write=True) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "research_migrations" in tables:
                versions = [r[0] for r in conn.execute("SELECT version FROM research_migrations ORDER BY version")]
                if versions not in ([1], [1, 2], [1, 2, 3], [1, 2, 3, 4], [1, 2, 3, 4, 5]):
                    raise IntegrityFailure("지원하지 않는 조사 DB 버전입니다. 기존 DB는 변경하지 않았습니다.")
            else:
                if any(name.startswith("research_") for name in tables):
                    raise IntegrityFailure("미완성 조사 스키마를 발견했습니다. 기존 DB를 보존합니다.")
                versions = []
            for version, filename in ((1, "research_schema.sql"), (2, "research_schema_v2.sql"), (3, "research_schema_v3.sql"), (4, "research_schema_v4.sql"), (5, "research_schema_v5.sql")):
                if version in versions:
                    continue
                statement = ""
                for line in Path(__file__).with_name(filename).read_text().splitlines(True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        conn.execute(statement)
                        statement = ""
                conn.execute("INSERT INTO research_migrations VALUES (?,?)", (version, now()))

    @staticmethod
    def _one(conn, table, object_id):
        # table은 이 모듈에 적힌 상수만 전달한다. 사용자 입력은 바인딩한다.
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (object_id,)).fetchone()
        if row is None:
            raise NotFound("요청한 저장 기록이 없습니다.")
        return row

    def _context(self, conn, run_id):
        run = self._one(conn, "research_runs", run_id)
        basis = self._one(conn, "research_bases", run["basis_id"])
        self.require_visible(conn,basis['research_id'])
        return run, basis

    @staticmethod
    def is_archived(conn,research_id):
        row=conn.execute("SELECT kind FROM research_events WHERE research_id=? AND kind IN ('research_archived','research_restored') ORDER BY sequence DESC LIMIT 1",(research_id,)).fetchone()
        return bool(row and row['kind']=='research_archived')

    @classmethod
    def require_visible(cls,conn,research_id):
        if cls.is_archived(conn,research_id):raise NotFound('보관함에 있는 조사입니다. 복원 후 열람해주세요.')

    @staticmethod
    def _event(conn, research_id, basis_id, run_id, kind, payload):
        sequence = conn.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM research_events WHERE research_id=?",
            (research_id,),
        ).fetchone()[0]
        conn.execute("INSERT INTO research_events VALUES (?,?,?,?,?,?,?)",
                     (research_id, sequence, basis_id, run_id, kind, safe_payload(payload), now()))

    def create_research(self, value: ResearchInput, *, initial_trigger=None):
        data = value.model_dump(mode="json")
        request_hash = digest(safe_payload(data if initial_trigger is None else {"input":data,"initial_trigger":initial_trigger}).encode())
        with self.connection(write=True) as conn:
            previous = conn.execute("SELECT * FROM research_researches WHERE request_key=?",
                                    (value.request_key,)).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise Conflict("같은 요청 키로 다른 내용을 전송했습니다.")
                return self._research(conn, previous["id"])
            research_id, basis_id, run_id = (uuid4().hex for _ in range(3))
            conn.execute("INSERT INTO research_researches VALUES (?,?,?,?,?)",
                         (research_id, value.request_key, request_hash, encode(data["company"]), now()))
            basis = BasisInput.model_validate({k: data[k] for k in BasisInput.model_fields})
            conn.execute("INSERT INTO research_bases VALUES (?,?,?,?,?)",
                         (basis_id, research_id, 1, encode(basis.model_dump(mode="json")), now()))
            self._insert_run(conn, run_id, basis_id, None, initial_trigger or {"kind": "initial"}, value.budget)
            self._event(conn, research_id, basis_id, run_id, "research_created",
                        {"stage": "cached_case_queued" if initial_trigger else "storage_only", "automatic_execution_available":bool(initial_trigger)})
            return self._research(conn, research_id)

    @staticmethod
    def _insert_run(conn, run_id, basis_id, previous_id, trigger, budget):
        conn.execute(
            "INSERT INTO research_runs VALUES (?,?,?,?,?,?,?,?,?)",
            (run_id, basis_id, previous_id, "queued", safe_payload(trigger),
             encode(budget.model_dump()), encode(Usage().model_dump()), 0, now()),
        )

    def create_basis(self, research_id, value: BasisInput, expected_version: int):
        data = safe_payload(value.model_dump(mode="json"))
        with self.connection(write=True) as conn:
            self._one(conn, "research_researches", research_id)
            self.require_visible(conn,research_id)
            version = conn.execute("SELECT MAX(version) FROM research_bases WHERE research_id=?",
                                   (research_id,)).fetchone()[0]
            if version != expected_version:
                raise Conflict("조사 기준이 갱신됐습니다. 최신 버전을 다시 읽어주세요.")
            basis_id = uuid4().hex
            conn.execute("INSERT INTO research_bases VALUES (?,?,?,?,?)",
                         (basis_id, research_id, version + 1, data, now()))
            self._event(conn, research_id, basis_id, None, "basis_created", {"version": version + 1})
            return row_data(self._one(conn, "research_bases", basis_id))

    def create_run(self, basis_id, *, previous_run_id=None, trigger=None, budget=None):
        """후속 실행 저장용 내부 계약. 실제 대기열 실행/입력 병합은 6~7단계다."""
        with self.connection(write=True) as conn:
            basis = self._one(conn, "research_bases", basis_id)
            self.require_visible(conn,basis['research_id'])
            if previous_run_id:
                _, old_basis = self._context(conn, previous_run_id)
                if old_basis["research_id"] != basis["research_id"]:
                    raise Conflict("다른 기업 조사의 실행을 연결할 수 없습니다.")
            run_id = uuid4().hex
            self._insert_run(conn, run_id, basis_id, previous_run_id,
                             trigger or {"kind": "followup"}, budget or Budget())
            self._event(conn, basis["research_id"], basis_id, run_id, "run_stored", {})
            return row_data(self._one(conn, "research_runs", run_id))

    def _research(self, conn, research_id):
        self.require_visible(conn,research_id)
        research = row_data(self._one(conn, "research_researches", research_id))
        research["bases"] = [row_data(r) for r in conn.execute(
            "SELECT * FROM research_bases WHERE research_id=? ORDER BY version", (research_id,))]
        research["runs"] = [row_data(r) for r in conn.execute(
            "SELECT r.* FROM research_runs r JOIN research_bases b ON r.basis_id=b.id "
            "WHERE b.research_id=? ORDER BY r.created_at,r.id", (research_id,))]
        research["reports"] = [row_data(r) for r in conn.execute(
            "SELECT * FROM research_reports WHERE research_id=? ORDER BY version", (research_id,))]
        for report in research['reports']:
            self._publication_status(conn,report)
        research["cumulative_usage"] = {
            key: sum(run["usage"][key] for run in research["runs"]) for key in Usage.model_fields
        }
        research["execution_available"] = False
        from .finance.research_lifecycle import paused
        research['paused']=paused(conn,research_id)
        return research

    def get_research(self, research_id):
        with self.connection() as conn:
            return self._research(conn, research_id)

    def list_researches(self):
        with self.connection() as conn:
            return [row_data(r) for r in conn.execute(
                "SELECT * FROM research_researches ORDER BY created_at,id") if not self.is_archived(conn,r['id'])]

    def get_run(self, run_id):
        with self.connection() as conn:
            self._context(conn,run_id)
            run = row_data(self._one(conn, "research_runs", run_id))
            run["snapshots"] = [row_data(r) for r in conn.execute(
                "SELECT * FROM research_snapshots WHERE run_id=? ORDER BY revision", (run_id,))]
            return run

    def save_snapshot(self, run_id, *, expected_revision, stage, state, usage: Usage):
        if not stage:
            raise ValueError("단계가 필요합니다.")
        state_json = safe_payload(state)
        with self.connection(write=True) as conn:
            run, basis = self._context(conn, run_id)
            if run["revision"] != expected_revision:
                raise Conflict("더 최신 실행 상태가 저장되어 있습니다.")
            old_usage, limits = json.loads(run["usage_json"]), json.loads(run["budget_json"])
            current = usage.model_dump()
            for key, used in current.items():
                if used < old_usage[key]:
                    raise Conflict("서버 재시작이나 저장으로 사용량을 줄일 수 없습니다.")
                if key in limits and used > limits[key]:
                    raise Conflict("저장된 실행 한도를 초과할 수 없습니다.")
            snapshot_id, revision = uuid4().hex, expected_revision + 1
            conn.execute("INSERT INTO research_snapshots VALUES (?,?,?,?,?,?,?)",
                         (snapshot_id, run_id, revision, stage, state_json, encode(current), now()))
            conn.execute("UPDATE research_runs SET revision=?,usage_json=? WHERE id=?",
                         (revision, encode(current), run_id))
            self._event(conn, basis["research_id"], basis["id"], run_id, "snapshot_stored",
                        {"snapshot_id": snapshot_id, "revision": revision, "stage": stage})
            return row_data(self._one(conn, "research_snapshots", snapshot_id))

    def save_source(self, run_id, value: SourceInput, content: bytes):
        """원문을 수정 없이 저장한다. 다운로드/첨부 수신 엔드포인트는 아니다."""
        metadata = value.model_dump(mode="json")
        metadata_json = safe_payload(metadata)
        if value.url:
            parts = urlsplit(value.url)
            if parts.scheme not in ("http", "https") or not parts.hostname or parts.fragment:
                raise ValueError("공개 HTTP(S) 원문 주소만 저장할 수 있습니다.")
        rights = {"dart": "public_disclosure", "official_ir": "public_disclosure", "official_standard": "public_reference",
                  "user": "user_authorized", "synthetic": "synthetic"}
        if value.rights_status != rights[value.kind]:
            raise ValueError("출처와 이용 구분이 일치하지 않습니다.")
        for key, secret in os.environ.items():
            if re.search(r"key|token|secret|password", key, re.I) and len(secret) >= 8:
                if secret.encode() in content:
                    raise ValueError("설정된 비밀값이 포함된 원문은 저장할 수 없습니다.")
        return self._write_source(run_id, metadata_json, content)

    def get_source(self, source_id):
        with self.connection() as conn:
            source=self._one(conn, "research_sources", source_id);self._context(conn,source['run_id'])
            return row_data(source)

    def source_content(self, source_id):
        with self.connection() as conn:
            source = self._one(conn, "research_sources", source_id)
            self._context(conn,source['run_id'])
            path = (self.source_root / source["storage_key"]).resolve()
            if not path.is_relative_to(self.source_root):
                raise IntegrityFailure("원문 저장 경로가 유효하지 않습니다.")
            try:
                content = path.read_bytes()
            except FileNotFoundError:
                raise IntegrityFailure("저장된 원문 파일을 찾을 수 없습니다.") from None
            if len(content) != source["byte_size"] or digest(content) != source["content_hash"]:
                raise IntegrityFailure("원문 해시가 다릅니다. 변경된 파일을 근거로 반환하지 않습니다.")
            return content

    def save_fragment(self, source_id, value: FragmentInput):
        data = safe_payload(value.model_dump(mode="json"))
        with self.connection(write=True) as conn:
            source = self._one(conn, "research_sources", source_id)
            _, basis = self._context(conn, source["run_id"])
            fragment_id = uuid4().hex
            conn.execute("INSERT INTO research_fragments VALUES (?,?,?,?,?)",
                         (fragment_id, source_id, data, digest(value.text.encode()), now()))
            self._event(conn, basis["research_id"], basis["id"], source["run_id"],
                        "fragment_stored", {"fragment_id": fragment_id, "source_id": source_id})
            return row_data(self._one(conn, "research_fragments", fragment_id))

    def save_fact(self, run_id, fragment_id, value: FactInput):
        if value.exact_value is None and not value.missing_reason:
            raise ValueError("결측값에는 이유가 필요합니다. 실제 0과 구분합니다.")
        if value.exact_value is not None and value.raw_value is None:
            raise ValueError("수치에는 원문 문자열이 필요합니다.")
        data = safe_payload(value.model_dump(mode="json"))
        with self.connection(write=True) as conn:
            _, basis = self._context(conn, run_id)
            fragment = self._one(conn, "research_fragments", fragment_id)
            source = self._one(conn, "research_sources", fragment["source_id"])
            _, source_basis = self._context(conn, source["run_id"])
            if basis["id"] != source_basis["id"]:
                raise Conflict("다른 조사 기준의 근거는 적합성 확인 없이 연결할 수 없습니다.")
            fact_id = uuid4().hex
            conn.execute("INSERT INTO research_facts VALUES (?,?,?,?,?,?)",
                         (fact_id, run_id, fragment_id, data, "candidate", now()))
            self._event(conn, basis["research_id"], basis["id"], run_id, "fact_stored",
                        {"fact_id": fact_id, "status": "candidate"})
        return self.get_evidence(fact_id)

    def get_evidence(self, fact_id):
        with self.connection() as conn:
            fact = row_data(self._one(conn, "research_facts", fact_id))
            fact["fragment"] = row_data(self._one(conn, "research_fragments", fact["fragment_id"]))
            fact["source"] = row_data(self._one(conn, "research_sources", fact["fragment"]["source_id"]))
            run, basis = self._context(conn, fact["run_id"])
            fact["basis"] = row_data(basis)
            fact["company"] = json.loads(self._one(conn, "research_researches", basis["research_id"])["company_json"])
            return fact

    def save_report(self, run_id, value: ReportInput):
        """검증 전 보고서 후보의 불변 버전. 실제 금융 검증/게시 기능은 제공하지 않는다."""
        content = value.model_dump(mode="json")
        if value.confirmed_facts:
            raise ValueError("1단계에서는 금융 검증 전 내용을 확인된 사실로 저장할 수 없습니다.")
        data = safe_payload(content)
        request_hash = digest(encode({"run_id": run_id, "content": content}).encode())
        with self.connection(write=True) as conn:
            _, basis = self._context(conn, run_id)
            research_id = basis["research_id"]
            previous = conn.execute("SELECT * FROM research_reports WHERE research_id=? AND request_key=?",
                                    (research_id, value.request_key)).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    raise Conflict("같은 보고서 요청 키로 다른 내용을 저장했습니다.")
                return row_data(previous)
            for fact_id in set(value.evidence_ids):
                fact = self._one(conn, "research_facts", fact_id)
                _, fact_basis = self._context(conn, fact["run_id"])
                if fact_basis["id"] != basis["id"]:
                    raise Conflict("보고서와 근거의 조사 기준이 다릅니다.")
            version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM research_reports WHERE research_id=?",
                                   (research_id,)).fetchone()[0]
            report_id = uuid4().hex
            conn.execute("INSERT INTO research_reports VALUES (?,?,?,?,?,?,?,?,?)",
                         (report_id, research_id, run_id, version, value.request_key,
                          request_hash, data, "building", now()))
            conn.executemany("INSERT INTO research_report_evidence VALUES (?,?)",
                             [(report_id, fact_id) for fact_id in set(value.evidence_ids)])
            self._event(conn, research_id, basis["id"], run_id, "report_candidate_stored",
                        {"report_id": report_id, "version": version, "status": "building"})
            return row_data(self._one(conn, "research_reports", report_id))

    def get_report(self, report_id):
        with self.connection() as conn:
            report = row_data(self._one(conn, "research_reports", report_id))
            self.require_visible(conn,report['research_id'])
            report["evidence_ids"] = [r[0] for r in conn.execute(
                "SELECT fact_id FROM research_report_evidence WHERE report_id=? ORDER BY fact_id", (report_id,))]
            self._publication_status(conn,report)
            return report

    @staticmethod
    def _publication_status(conn,report):
        publication=conn.execute('SELECT * FROM research_report_publications WHERE report_id=?',(report['id'],)).fetchone()
        if publication:
            publication=row_data(publication)
            if publication['content_hash']!=digest(safe_payload(report['content']).encode()):
                raise IntegrityFailure('게시 보고서 내용이 저장된 검증 해시와 다릅니다.')
            report['stored_status']=report['status'];report['status']='published';report['publication']=publication

    def events(self, research_id, after=0):
        with self.connection() as conn:
            self.require_visible(conn,research_id)
            self._one(conn, "research_researches", research_id)
            return [row_data(r) for r in conn.execute(
                "SELECT * FROM research_events WHERE research_id=? AND sequence>? ORDER BY sequence",
                (research_id, after))]
