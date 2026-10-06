"""
Regression tests for answer/no-volunteer behaviour.

A scripted fake provider records every message list sent to the model, so
these tests assert on what the loop actually asked for (prompts) and on what
it actually surfaced to the caller (events + final answer).

Covers:
  a) no internal scaffolding leaks into `text` events or the final answer
  b) trivial requests never call the planner
  c) NO_VOLUNTEER_RULE is defined once and stamped into every prompt
  d) `about` reports real sources with no *_error keys
  e) status/progress never arrive as `text`; the final answer shows once
"""

import json
import os
import sys

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


class ScriptedProvider:
    """Fake model: returns the script, recording every message list it gets."""

    kind = "ollama"
    provider_id = "ollama"
    label = "Scripted"
    default_model = "scripted"

    def __init__(self, script: list[dict]) -> None:
        self._script = list(script)
        self.recorded: list[list[dict]] = []   # every message list, copied
        self.calls: list[tuple[bool, int]] = []  # (tools offered, msg count)

    def resolve_model(self, model=None):
        return model or self.default_model

    def chat(self, messages, *, model=None, tools=None):
        self.recorded.append([dict(m) for m in messages])
        self.calls.append((tools is not None, len(messages)))
        return self._script.pop(0)

    @property
    def system_contents(self) -> list[str]:
        return [
            str(m[0].get("content", "")) for m in self.recorded
            if m and m[0].get("role") == "system"
        ]


def _patch_provider(monkeypatch, fake):
    from core import ai as ai_mod
    monkeypatch.setattr(ai_mod, "get_provider",
                        lambda cfg=None, provider=None, api_key="": fake)


@pytest.fixture(autouse=True)
def _project(tmp_path):
    (tmp_path / "main.py").write_text("x = 1\n")


def _run(tmp_path, monkeypatch, script, task, **kwargs):
    from core.agent import run_agent
    fake = ScriptedProvider(script)
    _patch_provider(monkeypatch, fake)
    events = []
    result = run_agent(task, workdir=str(tmp_path),
                       on_event=lambda k, t: events.append((k, t)), **kwargs)
    return fake, events, result


def _multi_step_script():
    """Plan → two sub-tasks (one tool call each) → synthesis."""
    return [
        {"content": json.dumps({"subtasks": [
            {"title": "Inspect main", "goal": "read main.py"},
            {"title": "Bump the value", "goal": "set x to 2"},
        ]})},
        _tool_message("read_file", {"path": "main.py"}),
        {"content": "the file holds one line"},
        _tool_message("read_file", {"path": "main.py"}),
        {"content": "checked it again"},
        {"content": "All done: x is now 2."},
    ]


class TestNothingLeaksIntoTheAnswer:
    """a) text events + final answer carry no internal scaffolding."""

    def test_text_events_and_final_answer_are_clean(self, tmp_path, monkeypatch):
        from core.tools import all_tool_schemas

        fake, events, result = _run(
            tmp_path, monkeypatch, _multi_step_script(),
            "update main.py so x becomes 2",
        )

        plan_payloads = [t for k, t in events if k == "plan"]
        tool_names = [s["function"]["name"] for s in all_tool_schemas()]
        markers = ("Sub-task", "Project layout", "Overall task",
                   "Results so far", "Current sub-task", "📋")
        texts = [(k, t) for k, t in events if k == "text"]

        assert texts, "the run produced no text events at all"
        for _kind, text in texts:
            assert str(tmp_path) not in text
            for marker in markers:
                assert marker not in text, marker
            for payload in plan_payloads:
                assert payload not in text
            for name in tool_names:
                assert name not in text
            for title in ("Inspect main", "Bump the value"):
                assert title not in text

        assert str(tmp_path) not in result
        for marker in markers:
            assert marker not in result, marker
        for payload in plan_payloads:
            assert payload not in result
        for name in tool_names:
            assert name not in result
        assert result == "All done: x is now 2."


class TestTrivialRequestSkipsPlanner:
    """b) 'hi' never reaches the planner."""

    def test_hi_makes_no_planner_call(self, tmp_path, monkeypatch):
        from core.agent import PLANNING_PROMPT, _is_trivial_request

        assert _is_trivial_request("hi") is True

        fake, events, result = _run(
            tmp_path, monkeypatch,
            [{"content": "Hello — what should we work on?"}],
            "hi",
        )

        assert result == "Hello — what should we work on?"
        assert fake.system_contents  # there was a (sub-task) call
        for system in fake.system_contents:
            assert system != PLANNING_PROMPT
        # every call for 'hi' offered tools → none was the tools-free planner
        assert fake.calls and all(tools for tools, _ in fake.calls)

    def test_non_trivial_request_does_call_planner(self, tmp_path, monkeypatch):
        from core.agent import PLANNING_PROMPT

        fake, _events, _result = _run(
            tmp_path, monkeypatch, _multi_step_script(),
            "update main.py so x becomes 2",
        )
        assert any(system == PLANNING_PROMPT for system in fake.system_contents)


class TestSingleSharedRule:
    """c) one NO_VOLUNTEER_RULE, stamped into every prompt."""

    RULE_SOURCES = ("core/agents.py", "core/agent.py")
    OPENING = "Never mention or describe your own setup"

    def _source(self, rel: str) -> str:
        base = os.path.join(os.path.dirname(__file__), "..")
        with open(os.path.join(base, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_rule_in_every_agent_prompt_and_planner_and_synthesis(self):
        from core import agent as agent_mod
        from core.agents import NO_VOLUNTEER_RULE, all_agents

        for agent_id in ("build", "coder", "reviewer", "explore", "general"):
            prompt = all_agents()[agent_id].system_prompt
            assert NO_VOLUNTEER_RULE in prompt, agent_id

        assert NO_VOLUNTEER_RULE in agent_mod.PLANNING_PROMPT
        assert NO_VOLUNTEER_RULE in agent_mod.SYNTHESIS_PROMPT

    def test_agent_module_imports_the_single_constant(self):
        from core import agent as agent_mod
        from core.agents import NO_VOLUNTEER_RULE

        assert agent_mod.NO_VOLUNTEER_RULE is NO_VOLUNTEER_RULE

    def test_rule_text_defined_exactly_once_in_source(self):
        agents_src = self._source("core/agents.py")
        agent_src = self._source("core/agent.py")
        assert agents_src.count(self.OPENING) == 1
        assert agent_src.count(self.OPENING) == 0

    def test_rule_present_in_the_live_planner_and_synthesis_messages(
        self, tmp_path, monkeypatch
    ):
        from core.agent import PLANNING_PROMPT, SYNTHESIS_PROMPT
        from core.agents import NO_VOLUNTEER_RULE

        fake, _events, _result = _run(
            tmp_path, monkeypatch, _multi_step_script(),
            "update main.py so x becomes 2",
        )
        systems = fake.system_contents
        # the planner call went out with the shared rule…
        assert PLANNING_PROMPT in systems
        assert NO_VOLUNTEER_RULE in PLANNING_PROMPT
        # …and so did the synthesis step
        assert SYNTHESIS_PROMPT in systems
        assert NO_VOLUNTEER_RULE in SYNTHESIS_PROMPT


class TestAboutTool:
    """d) `about` reports real sources, with no *_error keys."""

    def _about(self, workdir: str) -> dict:
        from core.tools import _tool_about
        return json.loads(_tool_about({}, workdir))

    def test_no_error_keys_and_core_commands_listed(self, tmp_path):
        info = self._about(str(tmp_path))
        error_keys = [k for k in info if k.endswith("_error")]
        assert error_keys == [], error_keys
        assert "about" in info["slash_commands"]
        assert "status" in info["slash_commands"]

    def test_agents_reported_with_names_and_descriptions(self, tmp_path):
        from core.agents import all_agents

        info = self._about(str(tmp_path))
        specs = all_agents()
        assert set(info["agents"]) == set(specs)
        for agent_id, entry in info["agents"].items():
            assert entry["name"] == specs[agent_id].name
            assert entry["description"] == specs[agent_id].description

    def test_skills_comes_from_real_discovery(self, tmp_path):
        info = self._about(str(tmp_path))
        assert info["skills"] == {}

        skill_md = tmp_path / "skills" / "demo" / "SKILL.md"
        skill_md.parent.mkdir(parents=True)
        skill_md.write_text("# Demo\nDo the thing.\n", encoding="utf-8")

        info = self._about(str(tmp_path))
        assert "demo" in info["skills"]
        assert not [k for k in info if k.endswith("_error")]

    def test_permission_rules_match_the_approver_loader(self, tmp_path):
        from modules.agent_mode import _load_permission_rules

        info = self._about(str(tmp_path))
        assert isinstance(info["permission_rules"], dict)
        assert info["permission_rules"] == _load_permission_rules()

    def test_mcp_servers_match_configured_servers(self, tmp_path):
        from core.mcp import _configured_servers

        info = self._about(str(tmp_path))
        assert info["mcp_servers"] == sorted(_configured_servers())


class TestEventKinds:
    """e) status/progress never arrive as `text`; the answer shows once."""

    def test_status_and_progress_are_not_text_and_answer_appears_once(
        self, tmp_path, monkeypatch
    ):
        fake, events, result = _run(
            tmp_path, monkeypatch,
            [
                {"content": json.dumps({"subtasks": [
                    {"title": "Inspect", "goal": "read main.py"},
                ]})},
                _tool_message("read_file", {"path": "main.py"}),
                {"content": "the answer is 1"},
            ],
            "read main.py and report the value",
        )

        kinds = [k for k, _ in events]
        assert "status" in kinds
        assert "progress" in kinds

        texts = [t for k, t in events if k == "text"]
        assert len(texts) == 1, texts
        assert texts[0] == "the answer is 1"
        assert result == "the answer is 1"

        non_text_payloads = [t for k, t in events if k != "text"]
        for payload in texts:
            assert payload not in non_text_payloads
        for kind, payload in events:
            if kind in ("status", "progress"):
                assert payload
                assert payload not in texts

    def test_final_answer_delivered_exactly_once_for_multi_step_run(
        self, tmp_path, monkeypatch
    ):
        _fake, events, result = _run(
            tmp_path, monkeypatch, _multi_step_script(),
            "update main.py so x becomes 2",
        )
        texts = [t for k, t in events if k == "text"]
        # the answer reaches the caller exactly once: as the return value
        # (multi-step runs synthesise it after the sub-task texts)
        delivered = texts + [result]
        assert delivered.count(result) == 1
        assert len(texts) == len(set(texts))
