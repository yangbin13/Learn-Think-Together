from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


MAX_QUIZ_DRAFT_VERSION = 2_147_483_647


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def loads(value, default=None):
    if value is None:
        return default
    return json.loads(value)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
 display_name TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_sessions (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS courses (
 id TEXT PRIMARY KEY, user_id TEXT REFERENCES users(id) ON DELETE CASCADE,
 title TEXT NOT NULL, description TEXT NOT NULL, code TEXT NOT NULL,
 color TEXT NOT NULL, icon TEXT NOT NULL, assistant_name TEXT NOT NULL,
 is_builtin INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
 id TEXT PRIMARY KEY, course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
 user_id TEXT REFERENCES users(id) ON DELETE CASCADE, title TEXT NOT NULL, filename TEXT NOT NULL,
 path TEXT NOT NULL, status TEXT NOT NULL, page_count INTEGER NOT NULL DEFAULT 0,
 chunk_count INTEGER NOT NULL DEFAULT 0, error TEXT, source_type TEXT NOT NULL,
 license TEXT NOT NULL, source_url TEXT, created_at TEXT NOT NULL, seed_hash TEXT,
 embedding_status TEXT NOT NULL DEFAULT 'lexical',
 progress TEXT NOT NULL DEFAULT '{}', quality TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS chunks (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
 user_id TEXT REFERENCES users(id) ON DELETE CASCADE, page INTEGER NOT NULL,
 section TEXT NOT NULL, text TEXT NOT NULL, embedding TEXT
);
CREATE TABLE IF NOT EXISTS ingestion_jobs (
 id TEXT PRIMARY KEY, document_id TEXT UNIQUE NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, error TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document_figures (
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
 physical_page INTEGER NOT NULL CHECK (physical_page > 0), figure_number TEXT NOT NULL,
 caption TEXT NOT NULL, caption_text TEXT NOT NULL, section_path TEXT NOT NULL DEFAULT '',
 index_version TEXT NOT NULL, source_kind TEXT NOT NULL DEFAULT 'auto',
 user_id TEXT REFERENCES users(id) ON DELETE CASCADE, raw_figure_number TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS figure_index_jobs (
 document_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
 status TEXT NOT NULL, index_version TEXT NOT NULL, current_page INTEGER NOT NULL DEFAULT 0,
 total_pages INTEGER NOT NULL DEFAULT 0, figure_count INTEGER NOT NULL DEFAULT 0,
 error TEXT, attempts INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS concepts (
 id TEXT NOT NULL, course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
 label TEXT NOT NULL, chapter TEXT NOT NULL, source TEXT NOT NULL,
 PRIMARY KEY (course_id,id)
);
CREATE TABLE IF NOT EXISTS edges (
 course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
 source TEXT NOT NULL, target TEXT NOT NULL, relation TEXT NOT NULL,
 PRIMARY KEY (course_id,source,target,relation)
);
CREATE TABLE IF NOT EXISTS question_bank (
 id TEXT NOT NULL, course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
 data TEXT NOT NULL, PRIMARY KEY (course_id,id)
);
CREATE TABLE IF NOT EXISTS tutor_sessions (
 id TEXT PRIMARY KEY, course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES tutor_sessions(id) ON DELETE CASCADE,
 role TEXT NOT NULL, content TEXT NOT NULL, citations TEXT NOT NULL DEFAULT '[]',
 trace TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, model TEXT
);
CREATE TABLE IF NOT EXISTS quizzes (
 id TEXT PRIMARY KEY, course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, title TEXT NOT NULL,
 questions TEXT NOT NULL, created_at TEXT NOT NULL, source TEXT NOT NULL,
 submitted INTEGER NOT NULL DEFAULT 0, result TEXT,
 draft_answers TEXT NOT NULL DEFAULT '{}', draft_version INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mistakes (
 id TEXT PRIMARY KEY, quiz_id TEXT NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
 course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 question_id TEXT NOT NULL, concept TEXT NOT NULL, stem TEXT NOT NULL,
 user_answer TEXT NOT NULL, correct_answer TEXT NOT NULL, explanation TEXT NOT NULL,
 created_at TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0,
 UNIQUE (quiz_id,question_id)
);
CREATE TABLE IF NOT EXISTS mastery (
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
 concept TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, correct INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY (user_id,course_id,concept)
);
CREATE TABLE IF NOT EXISTS mindmap_annotations (
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
 node_id TEXT NOT NULL, display_label TEXT NOT NULL DEFAULT '',
 study_note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL,
 PRIMARY KEY (user_id,course_id,node_id)
);
CREATE TABLE IF NOT EXISTS plans (
 id TEXT PRIMARY KEY, course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, data TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity (
 id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 course_id TEXT NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
 type TEXT NOT NULL, label TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_course ON chunks(course_id,user_id);
CREATE INDEX IF NOT EXISTS documents_course ON documents(course_id,user_id);
CREATE INDEX IF NOT EXISTS figures_document_page ON document_figures(document_id,physical_page);
CREATE INDEX IF NOT EXISTS tutor_sessions_user ON tutor_sessions(user_id,course_id);
CREATE INDEX IF NOT EXISTS activity_user ON activity(user_id,created_at);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            # Additive migration preserves existing uploads, sessions, and student progress.
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
            for name in ("progress", "quality"):
                if name not in columns:
                    conn.execute(f"ALTER TABLE documents ADD COLUMN {name} TEXT NOT NULL DEFAULT '{{}}'")
            quiz_columns = {row["name"] for row in conn.execute("PRAGMA table_info(quizzes)")}
            if "draft_answers" not in quiz_columns:
                conn.execute("ALTER TABLE quizzes ADD COLUMN draft_answers TEXT NOT NULL DEFAULT '{}'")
            if "draft_version" not in quiz_columns:
                conn.execute("ALTER TABLE quizzes ADD COLUMN draft_version INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(str(self.path), timeout=20, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=20000")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def all(self, query, params=()):
        with self.connection() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def one(self, query, params=()):
        with self.connection() as conn:
            row = conn.execute(query, params).fetchone()
            return dict(row) if row else None

    def execute(self, query, params=()):
        with self.connection() as conn:
            return conn.execute(query, params).rowcount

    def activity(self, user_id, course_id, kind, label, conn=None):
        values = (uid(), user_id, course_id, kind, label, now())
        sql = "INSERT INTO activity VALUES (?,?,?,?,?,?)"
        if conn:
            conn.execute(sql, values)
        else:
            self.execute(sql, values)

