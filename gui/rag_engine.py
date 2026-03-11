#!/usr/bin/env python3
"""
RAG engine — no LangChain, no ChromaDB.
Uses:  sentence-transformers  (embedding)
       faiss-cpu               (vector search)
       pypdf / python-docx     (document loading)

All heavy imports are LAZY — loaded only when first used.
Persists index + metadata to ~/.ollama_gui/rag/
"""
import hashlib
import json
import os
import pickle
import re

_PERSIST = os.path.join(os.path.expanduser("~"), ".ollama_gui", "rag")


# ------------------------------------------------------------------ #
#  Helpers                                                             #
# ------------------------------------------------------------------ #
def _ensure_dir():
    os.makedirs(_PERSIST, exist_ok=True)


def _file_hash(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _chunk_text(text: str, size: int = 500, overlap: int = 80) -> list[str]:
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        chunk = " ".join(words[i: i + size])
        if chunk.strip():
            chunks.append(chunk)
        i += size - overlap
    return chunks


# ------------------------------------------------------------------ #
#  Document loaders (lazy)                                             #
# ------------------------------------------------------------------ #
def _load_pdf(path: str) -> str:
    import pypdf
    text = []
    with open(path, "rb") as f:
        reader = pypdf.PdfReader(f)
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text.append(t)
    return "\n".join(text)


def _load_docx(path: str) -> str:
    import docx
    doc = docx.Document(path)
    return "\n".join(p.text for p in doc.paragraphs if p.text.strip())


def _load_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def load_document(path: str) -> str:
    ext = path.lower().rsplit(".", 1)[-1]
    if ext == "pdf":
        return _load_pdf(path)
    elif ext == "docx":
        return _load_docx(path)
    else:
        return _load_text(path)


# ------------------------------------------------------------------ #
#  Embedding — two backends                                            #
#  1. Ollama  (model names containing ':')  → /api/embed (>=0.3)      #
#                                           → /api/embeddings fallback #
#  2. HuggingFace sentence-transformers     → local inference          #
# ------------------------------------------------------------------ #
_st_cache: dict = {}


def _is_ollama_model(name: str) -> bool:
    """Ollama model names contain ':' (e.g. nomic-embed-text:latest)."""
    return ":" in name


def _embed_ollama(texts: list[str], model: str) -> "np.ndarray":
    """
    Call Ollama embedding API.
    Tries /api/embed (Ollama >=0.3) first — batched, faster.
    Falls back to /api/embeddings (older) one-by-one if needed.
    """
    import numpy as np
    import requests

    BASE = "http://localhost:11434"

    # ── Try new /api/embed (batch) ──────────────────────────────────
    try:
        r = requests.post(
            f"{BASE}/api/embed",
            json={"model": model, "input": texts},
            timeout=120,
        )
        if r.status_code == 200:
            data = r.json()
            # Response key is "embeddings" (list of lists)
            vecs = data.get("embeddings") or data.get("embedding")
            if vecs:
                arr = np.array(vecs, dtype="float32")
                norms = np.linalg.norm(arr, axis=1, keepdims=True)
                return arr / (norms + 1e-10)
    except Exception:
        pass

    # ── Fallback: old /api/embeddings (one text at a time) ──────────
    vecs = []
    for text in texts:
        r = requests.post(
            f"{BASE}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=60,
        )
        r.raise_for_status()
        vecs.append(r.json()["embedding"])

    arr = np.array(vecs, dtype="float32")
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    return arr / (norms + 1e-10)


def _embed_st(texts: list[str], model: str) -> "np.ndarray":
    """Use sentence-transformers (HuggingFace) locally."""
    import numpy as np
    if model not in _st_cache:
        from sentence_transformers import SentenceTransformer
        _st_cache[model] = SentenceTransformer(model)
    m = _st_cache[model]
    vecs = m.encode(texts, show_progress_bar=False, convert_to_numpy=True)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    return (vecs / (norms + 1e-10)).astype("float32")


def _embed(texts: list[str], model_name: str) -> "np.ndarray":
    if _is_ollama_model(model_name):
        return _embed_ollama(texts, model_name)
    return _embed_st(texts, model_name)


# ------------------------------------------------------------------ #
#  RAGIndex — the main class                                           #
# ------------------------------------------------------------------ #
class RAGIndex:
    META_FILE = os.path.join(_PERSIST, "meta.json")
    INDEX_FILE = os.path.join(_PERSIST, "index.faiss")

    def __init__(self, embed_model: str = "all-MiniLM-L6-v2"):
        self.embed_model = embed_model
        self._index = None       # faiss index
        self._chunks: list[dict] = []   # [{text, source, hash}]
        _ensure_dir()
        self._load()

    # ---- persistence -------------------------------------------- #
    def _load(self):
        if os.path.exists(self.META_FILE) and os.path.exists(self.INDEX_FILE):
            try:
                import faiss
                with open(self.META_FILE, encoding="utf-8") as f:
                    self._chunks = json.load(f)
                self._index = faiss.read_index(self.INDEX_FILE)
                return
            except Exception:
                pass
        self._index = None
        self._chunks = []

    def _save(self):
        import faiss
        faiss.write_index(self._index, self.INDEX_FILE)
        with open(self.META_FILE, "w", encoding="utf-8") as f:
            json.dump(self._chunks, f, ensure_ascii=False)

    @property
    def is_empty(self) -> bool:
        return self._index is None or len(self._chunks) == 0

    # ---- build index -------------------------------------------- #
    def add_documents(self, paths: list[str],
                      progress_cb=None, message_cb=None,
                      stop_cb=None) -> int:
        """
        Load, chunk, embed, add to FAISS.
        progress_cb(done, total) — called per batch
        message_cb(str)         — status messages
        stop_cb() → bool        — return True to abort gracefully
        Returns number of NEW chunks added.
        """
        import faiss, numpy as np

        def _stopped():
            return stop_cb is not None and stop_cb()

        existing_hashes = {c["hash"] for c in self._chunks}
        new_chunks: list[dict] = []

        for path in paths:
            if _stopped():
                break
            try:
                fh = _file_hash(path)
                if fh in existing_hashes:
                    if message_cb:
                        message_cb(f"⏭ Skipped (already indexed): {os.path.basename(path)}")
                    continue
                text = load_document(path)
                for chunk in _chunk_text(text):
                    new_chunks.append({
                        "text": chunk,
                        "source": os.path.basename(path),
                        "hash": fh,
                    })
                if message_cb:
                    message_cb(f"✅ Loaded: {os.path.basename(path)}")
            except Exception as e:
                if message_cb:
                    message_cb(f"❌ Failed: {os.path.basename(path)} — {e}")

        if not new_chunks:
            if progress_cb:
                progress_cb(1, 1)
            return 0

        total = len(new_chunks)
        if progress_cb:
            progress_cb(0, total)

        # batch_size=1 for Ollama — one request per chunk, stops instantly on cancel
        # sentence-transformers can handle larger batches efficiently
        is_ollama = _is_ollama_model(self.embed_model)
        batch_size = 1 if is_ollama else 32

        all_vecs    = []
        good_chunks = []
        done = 0

        for i in range(0, total, batch_size):
            if _stopped():
                break
            batch = new_chunks[i: i + batch_size]
            try:
                vecs = _embed([c["text"] for c in batch], self.embed_model)
                all_vecs.append(vecs)
                good_chunks.extend(batch)
            except Exception as e:
                srcs = {c["source"] for c in batch}
                if message_cb:
                    message_cb(
                        f"⚠️ Embed failed ({', '.join(srcs)}): "
                        f"{str(e)[:120]} — skipping"
                    )
            done += len(batch)
            if progress_cb:
                progress_cb(done, total)

        if not all_vecs:
            if message_cb:
                message_cb("❌ No chunks could be embedded — check embed model.")
            return 0

        vecs_np = np.vstack(all_vecs).astype("float32")
        dim = vecs_np.shape[1]

        if self._index is None:
            self._index = faiss.IndexFlatIP(dim)

        self._index.add(vecs_np)
        self._chunks.extend(good_chunks)
        self._save()
        return len(good_chunks)

    # ---- search ------------------------------------------------- #
    def search(self, query: str, k: int = 5) -> list[str]:
        if self._index is None or not self._chunks:
            return []
        import numpy as np
        qvec = _embed([query], self.embed_model).astype("float32")
        k = min(k, len(self._chunks))
        _, idxs = self._index.search(qvec, k)
        return [self._chunks[i]["text"] for i in idxs[0] if i >= 0]

    # ---- maintenance -------------------------------------------- #
    def remove_source(self, source: str) -> int:
        """Remove all chunks from a given source filename. Returns removed count."""
        # FAISS flat index doesn't support remove — rebuild without those chunks
        import faiss, numpy as np
        before = len(self._chunks)
        keep = [c for c in self._chunks if c["source"] != source]
        removed = before - len(keep)
        if removed == 0:
            return 0
        self._chunks = keep
        if not keep:
            self._index = None
        else:
            texts = [c["text"] for c in keep]
            vecs = _embed(texts, self.embed_model).astype("float32")
            dim = vecs.shape[1]
            self._index = faiss.IndexFlatIP(dim)
            self._index.add(vecs)
        self._save()
        return removed

    def clear(self):
        import shutil
        shutil.rmtree(_PERSIST, ignore_errors=True)
        _ensure_dir()
        self._index = None
        self._chunks = []

    @property
    def is_empty(self) -> bool:
        return not self._chunks

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)
