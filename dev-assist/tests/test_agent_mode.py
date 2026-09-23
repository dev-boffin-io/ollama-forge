"""
Tests for modules/agent_mode.py — flag parsing and auto/undo wiring.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modules.agent_mode import _parse_run_args


class TestParseRunArgs:
    def test_plain_task(self):
        task, flags = _parse_run_args("do fix the bug")
        assert task == "do fix the bug"
        assert flags == {"auto": False, "auto_yes": False, "verbose": False, "agent": None}

    def test_auto_flag(self):
        task, flags = _parse_run_args("do fix the bug --auto")
        assert task == "do fix the bug"
        assert flags["auto"] is True

    def test_yolo_flag(self):
        task, flags = _parse_run_args("do fix the bug --yolo")
        assert flags["auto"] is True
        assert task == "do fix the bug"

    def test_auto_with_verbose(self):
        task, flags = _parse_run_args("do fix the bug --auto --verbose")
        assert flags["auto"] is True
        assert flags["verbose"] is True
        assert task == "do fix the bug"

    def test_yes_flag_unchanged(self):
        task, flags = _parse_run_args("do fix the bug --yes -v")
        assert flags["auto"] is False
        assert flags["auto_yes"] is True
        assert flags["verbose"] is True
        assert task == "do fix the bug"

    def test_prefix_flag_not_matched(self):
        # "automate" must not be treated as --auto.
        task, flags = _parse_run_args("do automate the build")
        assert flags["auto"] is False
        assert task == "do automate the build"

    def test_agent_flag_parsed(self):
        task, flags = _parse_run_args("do refactor --agent coder")
        assert flags["agent"] == "coder"
        assert task == "do refactor"

    def test_agent_flag_with_other_flags(self):
        task, flags = _parse_run_args("do refactor --agent reviewer --auto -v")
        assert flags == {"auto": True, "auto_yes": False, "verbose": True,
                         "agent": "reviewer"}
        assert task == "do refactor"

    def test_agent_flag_inside_text_not_matched(self):
        # "--agent" must be a real flag token, not a substring.
        task, flags = _parse_run_args("do make an agent for tests")
        assert flags["agent"] is None
        assert task == "do make an agent for tests"


class TestRunTaskFinalAnswer:
    def test_final_answer_is_printed(self, monkeypatch):
        """The agent's final answer must reach the terminal (it was dropped).
        The sub-task blow-by-blow streamed, but the finished synthesis never
        printed — so plain chat showed work without the actual answer."""
        import io

        from rich.console import Console

        import modules.agent_mode as am

        buf = io.StringIO()
        monkeypatch.setattr(am, "_console", Console(file=buf))

        class FakeTracker:
            def has_changes(self):
                return False

        monkeypatch.setattr("core.change_tracker.get_tracker", lambda: FakeTracker())
        monkeypatch.setattr(am, "_record_turn", lambda *a, **k: None)
        monkeypatch.setattr(
            "core.agent.run_agent", lambda *a, **k: "FINAL ANSWER"
        )

        result = am.run_task("refactor the widget", flags={"auto": True})

        assert result == "FINAL ANSWER"
        assert "FINAL ANSWER" in buf.getvalue()

    def test_final_answer_inline_markup_not_mangled(self, monkeypatch):
        """Model output may contain brackets/backticks — print it as plain text."""
        import io

        from rich.console import Console

        import modules.agent_mode as am

        buf = io.StringIO()
        monkeypatch.setattr(am, "_console", Console(file=buf))

        class FakeTracker:
            def has_changes(self):
                return False

        monkeypatch.setattr("core.change_tracker.get_tracker", lambda: FakeTracker())
        monkeypatch.setattr(am, "_record_turn", lambda *a, **k: None)
        monkeypatch.setattr(
            "core.agent.run_agent",
            lambda *a, **k: "Use `[bold] tags` like [x]",
        )

        am.run_task("task", flags={"auto": True})

        assert "Use `[bold] tags` like [x]" in buf.getvalue()
