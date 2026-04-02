"""
CLI session history — SQLite backed, per-user (system username).
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


def _get_db_path() -> str:
    data_dir = os.environ.get("DEV_ASSIST_DATA_DIR") or str(
        Path.home() / ".config" / "dev-assist"
    )
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "cli_history.db")


@contextmanager
def _db():
    conn = sqlite3.connect(_get_db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init() -> None:
    with _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS cli_history (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT    NOT NULL,
                role     TEXT    NOT NULL,
                content  TEXT    NOT NULL,
                created  INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cli_user
                ON cli_history(username, created);
        """)


_init()

_SYS_USER = os.environ.get("USER") or os.environ.get("LOGNAME") or "user"


def save(role: str, content: str, username: str = _SYS_USER) -> None:
    with _db() as conn:
        conn.execute(
            "INSERT INTO cli_history (username, role, content, created) VALUES (?,?,?,?)",
            (username, role, content.strip(), int(time.time())),
        )


def get(limit: int = 50, username: str = _SYS_USER) -> list[dict]:
    with _db() as conn:
        rows = conn.execute(
            "SELECT role, content, created FROM cli_history "
            "WHERE username = ? ORDER BY created DESC LIMIT ?",
            (username, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def clear(username: str = _SYS_USER) -> None:
    with _db() as conn:
        conn.execute("DELETE FROM cli_history WHERE username = ?", (username,))


def show(limit: int = 20, username: str = _SYS_USER) -> None:
    hist = get(limit, username)
    if not hist:
        print("No CLI history yet.")
        return
    print(f"\n📜 CLI history ({username}) — last {len(hist)} entries\n")
    for m in hist:
        ts   = time.strftime("%m-%d %H:%M", time.localtime(m["created"]))
        role = "You" if m["role"] == "user" else " AI"
        short = m["content"][:120].replace("\n", " ")
        ellipsis = "…" if len(m["content"]) > 120 else ""
        print(f"  [{ts}] {role}: {short}{ellipsis}")
    print()
