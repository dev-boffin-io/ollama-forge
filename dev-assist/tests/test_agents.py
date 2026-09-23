"""
Tests for core/agents.py — the automatic agent router & compaction.

Routing is deterministic (never calls the model), so these tests assert
classification purely from text + settings. Compaction is exercised with a
scripted fake provider.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import agents


class FakeModel:
    """Drop-in stand-in for a provider; returns scripted dict messages."""

    def __init__(self, script: list[dict]) -> None:
        self._script = list(script)
        self.calls: list[list[dict]] = []

    def chat(self, messages, *, model=None, tools=None):
        self.calls.append(list(messages))
        return self._script.pop(0)


class TestResolve:
    def test_unknown_falls_back_to_build(self):
        assert agents.resolve("no-such-agent").id == "build"

    def test_builtins_resolvable(self):
        for name in ("build", "coder", "reviewer", "explore", "general"):
            assert agents.resolve(name).id == name

    def test_hidden_compaction_not_resolvable_as_target(self):
        assert agents.resolve("compaction").id == "build"

    def test_agent_spec_passthrough(self):
        spec = agents.resolve("coder")
        assert agents.resolve(spec).id == "coder"

    def test_readonly_flags(self):
        assert agents.resolve("reviewer").read_only is True
        assert agents.resolve("reviewer").uses_planning is False
        assert agents.resolve("build").read_only is False

    def test_custom_agent_registered_and_routable(self, monkeypatch):
        from core import config as core_config
        monkeypatch.setattr(core_config, "load_config", lambda: {
            "agents": {
                "qa": {
                    "description": "Test-only QA agent.",
                    "system_prompt": "You are QA. {workdir}",
                    "read_only": True,
                    "uses_planning": False,
                }
            },
            "routing": {"enabled": True, "default_agent": "qa"},
        })
        spec = agents.resolve("qa")
        assert spec.id == "qa"
        assert "QA" in spec.system_prompt
        assert spec.read_only is True
        assert "qa" in agents.routable_agents()

    def test_custom_override_of_builtin(self, monkeypatch):
        from core import config as core_config
        monkeypatch.setattr(core_config, "load_config", lambda: {
            "agents": {"coder": {"description": "My custom coder"}},
            "routing": {"enabled": True},
        })
        assert agents.resolve("coder").description == "My custom coder"
        assert agents.resolve("coder").system_prompt  # inherited from builtin


class TestRoute:
    def _route(self, task, **kw):
        context = kw.pop("context", "")
        forced = kw.pop("forced", None)
        return agents.route(
            task,
            context=context,
            forced=forced,
            settings={"enabled": True, "default_agent": "build", **kw},
        )

    def test_review_intent(self):
        for task in ("review the diff", "review my code", "review the pull request",
                     "code review this change", "find bugs in utils.py"):
            assert self._route(task).agent_id == "reviewer", task

    def test_coder_intent(self):
        for task in ("refactor the config parser", "implement a merge sort",
                     "fix the bug in main.py", "fix the failing test", "add tests for the api"):
            assert self._route(task).agent_id == "coder", task

    def test_explore_intent(self):
        for task in ("where is the config file?", "how does the router work",
                     "which file defines the toolbar?", "find the entrypoint"):
            assert self._route(task).agent_id == "explore", task

    def test_ambiguous_defaults_to_build(self):
        decision = self._route("hello there")
        assert decision.agent_id == "build"
        assert decision.confidence < 0.5

    def test_leading_review_explicit(self):
        assert self._route("review: the api handlers").agent_id == "reviewer"

    def test_leading_explore_explicit(self):
        assert self._route("explore: the auth flow").agent_id == "explore"

    def test_forced_agent_wins(self):
        decision = self._route("review the diff", forced="coder")
        assert decision.agent_id == "coder"
        assert decision.forced is True

    def test_disabled_routing_uses_default(self):
        decision = agents.route("review the diff", settings={"enabled": False, "default_agent": "coder"})
        assert decision.agent_id == "coder"
        assert decision.confidence == 0.0

    def test_reviewer_beats_coder_on_tie(self):
        # "review" and "fix the bug" both appear; reviewer outranks coder.
        decision = self._route("fix the bug and review it")
        assert decision.agent_id == "reviewer"

    def test_context_boosts_intent(self):
        decision = self._route("anything at all", context="review the latest change carefully")
        assert decision.agent_id == "reviewer"


class TestDescribe:
    def test_describes_routable_agents(self):
        text = agents.describe()
        for name in ("build", "coder", "reviewer", "explore"):
            assert name in text
        assert "compaction" not in text  # hidden agents are not listed


class TestCompaction:
    def test_should_compact_above_threshold(self):
        msgs = [
            {"role": "user", "content": "a" * 30000},
            {"role": "assistant", "content": "b" * 30000},
            {"role": "user", "content": "and then..."},
        ]
        assert agents.should_compact(msgs, 50000) is True

    def test_should_compact_below_threshold(self):
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
        assert agents.should_compact(msgs, 1000) is False

    def test_should_compact_needs_two_users(self):
        msgs = [{"role": "user", "content": "x" * 10000}]
        assert agents.should_compact(msgs, 50) is False

    def test_compact_context_returns_summary(self):
        fake = FakeModel([{"content": "SUMMARIZED CONTEXT"}])
        out = agents.compact_context("user: lots of history\nassistant: more", provider=fake)
        assert "SUMMARIZED CONTEXT" in out
        assert out.count("SUMMARIZED CONTEXT") == 1

    def test_compact_context_chunks_progressively(self):
        fake = FakeModel([
            {"content": "PART1"},
            {"content": "PART2"},
        ])
        # ~2.6k chars → two 2k chunks → exactly two provider calls.
        long = "user: plan the migration\nassistant: " + "ab" * 1300
        out = agents.compact_context(long, provider=fake, chunk_chars=2000)
        assert "PART1" in out
        assert "PART2" in out
        assert len(fake.calls) == 2
        # the second call must carry the prior summary forward
        second = fake.calls[1]
        user_text = " ".join(
            m["content"] for m in second if isinstance(m, dict) and m.get("role") == "user"
        )
        assert "Prior summary" in user_text

    def test_compact_context_never_raises_on_failure(self):
        class Broken:
            def chat(self, messages, *, model=None, tools=None):
                raise RuntimeError("boom")
        out = agents.compact_context("user: text\nassistant: more", provider=Broken())
        assert "Compacted conversation (excerpt)" in out

    def test_msgs_text_renders_roles(self):
        out = agents._msgs_text([{"role": "user", "content": "ask"},
                                 {"role": "assistant", "content": "answer"}])
        assert out == "user: ask\n\nassistant: answer"

    def test_empty_transcript(self):
        assert agents.compact_context("  ", provider=FakeModel([])) == ""
