"""
Persistent session store — SQLite backed.

Port of opencode's session persistence. opencode stores every session as JSON
info plus per-message / per-part files under `storage/session/<project>/`,
and a *session* is a titled, timestamped conversation with an ordered message
list. We keep the same session model but back it with one SQLite database
(`$DEV_ASSIST_DATA_DIR/sessions.db`, default `~/.config/dev-assist/sessions.db`)
so writes are atomic and list/resume queries are cheap.

Session model:
  - `id`       — uuid hex; stable handle for `/resume`, `/rename`, `/delete`.
  - `title`    — auto-set from the first user message (mirrors opencode),
                overridable via `/rename`.
  - `project`  — directory the session was started in (display grouping).
  - `created` / `updated` — epoch seconds; `updated` bumps on every message.

The module keeps one *active* session in-process; chat turns recorded through
`core.session` are written through to the active session id (see
`SessionContext.store_id`).

MIT License — Copyright (c) 2025 dev-assist contributors.
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

_MAX_TITLE_CHARS = 60


# ── Public data model ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Session:
    """Lightweight handle to a stored conversation."""

    id: str
    title: str
    project: str
    created: int
    updated: int

    @property
    def is_empty(self) -> bool:
        return not self.title


@dataclass(frozen=True)
class StoredMessage:
    """One persisted chat turn inside a session."""

    id: int
    session_id: str
    role: str
    content: str
    created: int


# ── Location / connection ────────────────────────────────────────────────────

def get_data_dir() -> str:
    """Data directory for the database (env override honours tests / frozen)."""
    return os.environ.get("DEV_ASSIST_DATA_DIR") or str(
        Path.home() / ".config" / "dev-assist"
    )


def db_path() -> str:
    return os.path.join(get_data_dir(), "sessions.db")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id      TEXT PRIMARY KEY,
            title   TEXT NOT NULL DEFAULT '',
            project TEXT NOT NULL DEFAULT '',
            created INTEGER NOT NULL,
            updated INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT    NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role       TEXT    NOT NULL,
            content    TEXT    NOT NULL,
            created    INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session
            ON messages(session_id, id);
        CREATE INDEX IF NOT EXISTS idx_sessions_updated
            ON sessions(updated DESC);
        """)


@contextmanager
def _connect():
    data_dir = get_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    conn = sqlite3.connect(db_path(), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA foreign_keys = ON")  # enforce cascade deletes
    try:
        _ensure_schema(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row_to_session(row: sqlite3.Row) -> Session:
    return Session(
        id=row["id"],
        title=row["title"],
        project=row["project"],
        created=row["created"],
        updated=row["updated"],
    )


def _row_to_message(row: sqlite3.Row) -> StoredMessage:
    return StoredMessage(
        id=row["id"],
        session_id=row["session_id"],
        role=row["role"],
        content=row["content"],
        created=row["created"],
    )


# ── CRUD ─────────────────────────────────────────────────────────────────────

def create_session(project: str = "") -> Session:
    """Create and persist a new, empty session."""
    sid = uuid.uuid4().hex
    now = int(time.time())
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions (id, title, project, created, updated) VALUES (?,?,?,?,?)",
            (sid, "", project, now, now),
        )
    return Session(id=sid, title="", project=project, created=now, updated=now)


def get_session_info(session_id: str) -> Session | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return _row_to_session(row) if row else None


def list_sessions(limit: int = 50, project: str | None = None) -> list[Session]:
    """
    Most recently updated first. `project` filters to one directory when given.
    """
    sql = "SELECT * FROM sessions"
    params: tuple = ()
    if project:
        sql += " WHERE project = ?"
        params = (project,)
    sql += " ORDER BY updated DESC, created DESC, rowid DESC LIMIT ?"
    with _connect() as conn:
        rows = conn.execute(sql, params + (max(1, int(limit)),)).fetchall()
    return [_row_to_session(r) for r in rows]


def latest_session() -> Session | None:
    sessions = list_sessions(limit=1)
    return sessions[0] if sessions else None


def rename_session(session_id: str, title: str) -> bool:
    """Rename a session. Returns False when the session doesn't exist."""
    title = (title or "").strip()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE sessions SET title = ?, updated = ? WHERE id = ?",
            (title, int(time.time()), session_id),
        )
        return cur.rowcount > 0


def delete_session(session_id: str) -> bool:
    """Delete a session and all of its messages. Returns False when missing."""
    with _connect() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return cur.rowcount > 0


def count_sessions() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
    return int(row["n"])


def _derive_title(content: str) -> str:
    one_line = " ".join(content.split())
    return one_line[:_MAX_TITLE_CHARS]


def append_message(session_id: str, role: str, content: str) -> StoredMessage | None:
    """
    Persist one chat turn. Returns the stored message (None if the content is
    empty or the session doesn't exist — write failures never raise).
    """
    content = (content or "").strip()
    role = (role or "").strip() if isinstance(role, str) else ""
    if not content or role not in ("user", "assistant"):
        return None
    now = int(time.time())
    try:
        with _connect() as conn:
            cur = conn.execute(
                "INSERT INTO messages (session_id, role, content, created) VALUES (?,?,?,?)",
                (session_id, role, content, now),
            )
            conn.execute(
                "UPDATE sessions SET updated = ? WHERE id = ?",
                (now, session_id),
            )
            # Auto-title from the first user message (opencode behaviour).
            row = conn.execute(
                "SELECT title FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is not None and not row["title"] and role == "user":
                conn.execute(
                    "UPDATE sessions SET title = ? WHERE id = ?",
                    (_derive_title(content), session_id),
                )
        return StoredMessage(
            id=cur.lastrowid,
            session_id=session_id,
            role=role,
            content=content,
            created=now,
        )
    except sqlite3.Error:
        return None


def get_messages(session_id: str, limit: int | None = None) -> list[StoredMessage]:
    sql = "SELECT * FROM messages WHERE session_id = ? ORDER BY id ASC"
    params: tuple = (session_id,)
    if limit:
        sql += " LIMIT ?"
        params = params + (max(1, int(limit)),)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_to_message(r) for r in rows]


def message_count(session_id: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return int(row["n"])


# ── Active session tracking ──────────────────────────────────────────────────

_ACTIVE_ID: str | None = None


def current_session_id() -> str | None:
    return _ACTIVE_ID


def current_session() -> Session | None:
    if not _ACTIVE_ID:
        return None
    return get_session_info(_ACTIVE_ID)


def _load_into_context(session_id: str) -> None:
    """Hydrate core.session's singleton with a session's stored messages."""
    from core.session import get_session
    sess = get_session()
    messages = get_messages(session_id)
    sess.bind_persisted_session(
        session_id,
        [(m.role, m.content, float(m.created)) for m in messages],
    )


def new_session(project: str = "") -> Session:
    """Create a fresh session and make it the active one."""
    global _ACTIVE_ID
    session = create_session(project)
    _ACTIVE_ID = session.id
    _load_into_context(session.id)
    return session


def resume_session(session_id: str) -> Session | None:
    """Switch the active session to an existing one. Returns None if missing."""
    global _ACTIVE_ID
    session = get_session_info(session_id)
    if session is None:
        return None
    _ACTIVE_ID = session.id
    _load_into_context(session.id)
    return session


def bootstrap(project: str = "", resume: bool | str = False) -> Session:
    """
    Ensure an active session exists. Called once at REPL startup.

      resume=False     -> start a fresh session (opencode's default on launch).
      resume=True      -> resume the most recently updated session (or create one).
      resume=<id>      -> resume that specific session (falls back to latest,
                          then to a fresh session).
    """
    global _ACTIVE_ID
    if _ACTIVE_ID and get_session_info(_ACTIVE_ID):
        already = not (isinstance(resume, str) and resume and resume != _ACTIVE_ID)
        if already:
            # Already active (e.g. a later /resume); leave it in place.
            existing = get_session_info(_ACTIVE_ID)
            if existing is not None:
                _load_into_context(existing.id)
                return existing

    if isinstance(resume, str) and resume:
        target = get_session_info(resume) or latest_session()
    elif resume:
        target = latest_session()
    else:
        target = None

    if target is not None:
        _ACTIVE_ID = target.id
        _load_into_context(target.id)
        return target
    return new_session(project)


def reset_state() -> None:
    """
    Drop the in-process active-session cache. Used by tests to isolate state.
    """
    global _ACTIVE_ID
    _ACTIVE_ID = None
