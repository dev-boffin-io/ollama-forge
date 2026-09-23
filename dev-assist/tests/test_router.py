"""
Tests for core/router.py — intent detection and dispatch.
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.router import INTENTS, handle_input

# ── Intent pattern matching tests ────────────────────────────────────────────

def _match(text: str) -> str | None:
    """Return the func_name for the first matching intent, or None."""
    text_lower = text.lower()
    for pattern, _module_path, func_name in INTENTS:
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
        assert _match("tunnel") == "run"

    def test_ngrok(self):
        assert _match("ngrok") == "run"

    def test_expose_port(self):
        assert _match("expose 3000") == "run"

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

    # RAG fallthrough (explicit `ask` only)
    def test_rag_ask(self):
        func = _match("ask what does main.py do")
        assert func == "rag_ask"

    def test_rag_ask_requires_prefix(self):
        assert _match("what is the architecture") is None

    def test_plain_questions_not_rag(self):
        # Plain text no longer routes to RAG — the agent handles it.
        for text in (
            "what is the architecture",
            "explain this function",
            "there is a bug here",
            "what does this directory contain",
        ):
            assert _match(text) is None, text

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
        """explicit `ask` must come after specific module patterns."""
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


class TestAgentFallback:
    def test_plain_message_routes_to_agent(self, monkeypatch):
        """OpenCode parity: unmatched plain text runs the default agent."""
        calls: list[tuple[str, dict]] = []

        def fake_run_task(text, **kwargs):
            calls.append((text, kwargs.get("flags", {})))
            return "ok"

        monkeypatch.setattr("modules.agent_mode.run_task", fake_run_task)
        handle_input("what does this directory contain")
        (task, flags) = calls[0]
        assert task == "what does this directory contain"
        assert flags.get("agent") is None

    def test_plain_message_parses_flags(self, monkeypatch):
        """Flags like --agent/--yes are honored on plain messages too."""
        calls: list[tuple[str, dict]] = []

        def fake_run_task(text, **kwargs):
            calls.append((text, kwargs.get("flags", {})))
            return "ok"

        monkeypatch.setattr("modules.agent_mode.run_task", fake_run_task)
        handle_input("list these files --agent explore --yes")
        (task, flags) = calls[0]
        assert task == "list these files"
        assert flags.get("agent") == "explore"
        assert flags.get("auto_yes") is True

    def test_explicit_ask_bypasses_agent(self, monkeypatch):
        """`ask ...` stays on fast RAG, not the agent."""
        calls: list[str] = []

        def fake_run_task(text, **kwargs):
            calls.append(text)
            return "ok"

        monkeypatch.setattr("modules.agent_mode.run_task", fake_run_task)
        handle_input("ask what does main.py do")
        assert calls == []
