"""研伴 2.0 认知层 SQLite 存储。

数据库文件：data/yanban.db（gitignore）。WAL 模式；全部时间戳 UTC ISO。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "yanban.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  profile_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  name TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'DISCOVER',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS research_intents (
  project_id TEXT PRIMARY KEY,
  intent_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  meta_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS papers (
  id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  paper_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'recommended',
  user_feedback TEXT NOT NULL DEFAULT '',
  relevance REAL,
  difficulty REAL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  summary TEXT NOT NULL,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_papers_project ON papers(project_id);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id);
CREATE TABLE IF NOT EXISTS subscriptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL,
  query TEXT NOT NULL,
  seen_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  checked_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_subs_project ON subscriptions(project_id);
CREATE TABLE IF NOT EXISTS shares (
  token TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  revoked_at TEXT NOT NULL DEFAULT ''
);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_CONN: sqlite3.Connection | None = None


def get_conn(db_path: Path | None = None) -> sqlite3.Connection:
    """进程级单例连接（busy_timeout 防并发锁；WAL 提升读写并发）。

    测试隔离：重置 db_module._CONN = None 并设置 DB_PATH 后重新获取。
    """
    global _CONN
    if _CONN is not None:
        try:
            _CONN.execute("SELECT 1")
            return _CONN
        except sqlite3.ProgrammingError:
            _CONN = None
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(SCHEMA)
    _CONN = conn
    return conn


def write_event(conn: sqlite3.Connection, project_id: str, kind: str, summary: str,
                detail: dict | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO events (project_id, kind, summary, detail_json, created_at) VALUES (?,?,?,?,?)",
        (project_id, kind, summary, json.dumps(detail or {}, ensure_ascii=False), utcnow()),
    )
    return int(cur.lastrowid or 0)


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
