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


def _import_faiss():
    """Import faiss with a helpful error if not installed."""
    try:
        import faiss
        return faiss
    except ModuleNotFoundError:
        raise ModuleNotFoundError(
            "faiss is not installed.\n"
            "Install it with:  pip install faiss-cpu\n"
            "Then restart the application."
        )



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
    Tries /api/embed (>=0.3) first, then /api/embeddings fallback,
    then sentence-transformers if Ollama is unavailable.
    """
    import numpy as np
    import requests

    BASE = "http://localhost:11434"

    # ── Try new /api/embed (batch) ───────────────────────────────────
    try:
        r = requests.post(
            f"{BASE}/api/embed",
            json={"model": model, "input": texts},
            timeout=30,
        )
        if r.status_code == 200:
            data = r.json()
            vecs = data.get("embeddings") or data.get("embedding")
            if vecs:
                arr = np.array(vecs, dtype="float32")
                norms = np.linalg.norm(arr, axis=1, keepdims=True)
                return arr / (norms + 1e-10)
    except Exception:
        pass

    # ── Fallback: old /api/embeddings ───────────────────────────────
    try:
        vecs = []
        for text in texts:
            r = requests.post(
                f"{BASE}/api/embeddings",
                json={"model": model, "prompt": text},
                timeout=30,
            )
            r.raise_for_status()
            vecs.append(r.json()["embedding"])
        arr = np.array(vecs, dtype="float32")
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / (norms + 1e-10)
    except Exception:
        pass

    # ── Final fallback: sentence-transformers (no Ollama needed) ────
    _ST_FALLBACK = "all-MiniLM-L6-v2"
    return _embed_st(texts, _ST_FALLBACK)


def _embed_hash_tfidf(texts: list[str], n_features: int = 4096) -> "np.ndarray":
    """
    Pure-numpy hashed TF-IDF fallback.
    Fixed 4096-dim vectors via MD5 hash trick — no ML framework needed.
    Works offline, inside frozen PyInstaller binary, anywhere.
    """
    import numpy as np
    import hashlib

    def _tok(text: str) -> list[str]:
        import re
        return re.findall(r"[\w\u0980-\u09FF]+", text.lower())

    tokenized = [_tok(t) for t in texts]
    n = len(tokenized)

    # IDF over hashed features
    doc_freq = np.zeros(n_features, dtype="float32")
    for tokens in tokenized:
        seen = set()
        for tok in tokens:
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16) % n_features
            if h not in seen:
                doc_freq[h] += 1
                seen.add(h)
    idf = np.log((n + 1.0) / (doc_freq + 1.0)) + 1.0

    mat = np.zeros((n, n_features), dtype="float32")
    for i, tokens in enumerate(tokenized):
        if not tokens:
            continue
        for tok in tokens:
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16) % n_features
            mat[i, h] += 1.0
        mat[i] /= len(tokens)   # TF

    mat *= idf
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    return (mat / (norms + 1e-10)).astype("float32")


def _embed_st(texts: list[str], model: str) -> "np.ndarray":
    """
    sentence-transformers backend — requires torch.
    Falls back to hashed TF-IDF if torch is not available
    (e.g. in frozen PyInstaller binary without PyTorch bundled).
    """
    import numpy as np
    try:
        if model not in _st_cache:
            from sentence_transformers import SentenceTransformer
            _st_cache[model] = SentenceTransformer(model)
        m = _st_cache[model]
        vecs = m.encode(texts, show_progress_bar=False, convert_to_numpy=True)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return (vecs / (norms + 1e-10)).astype("float32")
    except (ImportError, Exception) as _err:
        # torch / sentence-transformers not available — use hash TF-IDF
        _no_torch_key = "__hash_tfidf__"
        _st_cache[_no_torch_key] = True   # suppress repeated attempts
        return _embed_hash_tfidf(texts)


def _embed(texts: list[str], model_name: str) -> "np.ndarray":
    """Route to Ollama or sentence-transformers based on model name."""
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
        self._embed_dim: int | None = None
        _ensure_dir()
        self._load()

    # ---- persistence -------------------------------------------- #
    def _load(self):
        if os.path.exists(self.META_FILE) and os.path.exists(self.INDEX_FILE):
            try:
                faiss = _import_faiss()
                with open(self.META_FILE, encoding="utf-8") as f:
                    meta = json.load(f)
                # Support both old format (list) and new format (dict)
                if isinstance(meta, list):
                    self._chunks = meta
                    self._embed_dim = None
                else:
                    self._chunks = meta.get("chunks", [])
                    self._embed_dim = meta.get("embed_dim")
                    saved_model = meta.get("embed_model")
                    if saved_model:
                        self.embed_model = saved_model
                self._index = faiss.read_index(self.INDEX_FILE)
                return
            except Exception:
                pass
        self._index = None
        self._chunks = []
        self._embed_dim = None

    def _save(self):
        faiss = _import_faiss()
        faiss.write_index(self._index, self.INDEX_FILE)
        meta = {
            "chunks": self._chunks,
            "embed_model": self.embed_model,
            "embed_dim": self._index.d if self._index else None,
        }
        with open(self.META_FILE, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)

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
        faiss = _import_faiss()
        import numpy as np

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
        # Guard: dimension must match index — mismatch happens when embed
        # backend changes (e.g. Ollama off → hash TF-IDF fallback)
        idx_dim = self._index.d
        q_dim   = qvec.shape[1]
        if idx_dim != q_dim:
            # Dimension mismatch — backend changed (e.g. Ollama off → TF-IDF).
            # Never block GUI thread with re-embed. Return empty; user should
            # clear and re-index with current backend.
            return []
        k = min(k, len(self._chunks))
        try:
            _, idxs = self._index.search(qvec, k)
            return [self._chunks[i]["text"] for i in idxs[0] if i >= 0]
        except Exception:
            return []

    # ---- maintenance -------------------------------------------- #
    def remove_source(self, source: str) -> int:
        """Remove all chunks from a given source filename. Returns removed count."""
        # FAISS flat index doesn't support remove — rebuild without those chunks
        faiss = _import_faiss()
        import numpy as np
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
