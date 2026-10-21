"""Wave 12 — 프로세스 밖(SQLite 파일)에 두는 세션 저장소.

서버가 재시작돼도(새 프로세스, 새 인스턴스) 같은 session.db 파일을 다시 열므로
대화 이력이 유지된다. in-memory dict였다면 재시작 즉시 소실됐을 상태다.
"""
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock

DB_PATH = Path(__file__).parent.parent / "session.db"


class SqliteSessionStore:
    def __init__(self, db_path: Path = DB_PATH):
        self._lock = RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=5)
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS messages ("
            " thread_id TEXT, role TEXT, content TEXT, created TEXT)"
        )
        self.conn.commit()

    def load(self, thread_id: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT role, content FROM messages WHERE thread_id=? ORDER BY rowid",
                (thread_id,),
            ).fetchall()
        return [{"role": r, "content": c} for r, c in rows]

    def append(self, thread_id: str, role: str, content: str) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO messages (thread_id,role,content,created) VALUES (?,?,?,?)",
                (thread_id, role, content, datetime.now().isoformat()),
            )

    def close(self) -> None:
        with self._lock:
            self.conn.close()
