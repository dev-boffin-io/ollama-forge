#!/usr/bin/env python3
"""
SQLite database — conversations, messages, crews.
Falls back gracefully; PostgresDB can be swapped in via DB_CLASS.
"""
import json
import os
import sqlite3
import threading


class SQLiteDB:
    def __init__(self):
        db_dir = os.path.join(os.path.expanduser("~"), ".ollama_gui")
        os.makedirs(db_dir, exist_ok=True)
        db_path = os.path.join(db_dir, "chat.db")
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.lock = threading.Lock()
        self._create_tables()

    # ------------------------------------------------------------------ #
    def _create_tables(self):
        with self.lock:
            c = self.conn.cursor()
            c.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id      INTEGER PRIMARY KEY AUTOINCREMENT,
                    title   TEXT,
                    pinned  INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id  INTEGER,
                    role             TEXT,
                    content          TEXT
                );
                CREATE TABLE IF NOT EXISTS crews (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    name       TEXT UNIQUE,
                    config     TEXT,
                    is_default INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS memories (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    key        TEXT NOT NULL,
                    value      TEXT NOT NULL,
                    updated_at TEXT DEFAULT (datetime('now')),
                    conv_id    INTEGER
                );
                CREATE TABLE IF NOT EXISTS notes (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    title      TEXT NOT NULL DEFAULT 'Untitled',
                    content    TEXT NOT NULL DEFAULT '',
                    tags       TEXT DEFAULT '',
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                );
            """)
            self.conn.commit()

    # ------------------------------------------------------------------ #
    #  Conversations                                                       #
    # ------------------------------------------------------------------ #
    def create_conversation(self, title: str | None = None) -> int:
        with self.lock:
            c = self.conn.cursor()
            c.execute("INSERT INTO conversations (title) VALUES (?)", (title,))
            self.conn.commit()
            return c.lastrowid

    def list_conversations(self) -> list[dict]:
        with self.lock:
            c = self.conn.cursor()
            c.execute("SELECT id, title, pinned FROM conversations ORDER BY pinned DESC, id DESC")
            return [{"id": r[0], "title": r[1], "pinned": bool(r[2])} for r in c.fetchall()]

    def rename_conversation(self, cid: int, title: str):
        with self.lock:
            c = self.conn.cursor()
            c.execute("UPDATE conversations SET title=? WHERE id=?", (title, cid))
            self.conn.commit()

    def toggle_pin(self, cid: int):
        with self.lock:
            c = self.conn.cursor()
            c.execute("UPDATE conversations SET pinned = NOT pinned WHERE id=?", (cid,))
            self.conn.commit()

    def delete_conversation(self, cid: int):
        with self.lock:
            c = self.conn.cursor()
            c.execute("DELETE FROM messages      WHERE conversation_id=?", (cid,))
            c.execute("DELETE FROM conversations WHERE id=?", (cid,))
            self.conn.commit()

    # ------------------------------------------------------------------ #
    #  Messages                                                            #
    # ------------------------------------------------------------------ #
    def add_message(self, cid: int, role: str, content: str):
        with self.lock:
            c = self.conn.cursor()
            c.execute(
                "INSERT INTO messages (conversation_id, role, content) VALUES (?,?,?)",
                (cid, role, content),
            )
            self.conn.commit()

    def get_messages(self, cid: int) -> list[dict]:
        with self.lock:
            c = self.conn.cursor()
            c.execute(
                "SELECT role, content FROM messages WHERE conversation_id=? ORDER BY id",
                (cid,),
            )
            return [{"role": r[0], "content": r[1]} for r in c.fetchall()]

    # ------------------------------------------------------------------ #
    #  Crews                                                               #
    # ------------------------------------------------------------------ #
    def create_crew(self, name: str, config: list) -> int:
        with self.lock:
            c = self.conn.cursor()
            c.execute("INSERT INTO crews (name, config) VALUES (?,?)",
                      (name, json.dumps(config)))
            self.conn.commit()
            return c.lastrowid

    def list_crews(self) -> list[dict]:
        with self.lock:
            c = self.conn.cursor()
            c.execute("SELECT id, name, config, is_default FROM crews")
            return [
                {"id": r[0], "name": r[1], "config": r[2], "is_default": bool(r[3])}
                for r in c.fetchall()
            ]

    def get_crew(self, crew_id: int) -> dict | None:
        with self.lock:
            c = self.conn.cursor()
            c.execute("SELECT name, config FROM crews WHERE id=?", (crew_id,))
            row = c.fetchone()
            return {"name": row[0], "config": row[1]} if row else None

    def update_crew(self, crew_id: int, name: str, config: list):
        with self.lock:
            c = self.conn.cursor()
            c.execute("UPDATE crews SET name=?, config=? WHERE id=?",
                      (name, json.dumps(config), crew_id))
            self.conn.commit()

    def update_crew_name(self, crew_id: int, name: str):
        with self.lock:
            c = self.conn.cursor()
            c.execute("UPDATE crews SET name=? WHERE id=?", (name, crew_id))
            self.conn.commit()

    def delete_crew(self, crew_id: int):
        with self.lock:
            c = self.conn.cursor()
            c.execute("DELETE FROM crews WHERE id=?", (crew_id,))
            self.conn.commit()

    def set_default_crew(self, crew_id: int):
        with self.lock:
            c = self.conn.cursor()
            c.execute("UPDATE crews SET is_default=0")
            c.execute("UPDATE crews SET is_default=1 WHERE id=?", (crew_id,))
            self.conn.commit()

    def get_default_crew_config(self) -> list | None:
        with self.lock:
            c = self.conn.cursor()
            c.execute("SELECT config FROM crews WHERE is_default=1 LIMIT 1")
            row = c.fetchone()
            return json.loads(row[0]) if row else None

    # ------------------------------------------------------------------ #
    #  Memories (Persistent Long-term)                                     #
    # ------------------------------------------------------------------ #
    def save_memory(self, key: str, value: str, conv_id: int | None = None):
        """Upsert a memory fact by key."""
        with self.lock:
            c = self.conn.cursor()
            c.execute("SELECT id FROM memories WHERE key=?", (key,))
            row = c.fetchone()
            if row:
                c.execute(
                    "UPDATE memories SET value=?, updated_at=datetime('now'), conv_id=? WHERE key=?",
                    (value, conv_id, key),
                )
            else:
                c.execute(
                    "INSERT INTO memories (key, value, conv_id) VALUES (?,?,?)",
                    (key, value, conv_id),
                )
            self.conn.commit()

    def get_all_memories(self) -> list[dict]:
        with self.lock:
            c = self.conn.cursor()
            c.execute("SELECT id, key, value, updated_at FROM memories ORDER BY updated_at DESC")
            return [{"id": r[0], "key": r[1], "value": r[2], "updated_at": r[3]} for r in c.fetchall()]

    def delete_memory(self, mem_id: int):
        with self.lock:
            c = self.conn.cursor()
            c.execute("DELETE FROM memories WHERE id=?", (mem_id,))
            self.conn.commit()

    def clear_memories(self):
        with self.lock:
            c = self.conn.cursor()
            c.execute("DELETE FROM memories")
            self.conn.commit()

    # ------------------------------------------------------------------ #
    #  Notes                                                               #
    # ------------------------------------------------------------------ #
    def create_note(self, title: str = "Untitled", content: str = "", tags: str = "") -> int:
        with self.lock:
            c = self.conn.cursor()
            c.execute(
                "INSERT INTO notes (title, content, tags) VALUES (?,?,?)",
                (title, content, tags),
            )
            self.conn.commit()
            return c.lastrowid

    def list_notes(self) -> list[dict]:
        with self.lock:
            c = self.conn.cursor()
            c.execute("SELECT id, title, tags, updated_at FROM notes ORDER BY updated_at DESC")
            return [{"id": r[0], "title": r[1], "tags": r[2], "updated_at": r[3]} for r in c.fetchall()]

    def get_note(self, note_id: int) -> dict | None:
        with self.lock:
            c = self.conn.cursor()
            c.execute("SELECT id, title, content, tags, updated_at FROM notes WHERE id=?", (note_id,))
            row = c.fetchone()
            return {"id": row[0], "title": row[1], "content": row[2], "tags": row[3], "updated_at": row[4]} if row else None

    def update_note(self, note_id: int, title: str, content: str, tags: str = ""):
        with self.lock:
            c = self.conn.cursor()
            c.execute(
                "UPDATE notes SET title=?, content=?, tags=?, updated_at=datetime('now') WHERE id=?",
                (title, content, tags, note_id),
            )
            self.conn.commit()

    def delete_note(self, note_id: int):
        with self.lock:
            c = self.conn.cursor()
            c.execute("DELETE FROM notes WHERE id=?", (note_id,))
            self.conn.commit()

    def search_notes(self, query: str) -> list[dict]:
        with self.lock:
            c = self.conn.cursor()
            q = f"%{query}%"
            c.execute(
                "SELECT id, title, content, tags, updated_at FROM notes "
                "WHERE title LIKE ? OR content LIKE ? OR tags LIKE ? ORDER BY updated_at DESC",
                (q, q, q),
            )
            return [{"id": r[0], "title": r[1], "content": r[2], "tags": r[3], "updated_at": r[4]} for r in c.fetchall()]


# Allow PostgresDB swap-in
try:
    from database.postgres import PostgresDB as DB_CLASS  # type: ignore
except Exception:
    DB_CLASS = SQLiteDB
