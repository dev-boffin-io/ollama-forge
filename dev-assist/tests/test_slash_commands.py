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
