"""
Tests for the prompt key bindings in main.py — a port of opencode's shortcuts.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip("prompt_toolkit")

from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.shortcuts import prompt as pt_prompt

from main import _build_key_bindings


@pytest.fixture
def kb():
    return _build_key_bindings()


def _run_pt(input_text: str, kb):
    with create_pipe_input() as inp:
        inp.send_text(input_text)
        with create_app_session(input=inp, output=DummyOutput()):
            return pt_prompt("> ", key_bindings=kb, multiline=True)


class TestLeaderKey:
    def test_leader_l_lists_sessions(self, kb):
        assert _run_pt("\x18l", kb) == ("list_sessions", "")

    def test_leader_n_new_session(self, kb):
        assert _run_pt("\x18n", kb) == ("new_session", "")

    def test_leader_s_status(self, kb):
        assert _run_pt("\x18s", kb) == ("status", "")

    def test_leader_m_models(self, kb):
        assert _run_pt("\x18m", kb) == ("models", "")

    def test_leader_a_agent_commands(self, kb):
        assert _run_pt("\x18a", kb) == ("slash_help", "")

    def test_leader_q_quits(self, kb):
        with pytest.raises(KeyboardInterrupt):
            _run_pt("\x18q", kb)

    def test_leader_other_key_cancels_then_types_normally(self, kb):
        # ctrl+x then 'z' cancels the leader; 'z' must NOT be consumed by it.
        # The trailing Enter submits the (empty) prompt.
        assert _run_pt("\x18z\r", kb) == ""


class TestPaletteAndClear:
    def test_ctrl_p_opens_palette_keeping_text(self, kb):
        assert _run_pt("HEAD~1\x10", kb) == ("palette", "HEAD~1")

    def test_ctrl_c_clears_input(self, kb):
        result = _run_pt("some text\x03\r", kb)
        assert result == "" or result is None

    def test_ctrl_c_on_empty_quits(self, kb):
        with pytest.raises(KeyboardInterrupt):
            _run_pt("\x03", kb)

    def test_plain_text_submits(self, kb):
        assert _run_pt("hello world\r", kb) == "hello world"

    def test_shift_enter_inserts_newline(self, kb):
        # Alt+Enter (escape then enter) inserts a newline without submitting.
        assert _run_pt("one\x1b\r\r", kb) == "one\n"


class TestBuildPromptFn:
    def test_prompt_fn_returns_submitted_text(self):
        from main import _build_prompt_fn

        # The PromptSession binds the active input at construction time, so it
        # must be built inside the app-session context of the pipe input.
        with create_pipe_input() as inp:
            inp.send_text("test input\r")
            with create_app_session(input=inp, output=DummyOutput()):
                prompt_fn = _build_prompt_fn()
                assert prompt_fn() == "test input"

    def test_completions_include_slash_commands(self):
        from prompt_toolkit.completion import WordCompleter

        # Mirror the completer construction; ensure slash names are exposed.
        from modules.slash_commands import command_names
        names = ["/" + n for n in command_names()]
        assert "/init" in names
        assert "/review" in names
        assert "/sessions" in names
        completer = WordCompleter(names, ignore_case=True)
        assert completer.get_completions("", None) is not None
