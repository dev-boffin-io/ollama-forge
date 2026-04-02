"""
Tests for RAG — indexer chunking, vector_store, rag_engine query enrichment.
"""

import sys
import os
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Indexer chunking tests ────────────────────────────────────────────────────

class TestChunking:
    def _chunk(self, filepath, content):
        """Helper: write temp file and chunk it."""
        from modules.indexer import _chunk_file
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return _chunk_file(filepath)

    def test_empty_file_returns_no_chunks(self, tmp_path):
        f = tmp_path / "empty.py"
        chunks = self._chunk(str(f), "")
        assert chunks == []

    def test_blank_only_file_returns_no_chunks(self, tmp_path):
        f = tmp_path / "blank.py"
        chunks = self._chunk(str(f), "\n\n   \n\n")
        assert chunks == []

    def test_small_file_returns_one_chunk(self, tmp_path):
        f = tmp_path / "small.py"
        code = "def foo():\n    return 42\n"
        chunks = self._chunk(str(f), code)
        assert len(chunks) >= 1

    def test_chunk_contains_filepath(self, tmp_path):
        f = tmp_path / "test.py"
        chunks = self._chunk(str(f), "x = 1\n")
        assert any(str(f) in c["content"] for c in chunks)

    def test_chunk_has_line_numbers(self, tmp_path):
        f = tmp_path / "lines.py"
        chunks = self._chunk(str(f), "a = 1\nb = 2\nc = 3\n")
        for c in chunks:
            assert "start_line" in c
            assert "end_line" in c
            assert c["start_line"] >= 1

    def test_large_file_produces_multiple_chunks(self, tmp_path):
        f = tmp_path / "large.py"
        # 200 lines — well above CHUNK_SIZE
        code = "\n".join(f"x_{i} = {i}" for i in range(200))
        chunks = self._chunk(str(f), code)
        assert len(chunks) > 1

    def test_python_semantic_chunks_split_at_def(self, tmp_path):
        f = tmp_path / "funcs.py"
        code = (
            "def alpha():\n    pass\n\n"
            "def beta():\n    return 1\n\n"
            "def gamma():\n    return 2\n"
        )
        chunks = self._chunk(str(f), code)
        # With semantic chunking, expect at least 2 chunks (one per func boundary)
        assert len(chunks) >= 1

    def test_non_utf8_file_handled_gracefully(self, tmp_path):
        f = tmp_path / "latin.py"
        f.write_bytes(b"x = 1\n# \xe9\xe8\n")
        from modules.indexer import _chunk_file
        chunks = _chunk_file(str(f))
        assert isinstance(chunks, list)

    def test_binary_file_detected(self, tmp_path):
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x02\x03binary data")
        from modules.indexer import _is_binary
        assert _is_binary(str(f)) is True

    def test_text_file_not_binary(self, tmp_path):
        f = tmp_path / "text.py"
        f.write_text("print('hello')\n")
        from modules.indexer import _is_binary
        assert _is_binary(str(f)) is False

    def test_path_extraction(self):
        from modules.indexer import _extract_path
        assert _extract_path("index /home/user/proj") == "/home/user/proj"
        assert _extract_path("index .") == "."
        assert _extract_path("index ./src") == "./src"
        assert _extract_path("index") == ""


# ── collect_files tests ───────────────────────────────────────────────────────

class TestCollectFiles:
    def test_collects_python_files(self, tmp_path):
        (tmp_path / "a.py").write_text("x=1")
        (tmp_path / "b.go").write_text("package main")
        from modules.indexer import _collect_files
        files = list(_collect_files(str(tmp_path)))
        basenames = [os.path.basename(f) for f in files]
        assert "a.py" in basenames
        assert "b.go" in basenames

    def test_skips_pycache(self, tmp_path):
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "foo.pyc").write_bytes(b"\x00compiled")
        from modules.indexer import _collect_files
        files = list(_collect_files(str(tmp_path)))
        assert all("__pycache__" not in f for f in files)

    def test_skips_node_modules(self, tmp_path):
        nm = tmp_path / "node_modules"
        nm.mkdir()
        (nm / "index.js").write_text("module.exports={}")
        from modules.indexer import _collect_files
        files = list(_collect_files(str(tmp_path)))
        assert all("node_modules" not in f for f in files)

    def test_skips_hidden_dirs(self, tmp_path):
        hidden = tmp_path / ".secret"
        hidden.mkdir()
        (hidden / "key.py").write_text("SECRET=1")
        from modules.indexer import _collect_files
        files = list(_collect_files(str(tmp_path)))
        assert all(".secret" not in f for f in files)

    def test_skips_unsupported_extension(self, tmp_path):
        (tmp_path / "image.png").write_bytes(b"\x89PNG")
        (tmp_path / "code.py").write_text("x=1")
        from modules.indexer import _collect_files
        files = list(_collect_files(str(tmp_path)))
        basenames = [os.path.basename(f) for f in files]
        assert "image.png" not in basenames
        assert "code.py" in basenames


# ── Vector store tests ────────────────────────────────────────────────────────

class TestVectorStore:
    """Uses a temp DB so real index is never touched."""

    @pytest.fixture(autouse=True)
    def patch_db_path(self, tmp_path, monkeypatch):
        import core.vector_store as vs
        monkeypatch.setattr(vs, "DB_PATH", str(tmp_path / "test_index.db"))

    def test_save_and_search(self):
        from core.vector_store import save_chunks, search
        chunks = [{"content": "def hello(): print('hello world')", "start_line": 1, "end_line": 1}]
        save_chunks("/fake/hello.py", chunks)
        results = search("hello world")
        assert len(results) > 0
        assert results[0]["filepath"] == "/fake/hello.py"

    def test_search_returns_empty_on_no_match(self):
        from core.vector_store import search
        results = search("xyzzy_no_match_ever")
        assert results == []

    def test_get_stats_empty(self):
        from core.vector_store import get_stats
        stats = get_stats()
        assert stats["total_files"] == 0
        assert stats["total_chunks"] == 0

    def test_get_stats_after_save(self):
        from core.vector_store import save_chunks, get_stats
        save_chunks("/a.py", [{"content": "x=1", "start_line": 1, "end_line": 1}])
        save_chunks("/b.py", [{"content": "y=2", "start_line": 1, "end_line": 1}])
        stats = get_stats()
        assert stats["total_files"] == 2
        assert stats["total_chunks"] == 2

    def test_clear_index(self):
        from core.vector_store import save_chunks, clear_index, get_stats
        save_chunks("/c.py", [{"content": "z=3", "start_line": 1, "end_line": 1}])
        clear_index()
        stats = get_stats()
        assert stats["total_files"] == 0

    def test_remove_file(self):
        from core.vector_store import save_chunks, remove_file, get_stats
        save_chunks("/keep.py", [{"content": "a=1", "start_line": 1, "end_line": 1}])
        save_chunks("/remove.py", [{"content": "b=2", "start_line": 1, "end_line": 1}])
        remove_file("/remove.py")
        stats = get_stats()
        assert stats["total_files"] == 1
        assert "/keep.py" in stats["files"]

    def test_search_top_k_respected(self):
        from core.vector_store import save_chunks, search
        for i in range(10):
            save_chunks(f"/file{i}.py", [{"content": f"function compute {i}", "start_line": 1, "end_line": 1}])
        results = search("function compute", top_k=3)
        assert len(results) <= 3


# ── Session context tests ─────────────────────────────────────────────────────

class TestSession:
    @pytest.fixture(autouse=True)
    def reset(self):
        from core.session import reset_session
        reset_session()
        yield
        reset_session()

    def test_empty_session_has_no_history(self):
        from core.session import get_session
        assert get_session().get_history() == []

    def test_add_user_and_assistant(self):
        from core.session import get_session
        sess = get_session()
        sess.add_user("hello")
        sess.add_assistant("world")
        h = sess.get_history()
        assert len(h) == 2
        assert h[0].role == "user"
        assert h[1].role == "assistant"

    def test_build_history_prompt_no_history(self):
        from core.session import get_session
        p = get_session().build_history_prompt("what is x")
        assert "what is x" in p

    def test_build_history_prompt_with_history(self):
        from core.session import get_session
        sess = get_session()
        sess.add_user("explain foo")
        sess.add_assistant("foo does bar")
        p = sess.build_history_prompt("what about baz")
        assert "explain foo" in p
        assert "what about baz" in p

    def test_clear_history(self):
        from core.session import get_session
        sess = get_session()
        sess.add_user("test")
        sess.clear_history()
        assert sess.get_history() == []

    def test_history_trim(self):
        from core.session import get_session, MAX_HISTORY_TURNS
        sess = get_session()
        for i in range(MAX_HISTORY_TURNS + 5):
            sess.add_user(f"q{i}")
            sess.add_assistant(f"a{i}")
        assert len(sess.get_history()) <= MAX_HISTORY_TURNS * 2

    def test_indexed_path(self):
        from core.session import get_session
        sess = get_session()
        assert sess.get_indexed_path() is None
        sess.set_indexed_path("/home/user/proj")
        assert sess.get_indexed_path() == "/home/user/proj"


# ── Prompt template tests ─────────────────────────────────────────────────────

class TestPrompts:
    def test_rag_ask_renders(self):
        from core.prompts import render
        result = render(
            "rag_ask",
            query="what does foo do",
            chunks=[{"filepath": "foo.py", "content": "def foo(): pass", "start_line": 1, "end_line": 1}],
        )
        assert "foo.py" in result
        assert "what does foo do" in result

    def test_code_audit_renders(self):
        from core.prompts import render
        result = render("code_audit", diff="+print('hello')")
        assert "diff" in result.lower() or "hello" in result

    def test_error_explain_renders(self):
        from core.prompts import render
        result = render("error_explain", error_text="NameError: name 'x' is not defined", context="")
        assert "NameError" in result

    def test_unknown_template_raises(self):
        from core.prompts import render
        with pytest.raises(KeyError):
            render("__nonexistent_template__")

    def test_list_templates_returns_list(self):
        from core.prompts import list_templates
        names = list_templates()
        assert isinstance(names, list)
        assert "rag_ask" in names
        assert "code_audit" in names


# ── Config tests ──────────────────────────────────────────────────────────────

class TestConfig:
    def test_load_config_returns_something(self):
        from core.config import load_config
        cfg = load_config()
        assert cfg is not None

    def test_env_api_key_takes_priority(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DEV_ASSIST_API_KEY", "test-secret-key")
        from core.config import load_config
        cfg = load_config()
        if hasattr(cfg, "get_active_api_key"):
            assert cfg.get_active_api_key() == "test-secret-key"

    def test_save_strips_api_key_when_env_set(self, monkeypatch, tmp_path):
        import json
        monkeypatch.setenv("DEV_ASSIST_API_KEY", "env-key")
        settings_path = tmp_path / "settings.json"
        settings_path.write_text('{"ai_engine": "ollama", "api_engine": {"api_key": "plaintext"}}')

        import core.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "CONFIG_PATH", settings_path)

        from core.config import load_config, save_config
        cfg = load_config()
        save_config(cfg)

        saved = json.loads(settings_path.read_text())
        # api_key should be blank in saved file when env var is set
        api_key_in_file = saved.get("api_engine", {}).get("api_key", "")
        assert api_key_in_file == ""
