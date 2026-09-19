"""
Vector Store — SQLite-based embedding storage.
No external database needed. Works fully offline.

Uses real semantic embeddings via Ollama's `/api/embed` endpoint
(nomic-embed-text) when the model is available, and falls back to a
simple TF-IDF cosine similarity when it isn't (zero-dependency fallback).
"""

import json
import math
import os
import re
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "index.db")

# Embedding model preference (only used if present on this machine).
EMBEDDING_MODEL = "nomic-embed-text"
_CHUNK_COMMIT_EVERY = 100  # files between commits during a bulk index run

_embedding_model_cache: dict = {"checked": False, "model": None}


def _get_db() -> sqlite3.Connection:
    """Open the (single) SQLite connection with WAL + schema in place."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _ensure_schema(conn)
    return conn


class batch_connection:
    """
    Context manager: one shared SQLite connection for a bulk index run.

    Callers pass it to `save_chunks(..., conn=conn)`; commit on exit so
    a long index run is one transaction (plus any periodic commits the
    caller makes along the way).
    """

    def __init__(self) -> None:
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self.conn = _get_db()
        return self.conn

    def __exit__(self, *exc) -> None:
        if self.conn is not None:
            try:
                self.conn.commit()
            finally:
                self.conn.close()
                self.conn = None


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Create tables/indexes if missing (safe to call on every open)."""
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
    # Older databases predate the embedding column — add it if missing.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(chunks)")}
    if "embedding" not in cols:
        conn.execute("ALTER TABLE chunks ADD COLUMN embedding TEXT")
    conn.commit()


# ── Embeddings ───────────────────────────────────────────────────────────────

def _available_embedding_model() -> str | None:
    """
    Return the name of an embedding model usable right now, or None.

    Checks that the `ollama` package is importable, that the model is
    actually pulled, and that the server responds. The result is cached
    for the process lifetime so a long multi-file index run doesn't ping
    the server once per file.
    """
    if _embedding_model_cache["checked"]:
        return _embedding_model_cache["model"]

    model: str | None = None
    try:
        import ollama
        # list() raises if the server isn't running — that's a fallback signal.
        models = ollama.list()
        names = set()
        if isinstance(models, dict):
            for m in models.get("models", []):
                names.add(m.get("name") or m.get("model") or "")
        else:  # newer ollama returns a dataclass-like ListResponse
            for m in getattr(models, "models", []):
                names.add(getattr(m, "model", "") or getattr(m, "name", ""))
        if EMBEDDING_MODEL in names:
            model = EMBEDDING_MODEL
    except Exception:
        model = None

    _embedding_model_cache["checked"] = True
    _embedding_model_cache["model"] = model
    return model


def _embed_text(text: str) -> list[float] | None:
    """Embed a single text via Ollama. Returns a vector, or None on any failure."""
    model = _available_embedding_model()
    if not model or not text.strip():
        return None
    try:
        import ollama
        resp = ollama.embeddings(model=model, prompt=text)
        embedding = resp.get("embedding") if isinstance(resp, dict) else getattr(resp, "embedding", None)
        if not embedding:
            return None
        return [float(x) for x in embedding]
    except Exception:
        return None


def embedding_status() -> tuple[bool, str | None]:
    """(active, model) — whether real embeddings are usable this session."""
    model = _available_embedding_model()
    return (model is not None, model)


# ── Indexing ─────────────────────────────────────────────────────────────────

def save_chunks(
    filepath: str,
    chunks: list[dict],
    *,
    conn: sqlite3.Connection | None = None,
) -> None:
    """
    Save file chunks to the vector store.

    When `conn` is given, the chunk rows are batched onto that connection
    and *not* committed — the caller (e.g. a long index run) controls the
    commit point. Otherwise a fresh connection is opened, committed, and
    closed (single-file convenience path).
    """
    owns_conn = conn is None
    if owns_conn:
        conn = _get_db()

    try:
        conn.execute("DELETE FROM chunks WHERE filepath = ?", (filepath,))
        rows = []
        for i, chunk in enumerate(chunks):
            tokens = json.dumps(_tokenize(chunk["content"]))
            embedding = _embed_text(chunk["content"])
            rows.append((
                filepath, i, chunk["content"], tokens,
                json.dumps(embedding) if embedding is not None else None,
            ))
        conn.executemany(
            "INSERT INTO chunks (filepath, chunk_idx, content, tokens, embedding) "
            "VALUES (?,?,?,?,?)",
            rows,
        )
        if owns_conn:
            conn.commit()
    finally:
        if owns_conn:
            conn.close()


# ── Search ───────────────────────────────────────────────────────────────────

def search(query: str, top_k: int = 5) -> list[dict]:
    """Find most relevant chunks for a query.

    Uses embedding cosine similarity when real embeddings are active and
    stored; otherwise falls back to TF-IDF cosine.
    """
    conn = _get_db()
    rows = conn.execute(
        "SELECT filepath, chunk_idx, content, tokens, embedding FROM chunks"
    ).fetchall()
    conn.close()

    if not rows:
        return []

    query_embedding = _embed_text(query)
    query_tokens = _tokenize(query)
    scored = []
    for filepath, chunk_idx, content, tokens_json, embedding_json in rows:
        if query_embedding:
            stored = json.loads(embedding_json) if embedding_json else None
            if stored:
                score = _embedding_cosine(query_embedding, stored)
                if score > 0:
                    scored.append({
                        "filepath": filepath,
                        "chunk_idx": chunk_idx,
                        "content": content,
                        "score": score,
                    })
                continue
        # TF-IDF fallback (no embedding, or a chunk without a stored vector)
        chunk_tokens = json.loads(tokens_json)
        score = _cosine(query_tokens, chunk_tokens)
        if score > 0:
            scored.append({
                "filepath": filepath,
                "chunk_idx": chunk_idx,
                "content": content,
                "score": score,
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]


# ── Stats / maintenance ──────────────────────────────────────────────────────

def get_stats() -> dict:
    """Return index statistics."""
    conn = _get_db()
    total_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    total_files = conn.execute(
        "SELECT COUNT(DISTINCT filepath) FROM chunks"
    ).fetchone()[0]
    embedded_chunks = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL"
    ).fetchone()[0]
    files = conn.execute(
        "SELECT DISTINCT filepath FROM chunks ORDER BY filepath"
    ).fetchall()
    conn.close()
    active, model = embedding_status()
    return {
        "total_chunks": total_chunks,
        "total_files": total_files,
        "files": [f[0] for f in files],
        "embedded_chunks": embedded_chunks,
        "embeddings_active": active,
        "embedding_model": model,
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


def _embedding_cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two embedding vectors."""
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = sum(a[i] * b[i] for i in range(n))
    mag_a = math.sqrt(sum(x * x for x in a[:n]))
    mag_b = math.sqrt(sum(x * x for x in b[:n]))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)