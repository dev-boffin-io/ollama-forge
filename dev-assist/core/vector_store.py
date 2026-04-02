"""
Vector Store — SQLite-based embedding storage.
No external database needed. Works fully offline.

Uses simple TF-IDF style cosine similarity when
sentence-transformers is not available (zero-dependency fallback).
"""

import sqlite3
import json
import math
import os
import re
from pathlib import Path

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "index.db")

def _get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath  TEXT NOT NULL,
            chunk_idx INTEGER NOT NULL,
            content   TEXT NOT NULL,
            tokens    TEXT NOT NULL,
            embedding TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_filepath ON chunks(filepath)")
    conn.commit()
    return conn

def save_chunks(filepath: str, chunks: list[dict]):
    """Save file chunks to vector store."""
    conn = _get_db()
    conn.execute("DELETE FROM chunks WHERE filepath = ?", (filepath,))
    for i, chunk in enumerate(chunks):
        tokens = json.dumps(_tokenize(chunk["content"]))
        conn.execute(
            "INSERT INTO chunks (filepath, chunk_idx, content, tokens) VALUES (?,?,?,?)",
            (filepath, i, chunk["content"], tokens)
        )
    conn.commit()
    conn.close()

def search(query: str, top_k: int = 5) -> list[dict]:
    """Find most relevant chunks for a query using cosine similarity."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT filepath, chunk_idx, content, tokens FROM chunks"
    ).fetchall()
    conn.close()

    if not rows:
        return []

    query_tokens = _tokenize(query)
    scored = []
    for filepath, chunk_idx, content, tokens_json in rows:
        chunk_tokens = json.loads(tokens_json)
        score = _cosine(query_tokens, chunk_tokens)
        if score > 0:
            scored.append({
                "filepath": filepath,
                "chunk_idx": chunk_idx,
                "content": content,
                "score": score
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]

def get_stats() -> dict:
    """Return index statistics."""
    conn = _get_db()
    total_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    total_files = conn.execute(
        "SELECT COUNT(DISTINCT filepath) FROM chunks"
    ).fetchone()[0]
    files = conn.execute(
        "SELECT DISTINCT filepath FROM chunks ORDER BY filepath"
    ).fetchall()
    conn.close()
    return {
        "total_chunks": total_chunks,
        "total_files": total_files,
        "files": [f[0] for f in files]
    }

def clear_index():
    """Wipe the entire index."""
    conn = _get_db()
    conn.execute("DELETE FROM chunks")
    conn.commit()
    conn.close()

def remove_file(filepath: str):
    """Remove a single file from the index."""
    conn = _get_db()
    conn.execute("DELETE FROM chunks WHERE filepath = ?", (filepath,))
    conn.commit()
    conn.close()

# ── Similarity helpers ─────────────────────────────────────────────────────────

def _tokenize(text: str) -> dict[str, float]:
    """Convert text to TF dict (term → frequency)."""
    text = text.lower()
    words = re.findall(r"[a-z_][a-z0-9_]{1,}", text)
    if not words:
        return {}
    tf: dict[str, float] = {}
    for w in words:
        tf[w] = tf.get(w, 0) + 1
    total = sum(tf.values())
    return {k: v / total for k, v in tf.items()}

def _cosine(a: dict, b: dict) -> float:
    """Cosine similarity between two TF dicts."""
    common = set(a) & set(b)
    if not common:
        return 0.0
    dot = sum(a[k] * b[k] for k in common)
    mag_a = math.sqrt(sum(v * v for v in a.values()))
    mag_b = math.sqrt(sum(v * v for v in b.values()))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)
