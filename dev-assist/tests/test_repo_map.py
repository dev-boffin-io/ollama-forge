"""
Tests for core/repo_map.py — the compact project map injected into the
agent's context (file tree + top-level signatures, with similarity-based
narrowing for large projects).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _write(project, rel, content):
    p = os.path.join(project, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p


class TestRepoMap:
    def test_small_project_builds_tree_and_signatures(self, tmp_path, monkeypatch):
        monkeypatch.setenv("_TEST_ONLY", "1")
        _write(tmp_path, "main.py", (
            "import os\n\n"
            "def hello(name: str) -> str:\n    return f'hi {name}'\n\n"
            "class Greeter:\n    def greet(self):\n        return hello('x')\n"
        ))
        _write(tmp_path, "README.md", "# Project\nDoes things.\n")

        from core.repo_map import build_repo_map, REPO_MAP_CHAR_CAP
        info = build_repo_map("hello", str(tmp_path), max_chars=REPO_MAP_CHAR_CAP)

        assert info["text"]
        assert info["focused"] is False
        assert "main.py" in info["text"]
        assert "README.md" in info["text"]
        assert "def hello(name: str)" in info["text"]
        assert "class Greeter" in info["text"]

    def test_empty_project_returns_empty(self, tmp_path):
        from core.repo_map import build_repo_map
        info = build_repo_map("anything", os.path.join(tmp_path, "empty"),
                              max_chars=100000)
        assert info["text"] == ""
        assert info["focused"] is False

    def test_large_project_falls_back_to_similarity(self, tmp_path, monkeypatch):
        # Many signature-bearing files with a tiny cap → full map over budget,
        # so the builder must narrow via the vector store's search.
        for i in range(30):
            _write(tmp_path, f"mod{i:02}.py", (
                f"def function_{i}(x):\n    return x + {i}\n"
            ))

        def fake_search(query, top_k=5):
            return [{
                "filepath": os.path.join(str(tmp_path), "mod07.py"),
                "chunk_idx": 0,
                "content": "",
                "score": 0.9,
            }]

        monkeypatch.setattr("core.vector_store.search", fake_search)

        from core.repo_map import build_repo_map
        info = build_repo_map("the thing about module seven", str(tmp_path),
                              max_chars=200)

        assert info["focused"] is True
        assert "mod07.py" in info["text"]
        assert len(info["text"]) <= 2000  # bounded, not the whole tree

    def test_large_project_with_empty_index_truncates(self, tmp_path, monkeypatch):
        for i in range(30):
            _write(tmp_path, f"mod{i:02}.py",
                   f"def function_{i}(x):\n    return x + {i}\n")

        monkeypatch.setattr("core.vector_store.search", lambda q, top_k=5: [])

        from core.repo_map import build_repo_map
        info = build_repo_map("anything", str(tmp_path), max_chars=200)

        # Nothing relevant found → truncated best-effort map, still usable.
        assert info["focused"] is False
        assert "mod00.py" in info["text"]
        assert "truncated" in info["text"]