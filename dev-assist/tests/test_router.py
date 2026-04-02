"""
Tests for core/router.py — intent detection and dispatch.
"""

import sys
import os
import re
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.router import INTENTS


# ── Intent pattern matching tests ────────────────────────────────────────────

def _match(text: str) -> str | None:
    """Return the func_name for the first matching intent, or None."""
    text_lower = text.lower()
    for pattern, module_path, func_name in INTENTS:
        if re.search(pattern, text_lower):
            return func_name
    return None


class TestIntentPatterns:
    # Index
    def test_index_path(self):
        assert _match("index /home/user/project") == "run"

    def test_index_dot(self):
        assert _match("index .") == "run"

    def test_index_status(self):
        assert _match("index status") == "run"

    def test_idx_alias(self):
        assert _match("idx /tmp/proj") == "run"

    # Audit
    def test_audit(self):
        assert _match("audit") == "run"

    def test_audit_no_sensitive(self):
        assert _match("audit --no-sensitive") == "run"

    # Port / cmd
    def test_fix_port(self):
        assert _match("fix port 3000") == "fix_port"

    def test_kill_port(self):
        assert _match("kill port 8080") == "fix_port"

    def test_port_number(self):
        assert _match("port 5432") == "fix_port"

    # Tunnel
    def test_tunnel(self):
        assert _match("tunnel") == "run_tunnel" or _match("tunnel") == "run"

    def test_ngrok(self):
        assert _match("ngrok") is not None

    def test_expose_port(self):
        assert _match("expose 3000") is not None

    # Git
    def test_git_push(self):
        assert _match("git push fix") == "run"

    def test_git_pull(self):
        assert _match("git pull") == "run"

    def test_git_conflict(self):
        assert _match("conflict") == "run"

    def test_git_rebase(self):
        assert _match("git rebase") == "run"

    # File
    def test_rename(self):
        assert _match("rename *.txt") == "run"

    def test_clean(self):
        assert _match("clean") == "run"

    # Built-ins
    def test_model(self):
        assert _match("model") == "model_select"

    def test_model_list(self):
        assert _match("model list") == "model_select"

    def test_help(self):
        assert _match("help") == "show_help"

    def test_commands(self):
        assert _match("commands") == "show_help"

    def test_plugins(self):
        assert _match("plugins") == "list_plugins"

    def test_status(self):
        assert _match("status") == "show_status"

    def test_history(self):
        assert _match("history") == "show_history"

    def test_clear_history(self):
        assert _match("history clear") == "clear_history"

    def test_clear_history_reversed(self):
        assert _match("clear history") == "clear_history"

    # RAG fallthrough
    def test_rag_ask(self):
        func = _match("ask what does main.py do")
        assert func == "rag_ask"

    def test_rag_what(self):
        func = _match("what is the architecture")
        assert func == "rag_ask"

    def test_rag_explain(self):
        assert _match("explain this function") == "rag_ask"

    def test_rag_bug(self):
        assert _match("there is a bug here") == "rag_ask"

    def test_rag_architecture(self):
        assert _match("architecture of this project") == "rag_ask"

    # Priority: index before rag
    def test_index_before_rag(self):
        # "index" should match indexer, not rag
        func = _match("index /some/path")
        assert func == "run"  # indexer.run


class TestIntentOrdering:
    def test_no_duplicate_patterns(self):
        """Each pattern should appear only once."""
        patterns = [p for p, _, _ in INTENTS]
        assert len(patterns) == len(set(patterns)), "Duplicate patterns found in INTENTS"

    def test_broad_patterns_at_end(self):
        """rag_ask patterns (what/how/explain) must come after specific ones."""
        rag_indices = [
            i for i, (_, _, fn) in enumerate(INTENTS) if fn == "rag_ask"
        ]
        specific_indices = [
            i for i, (_, m, _) in enumerate(INTENTS)
            if m in ("modules.indexer", "modules.code_audit", "modules.git_helper")
        ]
        if rag_indices and specific_indices:
            assert min(rag_indices) > max(specific_indices), \
                "RAG fallthrough patterns must come AFTER specific module patterns"
