"""
Tests for core/agent.py — the tool-calling loop with planning.

The fake model drives the loop deterministically: it returns a two-step
plan, then per sub-task a single tool call followed by a final answer,
then a combined final answer. No real ollama/API required.
"""

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _tool_message(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {
        "content": "",
        "tool_calls": [{
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)},
        }],
    }


class FakeModel:
    def __init__(self, script: list[dict]) -> None:
        self._script = list(script)
        self.calls: list[tuple[bool, list[dict]]] = []
        self.system_contents: list[str] = []
        self.kind = "ollama"
        self.provider_id = "ollama"
        self.label = "Fake"
        self.default_model = "fake"

    def resolve_model(self, model=None):
        return model or self.default_model

    def chat(self, messages, *, model=None, tools=None):
        self.calls.append((tools is not None, [m.get("role") for m in messages]))
        if messages and messages[0].get("role") == "system":
            self.system_contents.append(messages[0].get("content", ""))
        return self._script.pop(0)


@pytest.fixture(autouse=True)
def make_project(tmp_path):
    """A tiny project the agent can read/write inside."""
    (tmp_path / "main.py").write_text("x = 1\n")


def _patch_agent(monkeypatch, fake):
    from core import ai as ai_mod
    monkeypatch.setattr(ai_mod, "get_provider", lambda cfg=None, provider=None, api_key="": fake)


class TestPlanning:
    def test_parse_plan_object(self):
        from core.agent import _parse_plan
        plan = _parse_plan(
            '{"subtasks": [{"title": "A", "goal": "do a"}, {"title": "B", "goal": "do b"}]}'
        )
        assert [s["title"] for s in plan] == ["A", "B"]

    def test_parse_plan_with_fences(self):
        from core.agent import _parse_plan
        plan = _parse_plan('```json\n{"subtasks": [{"title": "X", "goal": "g"}]}\n```')
        assert plan[0]["title"] == "X"

    def test_parse_plan_bare_array(self):
        from core.agent import _parse_plan
        plan = _parse_plan('[{"title": "Y", "goal": "h"}]')
        assert plan[0]["title"] == "Y"

    def test_parse_plan_prose_falls_back_single(self):
        from core.agent import _parse_plan
        plan = _parse_plan("no valid plan here, just chat")
        assert len(plan) == 1
        assert plan[0]["goal"] == ""

    def test_plan_subtasks_falls_back_on_error(self):
        from core.agent import _plan_subtasks

        class Boom:
            kind = "ollama"

            def chat(self, messages, *, model=None, tools=None):
                raise RuntimeError("model exploded")

        plan = _plan_subtasks("do a thing", Boom())
        assert len(plan) == 1
        assert plan[0]["title"] == "Complete the task"


class TestRunAgent:
    def test_runs_plan_then_subtasks_then_synthesis(self, tmp_path, monkeypatch):
        from core.agent import run_agent

        plan_msg = {
            "content": json.dumps({
                "subtasks": [
                    {"title": "Explore", "goal": "read main.py"},
                    {"title": "Edit", "goal": "bump x to 2"},
                ]
            })
        }
        script = [
            plan_msg,                                   # planning
            _tool_message("read_file", {"path": "main.py"}),  # subtask 1 step 1
            {"content": "saw x=1"},                     # subtask 1 done
            _tool_message("edit_file", {"path": "main.py", "old_string": "x = 1", "new_string": "x = 2"}),  # subtask 2 step 1
            {"content": "edited to x=2"},               # subtask 2 done
            {"content": "Done: read then edited main.py."},  # synthesis
        ]
        fake = FakeModel(script)
        _patch_agent(monkeypatch, fake)

        events = []
        result = run_agent("fix main.py", workdir=str(tmp_path), on_event=lambda k, t: events.append((k, t)))

        assert result == "Done: read then edited main.py."
        kinds = [k for k, _ in events]
        assert "plan" in kinds
        assert "text" in kinds
        # the repo map is injected into the sub-task system prompt
        assert any("Project layout" in s for s in fake.system_contents)
        # each sub-task got its own call series; synthesis used no tools
        tool_flags = [tools for tools, _ in fake.calls]
        assert tool_flags[0] is False    # planning
        assert tool_flags[-1] is False   # synthesis
        assert all(tool_flags[1:-1])     # sub-task calls all had tools

    def test_single_subtask_plan_skips_synthesis_call(self, tmp_path, monkeypatch):
        from core.agent import run_agent

        script = [
            {"content": "garbage plan", },             # planning -> fallback single
            _tool_message("read_file", {"path": "main.py"}),
            {"content": "ok"},                          # subtask done
        ]
        fake = FakeModel(script)
        _patch_agent(monkeypatch, fake)

        result = run_agent("read main.py", workdir=str(tmp_path))
        assert "file" in (result or "").lower() or "ok" in result

    def test_max_steps_budget_respected(self, tmp_path, monkeypatch):
        from core.agent import run_agent

        # Model always calls a tool, never finishes: hits the per-subtask budget.
        script = [{"content": '{"subtasks": [{"title": "T", "goal": "g"}]}'}]
        script += [_tool_message("list_dir", {"path": "."})] * 5
        fake = FakeModel(script)
        _patch_agent(monkeypatch, fake)

        result = run_agent("do t", workdir=str(tmp_path), max_steps=3)
        assert "budget" in result

    def test_carry_forward_context_includes_prior_results(self):
        from core.agent import _build_subtask_context

        plan = [
            {"title": "Step1", "goal": "g1"},
            {"title": "Step2", "goal": "g2"},
        ]
        results = [{"title": "Step1", "summary": "found the bug"}]
        ctx = _build_subtask_context("fix bug", plan, 2, results)
        assert "Step1" in ctx
        assert "found the bug" in ctx
        assert "Current sub-task (2 of 2)" in ctx
        assert "Step2" in ctx