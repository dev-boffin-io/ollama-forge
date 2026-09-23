"""
Tests for modules/slash_commands.py — slash (`/`) command port.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import modules.slash_commands as slash
from core import session_store as ss


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("DEV_ASSIST_DATA_DIR", str(tmp_path))
    ss.reset_state()
    yield ss
    ss.reset_state()


@pytest.fixture
def fake_cfg(tmp_path, monkeypatch):
    """In-memory config replacements so /provider and /model never touch the
    real settings.json. Also pins the live-model lookup to the catalog so the
    interactive pickers are deterministic and offline."""
    from core import config as core_config
    from core import providers as _providers

    raw = {
        "active_provider": "ollama",
        "ai_engine": "ollama",
        "providers": _providers.provider_defaults_raw(),
    }
    saved: dict = {}

    def _fake_load():
        return dict(raw)

    def _fake_save(data):
        if hasattr(data, "model_dump"):
            saved.update(data.model_dump())
        else:
            saved.update(dict(data))
        raw.clear()
        raw.update(saved)

    monkeypatch.setattr(core_config, "load_config", _fake_load)
    monkeypatch.setattr(core_config, "save_config", _fake_save)
    monkeypatch.setattr(
        "core.ai.resolve_provider_live_models",
        lambda pid, **k: list(_providers.PROVIDERS.get(pid, {}).get("models", [])),
    )
    monkeypatch.setenv("DEV_ASSIST_DATA_DIR", str(tmp_path))
    ss.reset_state()
    yield raw
    ss.reset_state()


class TestParseArguments:
    def test_simple_words(self):
        assert slash.parse_arguments("foo bar baz") == ["foo", "bar", "baz"]

    def test_quotes_keep_spaces(self):
        assert slash.parse_arguments('"two words" single \'also kept\' plain') == [
            "two words", "single", "also kept", "plain",
        ]

    def test_empty(self):
        assert slash.parse_arguments("") == []


class TestRenderTemplate:
    def test_arguments_placeholder(self):
        out = slash.render_template("Create component $ARGUMENTS", "Button", "/tmp")
        assert out == "Create component Button"

    def test_positional_single(self):
        out = slash.render_template("Open $1 and close $2", "a.py b.py", "/tmp")
        assert out == "Open a.py and close b.py"

    def test_last_positional_captures_rest(self):
        out = slash.render_template("Run $1 with the rest: $2", "pytest tests/ x y", "/tmp")
        assert out == "Run pytest with the rest: tests/ x y"

    def test_missing_positional_is_empty(self):
        out = slash.render_template("Do $1 and $2", "only", "/tmp")
        assert out == "Do only and"

    def test_arguments_appended_when_no_placeholders(self):
        out = slash.render_template("Summarize this task", "write docs", "/tmp")
        assert out == "Summarize this task\n\nwrite docs"

    def test_path_placeholder_replaces_workdir(self):
        out = slash.render_template("Read AGENTS.md at ${path}", "", "/workspace/app")
        assert "AGENTS.md at /workspace/app" in out

    def test_mixed_placeholders_and_raw_arguments(self):
        # $1 is the highest positional here, so it captures every remaining arg
        # (opencode semantics) and matches $ARGUMENTS.
        out = slash.render_template("Input: $ARGUMENTS\nRef: $1", "one two", "/tmp")
        assert out == "Input: one two\nRef: one two"


class TestHints:
    def test_arguments_hint(self):
        assert slash.hints("Do $ARGUMENTS now") == ["$ARGUMENTS"]

    def test_positional_hints_match_template_order(self):
        # hints() preserves first-seen order (opencode behaviour) and dedupes.
        assert slash.hints("Use $2 with $1 and $2 again") == ["$2", "$1"]


class TestRegistry:
    def test_builtin_names_present(self):
        names = set(slash.list_commands("/tmp"))
        for expected in ("init", "review", "new", "sessions", "resume", "rename", "delete", "help"):
            assert expected in names

    def test_init_and_review_are_template_commands(self):
        cmds = slash.list_commands("/tmp")
        assert cmds["init"].source == "builtin"
        assert "$ARGUMENTS" in cmds["init"].template
        assert "code reviewer" in cmds["review"].template

    def test_config_commands(self, tmp_path, monkeypatch):
        from core import config as core_config

        fake_cfg = {"commands": {
            "component": {
                "description": "scaffold a component",
                "template": "Create a React component named $ARGUMENTS with TypeScript",
            },
            "review": {  # config may override the built-in (opencode behaviour)
                "template": "Custom review of $ARGUMENTS",
            },
        }}
        monkeypatch.setattr(core_config, "load_config", lambda: fake_cfg)
        cmds = slash.list_commands("/tmp")
        assert cmds["component"].source == "command"
        assert slash.render_template(
            cmds["component"].template, "Button", "/tmp"
        ) == "Create a React component named Button with TypeScript"
        assert cmds["review"].source == "command"
        assert cmds["review"].template == "Custom review of $ARGUMENTS"

    def test_skills_as_commands(self, tmp_path, monkeypatch):
        skill_dir = tmp_path / "skills" / "demo-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("How to work with Demo\n\nStep one.", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        cmds = slash.list_commands(str(tmp_path))
        assert "demo-skill" in cmds
        spec = cmds["demo-skill"]
        assert spec.source == "skill"
        assert "How to work with Demo" in spec.template
        assert "Base directory for this skill:" in spec.template
        assert str(skill_dir) in spec.template

    def test_skill_never_overrides_builtin(self, tmp_path, monkeypatch):
        skill_dir = tmp_path / "skills" / "init"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("skill init body", encoding="utf-8")
        cmds = slash.list_commands(str(tmp_path))
        assert cmds["init"].source == "builtin"
        assert "skill init body" not in cmds["init"].template

    def test_command_names_sorted(self):
        names = slash.command_names("/tmp")
        assert names == sorted(names)
        assert "init" in names


class TestDispatch:
    def test_unknown_command_returns_false(self):
        assert slash.execute("definitely-not-a-command") is False

    def test_run_ignores_non_slash(self):
        # Should not raise and not attempt dispatch.
        slash.run("just a normal message")
        slash.run(" /spaced")
        slash.run("")

    def test_run_new_starts_session(self, store, tmp_path):
        slash.run("/new")
        assert store.current_session_id() is not None

    def test_run_rename_current(self, store):
        slash.run("/new")
        sid = store.current_session_id()
        slash.run("/rename my session title")
        assert store.get_session_info(sid).title == "my session title"

    def test_run_resume_by_hash_index(self, store):
        a = store.new_session(project="/p")
        store.append_message(a.id, "user", "first")
        b = store.new_session(project="/p")
        store.append_message(b.id, "user", "second")
        # list order is most-recent-first → #2 is the older session `a`.
        slash.run("/resume #2")
        assert store.current_session_id() == a.id

    def test_run_delete_by_id(self, store):
        a = store.new_session(project="/p")
        sid = a.id
        slash.run(f"/delete {sid}")
        assert store.get_session_info(sid) is None

    def test_unknown_command_message_lists_available(self, capsys):
        slash.run("/snarf")
        out = capsys.readouterr().out
        assert "/nope" not in out
        assert "Unknown command" in out
        assert "/init" in out


class TestAgentsAndCompact:
    def test_agents_and_compact_registered(self, store, tmp_path):
        cmds = slash.list_commands(str(tmp_path))
        assert cmds["agents"].source == "builtin"
        assert cmds["compact"].source == "builtin"
        assert "agents" in slash.command_names(str(tmp_path))
        assert "compact" in slash.command_names(str(tmp_path))

    def test_agents_lists_router(self, store, tmp_path, capsys, monkeypatch):
        from core import config as core_config
        monkeypatch.setattr(core_config, "load_config", lambda: {
            "routing": {"enabled": True, "default_agent": "coder"},
            "agents": {},
        })
        assert slash.execute("agents", "", str(tmp_path)) is True
        out = capsys.readouterr().out
        assert "coder" in out
        assert "reviewer" in out
        assert "automatic routing: on" in out
        assert "default agent: coder" in out

    def test_compact_returns_none_without_active_session(self, store, tmp_path, capsys):
        assert slash.execute("compact", "", str(tmp_path)) is True
        assert "No active session" in capsys.readouterr().out

    def test_compact_summarises_and_stores_turn(self, store, tmp_path, capsys, monkeypatch):
        from core import agents as agents_mod
        monkeypatch.setattr(agents_mod, "compact_context", lambda transcript: "SUMMARIZED_NOW")

        s = store.new_session(project=str(tmp_path))
        store.append_message(s.id, "user", "first long thing " + "x" * 100)
        store.append_message(s.id, "assistant", "reply " + "y" * 100)

        assert slash.execute("compact", "", str(tmp_path)) is True
        msgs = store.get_messages(s.id)
        assert msgs[-1].agent == "compaction"
        assert "SUMMARIZED_NOW" in msgs[-1].content
        assert "Compacted" in capsys.readouterr().out


class TestProviderCommand:
    def test_registered(self):
        cmds = slash.list_commands("/tmp")
        assert cmds["provider"].source == "builtin"
        assert cmds["provider"].handler is not None
        assert "provider" in slash.command_names("/tmp")

    def test_switch(self, fake_cfg, capsys):
        assert slash.execute("provider", "groq", "/tmp") is True
        assert fake_cfg["active_provider"] == "groq"
        assert fake_cfg["ai_engine"] == "api"
        assert "Groq" in capsys.readouterr().out

    def test_switch_uses_catalog_id_case_insensitive(self, fake_cfg, capsys):
        assert slash.execute("provider", "Anthropic", "/tmp") is True
        assert fake_cfg["active_provider"] == "anthropic"
        assert fake_cfg["ai_engine"] == "api"

    def test_switch_back_to_ollama_sets_legacy_engine(self, fake_cfg, capsys):
        fake_cfg["active_provider"] = "groq"
        assert slash.execute("provider", "ollama", "/tmp") is True
        assert fake_cfg["active_provider"] == "ollama"
        assert fake_cfg["ai_engine"] == "ollama"

    def test_unknown_provider_rejected(self, fake_cfg, capsys):
        assert slash.execute("provider", "nope", "/tmp") is True
        assert fake_cfg["active_provider"] == "ollama"
        assert "Unknown provider" in capsys.readouterr().out

    def test_list_shows_providers(self, fake_cfg, capsys):
        assert slash.execute("provider", "list", "/tmp") is True
        out = capsys.readouterr().out
        assert "ollama" in out
        assert "groq" in out
        assert "← active" in out

    def test_no_args_shows_current_and_picker_cancel(self, fake_cfg, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda *a, **k: "")
        assert slash.execute("provider", "", "/tmp") is True
        out = capsys.readouterr().out
        assert "ollama" in out
        assert "Cancelled." in out
        assert fake_cfg["active_provider"] == "ollama"

    def test_picker_switches_by_number(self, fake_cfg, monkeypatch, capsys):
        vals = iter(["2"])  # index 1 in PROVIDER_ORDER → openai
        monkeypatch.setattr("builtins.input", lambda *a, **k: next(vals))
        assert slash.execute("provider", "", "/tmp") is True
        assert fake_cfg["active_provider"] == "openai"


class TestModelCommand:
    def test_registered(self):
        cmds = slash.list_commands("/tmp")
        assert cmds["model"].source == "builtin"
        assert cmds["model"].handler is not None

    def test_set_form(self, fake_cfg, capsys):
        assert slash.execute("model", "set gpt-4o", "/tmp") is True
        assert fake_cfg["providers"]["ollama"]["default_model"] == "gpt-4o"
        assert "ollama/gpt-4o" in capsys.readouterr().out

    def test_direct_form(self, fake_cfg, capsys):
        assert slash.execute("model", "llama3.1:8b", "/tmp") is True
        assert fake_cfg["providers"]["ollama"]["default_model"] == "llama3.1:8b"
        assert "ollama/llama3.1:8b" in capsys.readouterr().out

    def test_set_multiword_model_name(self, fake_cfg, capsys):
        assert slash.execute("model", "set anthropic/claude-sonnet-4-5", "/tmp") is True
        assert fake_cfg["providers"]["ollama"]["default_model"] == "anthropic/claude-sonnet-4-5"

    def test_set_sets_active_provider(self, fake_cfg, capsys):
        fake_cfg["active_provider"] = "groq"
        assert slash.execute("model", "set llama-3.3-70b-versatile", "/tmp") is True
        assert fake_cfg["providers"]["groq"]["default_model"] == "llama-3.3-70b-versatile"
        assert "groq/llama-3.3-70b-versatile" in capsys.readouterr().out

    def test_list(self, fake_cfg, capsys):
        assert slash.execute("model", "list", "/tmp") is True
        out = capsys.readouterr().out
        assert "qwen2.5-coder:7b" in out
        assert "← active" in out

    def test_list_specific_provider(self, fake_cfg, capsys):
        assert slash.execute("model", "list groq", "/tmp") is True
        out = capsys.readouterr().out
        assert "llama-3.3-70b-versatile" in out

    def test_set_requires_name(self, fake_cfg, capsys):
        assert slash.execute("model", "set", "/tmp") is True
        assert "Usage:" in capsys.readouterr().out

    def test_no_args_shows_current_and_picker_cancel(self, fake_cfg, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda *a, **k: "")
        assert slash.execute("model", "", "/tmp") is True
        out = capsys.readouterr().out
        assert "Active model" in out
        assert "Cancelled." in out
        assert fake_cfg["providers"]["ollama"]["default_model"] == "qwen2.5-coder:7b"

    def test_picker_switches_model_by_number(self, fake_cfg, monkeypatch, capsys):
        vals = iter(["2"])  # qwen2.5-coder:3b is 2nd catalog model for ollama
        monkeypatch.setattr("builtins.input", lambda *a, **k: next(vals))
        assert slash.execute("model", "", "/tmp") is True
        assert fake_cfg["providers"]["ollama"]["default_model"] == "qwen2.5-coder:3b"

    def test_picker_switches_by_exact_name(self, fake_cfg, monkeypatch, capsys):
        vals = iter(["codellama:7b"])
        monkeypatch.setattr("builtins.input", lambda *a, **k: next(vals))
        assert slash.execute("model", "", "/tmp") is True
        assert fake_cfg["providers"]["ollama"]["default_model"] == "codellama:7b"
