"""기업금융 조사 저장소의 기본 경로."""
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "session.db"
