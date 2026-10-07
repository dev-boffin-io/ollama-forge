"""
GUI regression tests — directory-first flow, agent toggle, AgentWorker wiring.

Everything runs offscreen with a fake dev-assist bridge: no Ollama server, no
network, no dialogs (the directory picker is stubbed per test).
"""

import json
import os
import sys
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

_GUI_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, _GUI_DIR)
import agent_bridge  # noqa: E402  (puts dev-assist/ first)

sys.path.insert(0, _GUI_DIR)             # gui/ back on top of dev-assist/

# Load gui/main.py under its own name: dev-assist tests already occupy the
# bare module name "main" in sys.modules when the whole suite runs together.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "ollama_gui_main", os.path.join(_GUI_DIR, "main.py"))
main_mod = importlib.util.module_from_spec(_spec)
sys.modules["ollama_gui_main"] = main_mod
_spec.loader.exec_module(main_mod)       # noqa: E402

from PyQt6.QtCore import QThread, pyqtSignal  # noqa: E402
from PyQt6.QtWidgets import QApplication, QFileDialog  # noqa: E402

# ── helpers ──────────────────────────────────────────────────────────────────
_APP = None          # QApplication must outlive every window we build


def _qapp():
    """Create the application once and keep a hard reference to it."""
    global _APP
    app = QApplication.instance()
    if app is None:
        _APP = QApplication([])
        app = _APP
    return app


@pytest.fixture(scope="session")
def qapp():
    return _qapp()


def _pump_until(cond, timeout=10.0):
    """Process Qt events until cond() is true (queued worker signals need this)."""
    app = _qapp()
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    app.processEvents()
    return bool(cond())


def _build_gui(tmp_path, monkeypatch, *, dialog="", settings=None):
    """Construct an isolated OllamaGUI (own HOME, own settings, stubbed net)."""
    home = tmp_path / "home"
    (home / ".ollama_gui").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))

    settings_file = home / ".ollama_gui" / "settings.json"
    monkeypatch.setattr(main_mod, "_CONFIG_DIR", str(home / ".ollama_gui"))
    monkeypatch.setattr(main_mod, "_SETTINGS_FILE", str(settings_file))
    if settings is not None:
        settings_file.write_text(json.dumps(settings), encoding="utf-8")

    # No network in tests: Ollama answers "running, no models".
    monkeypatch.setattr(main_mod.OllamaClient, "list_models", lambda self: [])
    monkeypatch.setattr(main_mod.OllamaClient, "is_running", lambda self: True)

    if "rag_engine" in sys.modules:
        monkeypatch.setattr(sys.modules["rag_engine"], "_PERSIST",
                            str(home / ".ollama_gui" / "rag"))

    # The launch/selection dialog must never block a test.
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *args, **kwargs: dialog))

    _qapp()
    win = main_mod.OllamaGUI()
    win._poll_timer.stop()
    return win


def _status_texts(win):
    return [str(m.get("content", "")) for m in win._chat_log
            if m.get("type") == "status"]


def _ai_contents(win):
    return [str(m.get("content", "")) for m in win._chat_log
            if m.get("type") == "ai"]


@pytest.fixture
def gui(tmp_path, monkeypatch):
    win = _build_gui(tmp_path, monkeypatch)
    yield win
    try:
        win._cleanup_thread()
    except Exception:
        pass
    win.close()
    win.deleteLater()
    _qapp().processEvents()


@pytest.fixture(autouse=True)
def _fake_bridge(monkeypatch):
    """Every test gets a controllable bridge unless it patches these itself."""
    monkeypatch.setattr(agent_bridge, "is_available", lambda: True)
    monkeypatch.setattr(agent_bridge, "get_error", lambda: None)
    yield


# ── 1. construction + directory-first gating ─────────────────────────────────
def test_we_imported_the_gui_main_module():
    assert os.path.basename(os.path.dirname(main_mod.__file__)) == "gui"
    assert hasattr(main_mod, "OllamaGUI")


def test_window_constructs_with_input_disabled_without_dir(gui):
    assert gui.input.isEnabled() is False
    assert gui.send_btn.isEnabled() is False
    assert "No directory" in gui.dir_label.text()
    assert "Pick a working directory" in gui.input.placeholderText()


def test_choosing_a_dir_enables_input_and_persists(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir()
    win = _build_gui(tmp_path, monkeypatch, dialog=str(d))
    try:
        assert win.agent_workdir == str(d)
        assert win.input.isEnabled() is True
        assert win.send_btn.isEnabled() is True
        assert str(d) in win.dir_label.text()

        saved = json.loads(open(main_mod._SETTINGS_FILE, encoding="utf-8").read())
        assert saved["workdir"] == str(d)
    finally:
        win.close()
        win.deleteLater()
        _qapp().processEvents()


def test_select_workdir_slot_is_the_only_picker(gui, monkeypatch, tmp_path):
    d = tmp_path / "other"
    d.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(d)))
    gui._select_workdir()
    assert gui.agent_workdir == str(d)
    assert gui.input.isEnabled()
    # one slot, wired to the button
    assert gui.open_dir_btn.receivers(gui.open_dir_btn.clicked) >= 1


def test_saved_settings_restore_the_dir(tmp_path, monkeypatch):
    d = tmp_path / "saved"
    d.mkdir()
    win = _build_gui(tmp_path, monkeypatch, settings={"workdir": str(d)})
    try:
        assert win.agent_workdir == str(d)
        assert win.input.isEnabled() is True
        assert str(d) in win.dir_label.text()
    finally:
        win.close()
        win.deleteLater()
        _qapp().processEvents()


def test_deleted_saved_dir_is_rejected(tmp_path, monkeypatch):
    d = tmp_path / "gone"
    d.mkdir()
    win = _build_gui(tmp_path, monkeypatch, settings={"workdir": str(d)})
    try:
        assert win.agent_workdir == str(d)
        # directory disappears, settings are re-loaded at runtime
        os.rmdir(d)
        win.agent_workdir = None
        win._load_settings()
        assert win.agent_workdir is None
        assert win.input.isEnabled() is False
        assert "No directory" in win.dir_label.text()
    finally:
        win.close()
        win.deleteLater()
        _qapp().processEvents()


def test_no_saved_dir_opens_the_launch_dialog(tmp_path, monkeypatch):
    d = tmp_path / "picked"
    d.mkdir()
    win = _build_gui(tmp_path, monkeypatch, dialog=str(d))
    try:
        assert win.agent_workdir == str(d)
        assert win.input.isEnabled() is True
    finally:
        win.close()
        win.deleteLater()
        _qapp().processEvents()


def test_dir_label_and_input_state_called_at_startup(gui):
    # both helpers exist and are safe to call at any time
    gui._update_dir_label()
    gui._update_input_state()
    assert gui.input.isEnabled() is False


def test_send_without_a_directory_is_refused(gui):
    gui.input.setPlainText("hello")
    gui._send()

    assert gui.current_conv_id is None          # no conversation was created
    assert gui.thread is None                   # no worker was started
    assert _ai_contents(gui) == []              # no reply bubble
    assert not any(m.get("type") == "user" for m in gui._chat_log)
    assert gui.input.isEnabled() is False
    # the refusal itself is surfaced to the user
    assert any("Pick a working directory first" in s for s in _status_texts(gui))


# ── 2. agent toggle ──────────────────────────────────────────────────────────
def test_toggle_agent_mode_on_and_off(gui):
    gui._toggle_agent_mode()
    assert gui.agent_mode is True
    assert "ON" in gui.agent_btn.text()

    gui._toggle_agent_mode()
    assert gui.agent_mode is False
    assert "OFF" in gui.agent_btn.text()


def test_toggle_keeps_off_when_bridge_unavailable(gui, monkeypatch):
    monkeypatch.setattr(agent_bridge, "is_available", lambda: False)
    monkeypatch.setattr(agent_bridge, "get_error", lambda: "No module named 'core'")

    gui._toggle_agent_mode()

    assert gui.agent_mode is False
    assert "OFF" in gui.agent_btn.text()
    assert any("No module named 'core'" in s for s in _status_texts(gui))


def test_toggle_survives_bridge_import_failure(gui, monkeypatch):
    monkeypatch.setitem(sys.modules, "agent_bridge", None)  # import blows up

    gui._toggle_agent_mode()

    assert gui.agent_mode is False
    assert "OFF" in gui.agent_btn.text()
    assert any("Agent unavailable" in s for s in _status_texts(gui))


# ── 3. AgentWorker ───────────────────────────────────────────────────────────
def _fake_worker_bridge(monkeypatch, *, events, results, errors, block=0.0):
    def fake_run_agent(task, *, workdir, approver=None, on_event=None,
                       max_steps=24, agent="build", extra_context=""):
        # the worker's approver must gate on the real destructive-tool list
        assert approver("read_file", {}) is True
        assert approver("list_dir", {}) is True
        assert approver("bash", {"command": "rm -rf /"}) is False
        assert approver("edit_file", {"path": "x"}) is False
        for kind, text in events:
            on_event(kind, text)
        if block:
            time.sleep(block)
        return results[0]

    monkeypatch.setattr(agent_bridge, "run_agent", fake_run_agent)


def test_agent_worker_emits_events_and_final_result(monkeypatch, qapp):
    from workers import AgentWorker

    got_events, got_done, got_failed = [], [], []
    _fake_worker_bridge(
        monkeypatch,
        events=[("route", "build · default"), ("text", "working…"),
                ("status", "✓ done")],
        results=["the final answer"],
        errors=[],
    )

    w = AgentWorker("do something", "/tmp", agent_name="build")
    # the fake runs read-only + destructive probes; auto-deny any prompt so
    # the destructive assertions below still hold (no real user present)
    w.approval_requested.connect(lambda n, j: w.provide_approval(False))
    w.event.connect(lambda k, t: got_events.append((k, t)))
    w.done.connect(lambda s: got_done.append(s))
    w.failed.connect(lambda e: got_failed.append(e))
    w.start()

    assert _pump_until(lambda: bool(got_done or got_failed)), "worker never finished"
    w.wait(3000)

    assert got_failed == []
    assert got_done == ["the final answer"]
    assert ("route", "build · default") in got_events
    assert ("text", "working…") in got_events
    assert ("status", "✓ done") in got_events


def test_agent_worker_reports_failure(monkeypatch, qapp):
    from workers import AgentWorker

    got_done, got_failed = [], []

    def exploding(task, **kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(agent_bridge, "run_agent", exploding)

    w = AgentWorker("boom", "/tmp")
    w.done.connect(got_done.append)
    w.failed.connect(got_failed.append)
    w.start()

    assert _pump_until(lambda: bool(got_done or got_failed))
    w.wait(3000)
    assert got_done == []
    assert got_failed and "model exploded" in got_failed[0]


def test_agent_worker_signal_is_not_qthread_finished():
    from workers import AgentWorker

    assert hasattr(AgentWorker, "done")
    # the result signal must not shadow QThread's own lifecycle signal
    assert "finished" not in vars(AgentWorker)
    assert issubclass(AgentWorker, QThread)


# ── 4. agent replies in the window ───────────────────────────────────────────
def test_agent_text_and_answer_share_the_bubble(gui, monkeypatch, tmp_path):
    # the final answer comes from the return value, not from an event
    script = [
        ("route", "build · default"),
        ("plan", "📋 Plan:\n  1. Greet"),
        ("progress", "▶ Sub-task 1/1: Greet"),
        ("tool", "read_file(path=main.py)"),
        ("result", "x = 1"),
        ("text", "Hello from the agent"),
        ("status", "✓ All sub-tasks complete — writing final answer."),
    ]

    def fake_run_agent(task, *, workdir, on_event=None, **kw):
        for kind, text in script:
            on_event(kind, text)
        return "Hello from the agent"  # SAME as text event (real case)

    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(d)))
    gui._select_workdir()
    monkeypatch.setattr(agent_bridge, "run_agent", fake_run_agent)

    gui._toggle_agent_mode()
    gui.input.setPlainText("hi")
    gui._send()
    assert _pump_until(lambda: gui.thread is None), "agent run never finished"

    bubbles = _ai_contents(gui)
    assert len(bubbles) == 1
    answer = bubbles[0]
    assert answer == "Hello from the agent"  # appears exactly once
    assert answer.count("Hello from the agent") == 1

    # every other agent event lives in the collapsible think buffer
    think = " | ".join(m["text"] for m in gui._think)
    kinds = [m["kind"] for m in gui._think]
    for kind in ("route", "plan", "progress", "tool", "result", "status"):
        assert kind in kinds, kind
    assert "Hello from the agent" not in think
    assert not any("Hello from the agent" in s for s in _status_texts(gui))

    # persisted like any other reply — assistant content is the answer
    rows = gui.db.get_messages(gui.current_conv_id)
    assert rows[-1]["role"] == "assistant"
    assert rows[-1]["content"] == answer

    # stop button back to idle
    assert gui.stop_btn.text() == "🔄 Reload"
    assert gui._is_streaming is False


def test_agent_multi_subtask_final_added_once(gui, monkeypatch, tmp_path):
    # several sub-tasks emit their own text; the returned final answer differs
    def fake_run_agent(task, *, workdir, on_event=None, **kw):
        on_event("text", "sub-task 1 finding")
        on_event("text", "sub-task 2 finding")
        return "final synthesized answer"

    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(d)))
    gui._select_workdir()
    monkeypatch.setattr(agent_bridge, "run_agent", fake_run_agent)

    gui._toggle_agent_mode()
    gui.input.setPlainText("go")
    gui._send()
    assert _pump_until(lambda: gui.thread is None)

    answer = _ai_contents(gui)[0]
    assert answer.count("final synthesized answer") == 1
    assert "sub-task 1 finding" in answer
    assert "sub-task 2 finding" in answer
    assert answer.endswith("final synthesized answer")

    rows = gui.db.get_messages(gui.current_conv_id)
    assert rows[-1]["content"] == answer
    assert rows[-1]["content"].count("final synthesized answer") == 1


def test_agent_failure_is_logged_and_thread_cleaned(gui, monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("bridge exploded")

    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(d)))
    gui._select_workdir()
    monkeypatch.setattr(agent_bridge, "run_agent", boom)

    gui._toggle_agent_mode()
    gui.input.setPlainText("hi")
    gui._send()
    assert _pump_until(lambda: gui.thread is None)

    assert any("bridge exploded" in s for s in _status_texts(gui))
    assert gui._is_streaming is False
    assert gui.stop_btn.text() == "🔄 Reload"


def test_stop_interrupts_the_agent_run(gui, monkeypatch, tmp_path):
    started = threading.Event()

    def slow_run_agent(task, *, workdir, on_event=None, **kw):
        started.set()
        time.sleep(0.6)                    # still running when Stop is hit
        on_event("text", "late text")
        return "late answer"

    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(d)))
    gui._select_workdir()
    monkeypatch.setattr(agent_bridge, "run_agent", slow_run_agent)

    gui._toggle_agent_mode()
    gui.input.setPlainText("please work")
    gui._send()
    assert started.wait(5), "agent never started"

    gui._stop_or_reload()                  # Stop

    assert gui.thread is None
    assert gui._is_streaming is False
    assert gui.stop_btn.text() == "🔄 Reload"
    assert not any("late answer" in c or "late text" in c
                   for c in _ai_contents(gui))


# ── 5. agent OFF keeps the normal chat path ──────────────────────────────────
class _FakeSmartWorker(QThread):
    token   = pyqtSignal(str)
    finished = pyqtSignal(str, float, int)
    error   = pyqtSignal(str)
    status  = pyqtSignal(str)

    def __init__(self, **kwargs):
        super().__init__()
        self.kwargs = kwargs
        self.stopped = False

    def run(self):
        self.token.emit("plain ")
        self.token.emit("reply")
        self.finished.emit("plain reply", 1.0, 2)

    def stop(self):
        self.stopped = True


def test_agent_off_uses_the_normal_chat_worker(gui, monkeypatch, tmp_path):
    monkeypatch.setattr(main_mod, "SmartChatWorker", _FakeSmartWorker)

    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(d)))
    gui._select_workdir()

    assert gui.agent_mode is False        # Agent stays OFF
    gui.input.setPlainText("hello there")
    gui._send()

    assert _pump_until(lambda: gui.thread is None)
    assert isinstance(gui._chat_log[-1], dict)
    assert _ai_contents(gui) == ["plain reply"]
    assert gui.agent_mode is False


# ── 6. approvals + undo ───────────────────────────────────────────────────────
def _deny_prompt(approvals):
    return lambda name, args: (approvals.append(name) or
                               {"approve": False, "reason": "too risky"})


def test_approver_prompts_for_destructive_but_not_readonly(monkeypatch):
    asked = []
    approver = agent_bridge.make_approver(
        "/tmp", lambda n, a: asked.append(n) or {"approve": True})
    assert approver("read_file", {"path": "x"}) is True      # no prompt
    assert approver("write_file", {"path": "x", "content": "y"}) is True
    assert asked == ["write_file"]


def test_approver_deny_sets_last_reason():
    approver = agent_bridge.make_approver(
        "/tmp", _deny_prompt([]))
    assert approver("edit_file", {"path": "x"}) is False
    assert approver.last_reason == "too risky"
    assert approver("read_file", {"path": "x"}) is True      # last_reason reset
    assert approver.last_reason is None


def test_approver_always_skips_the_second_prompt():
    asked = []

    def ask(name, args):
        asked.append(name)
        return {"approve": True, "always": True}

    approver = agent_bridge.make_approver("/tmp", ask)
    assert approver("bash", {"command": "rm -rf /"}) is True    # first: prompt
    assert approver("bash", {"command": "rm -rf /"}) is True    # always: no prompt
    assert asked == ["bash"]                                    # only once


def test_approver_rule_deny_never_prompts():
    asked = []
    approver = agent_bridge.make_approver("/tmp", _deny_prompt(asked),
                                          rules={"bash": "deny"})
    assert approver("bash", {"command": "git reset --hard"}) is False
    assert asked == []
    assert approver.last_reason == \
        "bash is blocked by your permission rules"


def test_worker_blocks_on_approval_and_unblocks(monkeypatch, qapp):
    from workers import AgentWorker

    decisions = []

    def fake_run_agent(task, *, workdir, approver=None, on_event=None, **kw):
        decisions.append(approver("write_file",
                                  {"path": "n.txt", "content": "hi"}))
        return "approved run done"

    monkeypatch.setattr(agent_bridge, "run_agent", fake_run_agent)

    got_done, got_failed, approvals = [], [], []
    w = AgentWorker("task", "/tmp")
    w.approval_requested.connect(
        lambda n, j: approvals.append((n, json.loads(j)))
        or w.provide_approval(True))
    w.done.connect(got_done.append)
    w.failed.connect(got_failed.append)
    w.start()

    assert _pump_until(lambda: bool(got_done or got_failed))
    w.wait(3000)
    assert approvals == [("write_file", {"path": "n.txt", "content": "hi"})]
    assert decisions == [True]
    assert got_failed == []
    assert got_done == ["approved run done"]


def test_worker_rule_deny_never_emits_approval(monkeypatch, qapp):
    from workers import AgentWorker

    decisions, approvals = [], []

    def fake_run_agent(task, *, workdir, approver=None, on_event=None, **kw):
        decisions.append(approver("bash", {"command": "rm -rf /"}))
        return "denied cleanly"

    monkeypatch.setattr(agent_bridge, "run_agent", fake_run_agent)

    got_done = []
    w = AgentWorker("task", "/tmp", rules={"bash": "deny"})
    w.approval_requested.connect(
        lambda n, j: approvals.append(n) or w.provide_approval(True))
    w.done.connect(got_done.append)
    w.start()

    assert _pump_until(lambda: got_done)
    w.wait(3000)
    assert decisions == [False]
    assert approvals == []           # blocked by rules → no dialog
    assert got_done == ["denied cleanly"]


def test_stop_unblocks_a_pending_approval(monkeypatch, qapp):
    from workers import AgentWorker

    def fake_run_agent(task, *, workdir, approver=None, on_event=None, **kw):
        approver("bash", {"command": "sleep 999"})
        return "should not surface"

    monkeypatch.setattr(agent_bridge, "run_agent", fake_run_agent)

    got_done, approvals = [], []
    w = AgentWorker("task", "/tmp")
    w.approval_requested.connect(lambda n, j: approvals.append(n))
    w.done.connect(got_done.append)
    w.start()

    assert _pump_until(lambda: bool(approvals)), "approval was never requested"
    w.stop()                                     # Stop while worker is blocked
    assert _pump_until(lambda: w.isFinished(), timeout=5)
    # clean exit: no result emitted, no hang, no exception
    assert w.wait(3000)
    assert got_done == []


def test_undo_restores_changed_files(tmp_path):
    from core import change_tracker
    p = tmp_path / "notes.txt"
    p.write_text("one", encoding="utf-8")

    change_tracker.new_run()
    t = change_tracker.get_tracker()
    t.snapshot(str(p))
    p.write_text("two", encoding="utf-8")
    t.record(str(p), "two")          # the real write_file/edit_file path
    assert p.read_text() == "two"

    restored = agent_bridge.undo_changes()
    assert restored == [str(p)]
    assert p.read_text() == "one"


def test_undo_button_state_and_do_undo(gui, monkeypatch):
    calls = []
    monkeypatch.setattr(agent_bridge, "has_changes", lambda: True)
    monkeypatch.setattr(agent_bridge, "diffstat",
                        lambda: "notes.txt | 1 +, 1 -")
    monkeypatch.setattr(agent_bridge, "undo_changes",
                        lambda: (calls.append(1) or ["/tmp/notes.txt"]))

    gui._refresh_agent_changes()
    assert gui.undo_btn.isEnabled() is True
    assert any("📝" in s and "notes.txt" in s for s in _status_texts(gui))

    gui._do_undo()
    assert calls == [1]
    assert any("Undone: 1 file(s) restored" in s for s in _status_texts(gui))


def test_auto_approve_stays_off_unless_confirmed(gui, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    assert gui.auto_approve_chk.isChecked() is False

    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: QMessageBox.StandardButton.No)
    gui.auto_approve_chk.setChecked(True)
    assert gui.auto_approve_chk.isChecked() is False    # declined → back off
    assert gui._agent_auto_approve is False

    monkeypatch.setattr(QMessageBox, "warning",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)
    gui.auto_approve_chk.setChecked(True)
    assert gui.auto_approve_chk.isChecked() is True
    assert gui._agent_auto_approve is True
    assert any("Auto-approve ON" in s for s in _status_texts(gui))


class _FakeApprovalThread:
    def __init__(self, workdir="/tmp"):
        self.workdir = workdir
        self.answers = []

    def provide_approval(self, approve, always=False, reason=""):
        self.answers.append((approve, always, reason))


def test_window_shows_dialog_and_hands_decision_to_worker(tmp_path, monkeypatch):
    win = _build_gui(tmp_path, monkeypatch)
    try:
        fake = _FakeApprovalThread(str(tmp_path / "proj"))
        win.thread = fake

        win._on_approval_requested("write_file",
            json.dumps({"path": "a.txt", "content": "line\n"}))
        assert win._approval_dialog is not None
        assert win._approval_dialog.windowTitle().startswith("Approve: write_file")
        assert any("Approval needed: write_file" in s for s in _status_texts(win))

        win._approval_dialog._deny()
        assert fake.answers == [(False, False, "")]
        assert win._approval_dialog is None
        assert any("Denied write_file" in s for s in _status_texts(win))
        assert win._pending_tool is None
    finally:
        win.thread = None
        win.close()
        win.deleteLater()
        _qapp().processEvents()


# ── 7. think buffer + live agent state ────────────────────────────────────────
def _run_fake_agent(gui, monkeypatch, tmp_path, events, result="done"):
    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(d)))
    gui._select_workdir()

    def fake_run_agent(task, *, workdir, on_event=None, **kw):
        for kind, text in events:
            on_event(kind, text)
        return result

    monkeypatch.setattr(agent_bridge, "run_agent", fake_run_agent)
    if not gui.agent_mode:
        gui._toggle_agent_mode()
    gui.input.setPlainText("go")
    gui._send()


def test_think_buffer_holds_only_real_agent_events(gui, monkeypatch, tmp_path):
    _run_fake_agent(gui, monkeypatch, tmp_path, [
        ("route", "build · default"),
        ("plan", "📋 Plan:\n  do it"),
        ("progress", "▶ Sub-task 1/7: build"),
        ("tool", "edit_file(path=a.py)"),
        ("result", "ok"),
        ("text", "final"),                    # goes to the bubble, NOT think
    ])
    assert _pump_until(lambda: gui.thread is None)

    kinds = [m["kind"] for m in gui._think]
    assert kinds == ["route", "plan", "progress", "tool", "result"]
    assert "final" not in " | ".join(m["text"] for m in gui._think)
    # not persisted: the think buffer is display-only
    rows = gui.db.get_messages(gui.current_conv_id)
    assert not any("[route]" in r["content"] or "Sub-task 1/7" in r["content"]
                   for r in rows)


def test_think_buffer_collapse_and_expand_toggle_anchor(gui, monkeypatch,
                                                        tmp_path):
    from PyQt6.QtCore import QUrl
    events = [("tool", f"read_file(path={i}.py)") for i in range(20)]
    _run_fake_agent(gui, monkeypatch, tmp_path, events)
    assert _pump_until(lambda: gui.thread is None)

    assert len(gui._think) == 20
    assert gui._think_expanded is False
    html = gui.chat.toHtml()
    assert "Agent reasoning · 20 events" in html
    assert "show all" in html                       # truncation marker present

    gui._on_anchor_clicked(QUrl("think-toggle"))    # expand
    assert gui._think_expanded is True
    html_open = gui.chat.toHtml()
    assert "show all" not in html_open
    for i in (0, 19):
        assert f"read_file(path={i}.py)" in gui.chat.toPlainText()

    gui._on_anchor_clicked(QUrl("think-toggle"))    # collapse again
    assert gui._think_expanded is False


def test_think_more_expands_the_buffer(gui, monkeypatch, tmp_path):
    from PyQt6.QtCore import QUrl
    events = [("tool", f"read_file(path={i}.py)") for i in range(12)]
    _run_fake_agent(gui, monkeypatch, tmp_path, events)
    assert _pump_until(lambda: gui.thread is None)

    assert gui._think_expanded is False
    gui._on_anchor_clicked(QUrl("think-more"))
    assert gui._think_expanded is True
    assert "read_file(path=0.py)" in gui.chat.toPlainText()  # oldest shown


def test_agent_state_strip_reflects_real_events(gui, monkeypatch, tmp_path):
    assert gui.agent_state_label.text() == "🤖 · Agent · idle"

    _run_fake_agent(gui, monkeypatch, tmp_path, [
        ("route", "code · research"),
        ("progress", "▶ Sub-task 2/5: research"),
        ("tool", "read_file(path=a.py)"),
        ("warn", "rate limit hit"),
    ])
    assert _pump_until(lambda: gui.thread is None)

    label = gui.agent_state_label.text()
    assert "code · research" in label      # from the real route event
    assert "2/5" in label                  # real step count from progress event
    assert "rate limit hit" in label       # real warning text
    assert "done" in label                 # real completion phase
    assert gui._agent_state["step"] == 2
    assert gui._agent_state["max_steps"] == 5
    assert gui._agent_state["note"] == "rate limit hit"


def test_agent_state_idle_when_toggled_off(gui):
    gui._toggle_agent_mode()          # ON does not fake any state
    gui._agent_state = {"phase": "working", "agent": "build", "step": 3,
                        "max_steps": 5, "tool": "edit_file", "note": ""}
    gui._update_agent_state()
    assert "working" in gui.agent_state_label.text()

    gui._toggle_agent_mode()          # OFF resets the strip to a real idle
    assert gui.agent_state_label.text() == "🤖 · Agent · idle"
    assert gui._agent_state["phase"] == "idle"


def test_think_buffer_cleared_on_new_run_and_clear(gui, monkeypatch, tmp_path):
    _run_fake_agent(gui, monkeypatch, tmp_path,
                    [("tool", "read_file(path=a.py)")])
    assert _pump_until(lambda: gui.thread is None)
    assert len(gui._think) == 1

    _run_fake_agent(gui, monkeypatch, tmp_path,
                    [("progress", "▶ Sub-task 1/2: x")])
    assert _pump_until(lambda: gui.thread is None)
    assert len(gui._think) == 1           # fresh buffer each run

    gui._clear_chat_display()
    assert gui._think == []
    assert gui._think_expanded is False
    assert "Agent reasoning" not in gui.chat.toHtml()


# ── 8. input gating (single _update_input_state) + memory toggle ─────────────
def test_rag_busy_blocks_chat_until_released(gui):
    # no directory → already disabled; RAG busy must keep it that way
    gui._set_rag_ui_busy(True)
    assert gui.input.isEnabled() is False
    assert gui.send_btn.isEnabled() is False
    assert "Indexing knowledge base" in gui.input.placeholderText()
    gui._set_rag_ui_busy(False)
    assert gui.input.isEnabled() is False        # still no directory

    # with a directory: RAG busy overrides the "dir ok AND server ok" state
    gui.agent_workdir = "/tmp"
    gui._update_input_state()
    assert gui.input.isEnabled() is True
    gui._set_rag_ui_busy(True)
    assert gui.input.isEnabled() is False        # busy wins
    assert gui.send_btn.isEnabled() is False
    gui._set_rag_ui_busy(False)                  # back to enabled
    assert gui.input.isEnabled() is True
    assert gui.send_btn.isEnabled() is True


def test_server_state_change_routes_through_single_gate(gui):
    gui.agent_workdir = "/tmp"
    gui._update_input_state()
    assert gui.input.isEnabled() is True

    gui._set_ui_server_state(False)              # server went away
    assert gui.input.isEnabled() is False
    assert gui.send_btn.isEnabled() is False
    assert "server stopped" in gui.input.placeholderText().lower()

    gui._set_ui_server_state(True)               # server back
    assert gui.input.isEnabled() is True
    assert gui.send_btn.isEnabled() is True


def test_streaming_disables_input_and_done_re_enables(gui, monkeypatch,
                                                      tmp_path):
    started = threading.Event()

    def fake_run_agent(task, *, workdir, on_event=None, **kw):
        started.set()
        time.sleep(0.3)
        return "done reply"

    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(d)))
    gui._select_workdir()
    assert gui.input.isEnabled() is True

    monkeypatch.setattr(agent_bridge, "run_agent", fake_run_agent)
    gui._toggle_agent_mode()
    gui.input.setPlainText("hi")
    gui._send()
    assert started.wait(5)
    assert gui._is_streaming is True
    assert gui.input.isEnabled() is False        # busy while streaming
    assert gui.send_btn.isEnabled() is False

    assert _pump_until(lambda: gui.thread is None)
    assert gui.input.isEnabled() is True         # released after the run
    assert gui.send_btn.isEnabled() is True


def test_stop_releases_streaming_gate(gui, monkeypatch, tmp_path):
    started = threading.Event()

    def slow(task, *, workdir, on_event=None, **kw):
        started.set()
        time.sleep(0.6)
        return "late"

    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(d)))
    gui._select_workdir()
    monkeypatch.setattr(agent_bridge, "run_agent", slow)
    gui._toggle_agent_mode()
    gui.input.setPlainText("go")
    gui._send()
    assert started.wait(5)
    assert gui.input.isEnabled() is False

    gui._stop_or_reload()
    assert gui.thread is None
    assert gui.input.isEnabled() is True         # Stop opens the input again


def test_persistent_memory_toggle_survives_settings_load(tmp_path, monkeypatch):
    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    win = _build_gui(tmp_path, monkeypatch, dialog=str(d),
                     settings={"persistent_memory": True})
    try:
        assert win._persistent_memory is True    # not clobbered by __init__
        assert win.mem_btn.text() == "🧠 Persistent"
    finally:
        win.close()
        win.deleteLater()
        _qapp().processEvents()


# ── 9. agent picker (3.1) ─────────────────────────────────────────────────────
def test_agent_picker_populates_from_devassist_registry(gui, monkeypatch):
    fake_agents = [
        {"id": "build", "name": "build", "description": "Default"},
        {"id": "coder", "name": "coder", "description": "Implementation"},
        {"id": "mycrew", "name": "mycrew", "description": "User-defined"},
    ]
    monkeypatch.setattr(agent_bridge, "available_agents", lambda: fake_agents)
    gui._populate_agent_picker()
    assert gui.agent_box.count() == 3
    assert gui.agent_box.itemData(1) == "coder"
    assert gui.agent_box.itemData(2) == "mycrew"
    assert gui.agent_box.currentData() == "build"   # default agent honored


def test_agent_picker_falls_back_when_devassist_missing(gui, monkeypatch):
    monkeypatch.setattr(agent_bridge, "available_agents",
                        lambda: [{"id": "build", "name": "build", "description": "Default"}])
    gui._populate_agent_picker()
    assert gui.agent_box.count() == 1
    assert gui.agent_box.currentData() == "build"


def test_send_passes_picked_agent_to_worker(gui, monkeypatch, tmp_path):
    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(d)))
    gui._select_workdir()
    assert gui.input.isEnabled() is True

    agents = [
        {"id": "build", "name": "build", "description": "Default"},
        {"id": "coder", "name": "coder", "description": "Implementation"},
    ]
    monkeypatch.setattr(agent_bridge, "available_agents", lambda: agents)
    gui._populate_agent_picker()
    gui.agent_box.setCurrentIndex(gui.agent_box.findData("coder"))

    calls = []
    def fake_run_agent(task, *, workdir, approver=None, on_event=None,
                       max_steps=24, agent="build", extra_context=""):
        calls.append(agent)
        return "coder response"

    monkeypatch.setattr(agent_bridge, "run_agent", fake_run_agent)
    gui._toggle_agent_mode()
    gui.input.setPlainText("go")
    gui._send()
    assert _pump_until(lambda: gui.thread is None)
    assert calls == ["coder"], f"agent id not passed: {calls}"
    assert gui._agent_state.get("agent") == "coder"
    assert "coder" in gui.agent_state_label.text()


def test_picked_reviewer_is_read_only_in_state_strip(gui, monkeypatch, tmp_path):
    """Picking reviewer still routes the run, and the strip shows it — the
    reviewer agent itself never volunteers its own setup in replies."""
    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(d)))
    gui._select_workdir()
    agents = [{"id": "reviewer", "name": "reviewer",
               "description": "Read-only reviewer"}]
    monkeypatch.setattr(agent_bridge, "available_agents", lambda: agents)
    gui._populate_agent_picker()
    gui.agent_box.setCurrentIndex(0)

    captured = []
    def fake_run_agent(task, *, workdir, approver=None, on_event=None,
                       max_steps=24, agent="build", extra_context=""):
        captured.append(agent)
        on_event("route", agent)
        return "review done"

    monkeypatch.setattr(agent_bridge, "run_agent", fake_run_agent)
    gui._toggle_agent_mode()
    gui.input.setPlainText("review the change")
    gui._send()
    assert _pump_until(lambda: gui.thread is None)
    assert captured == ["reviewer"]
    assert gui.agent_state_label.text().startswith("🤖 · Agent · reviewer")
    assert "reviewer" in gui.agent_state_label.text()


# ── 10. slash commands via real dispatcher (3.2) ─────────────────────────────
def _enable_agent(gui, monkeypatch, tmp_path):
    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(d)))
    gui._select_workdir()
    if not gui.agent_mode:
        gui._toggle_agent_mode()
    return d


def test_slash_status_dispatch_through_real_executor(gui, monkeypatch,
                                                     tmp_path):
    _enable_agent(gui, monkeypatch, tmp_path)
    gui.input.setPlainText("/status")
    gui._send()
    assert gui.input.toPlainText() == ""          # consumed, not sent to a model
    slash_entries = [m for m in gui._think if m["kind"] == "slash"]
    assert slash_entries, "no slash output reached the think buffer"
    out = slash_entries[0]["text"]
    assert '"agents"' in out and '"tools"' in out


def test_about_json_has_no_error_keys(gui, monkeypatch, tmp_path):
    _enable_agent(gui, monkeypatch, tmp_path)
    import json as _json
    gui.input.setPlainText("/about")
    gui._send()
    slash_entries = [m for m in gui._think if m["kind"] == "slash"]
    assert slash_entries
    data = _json.loads(slash_entries[0]["text"])
    bad = [k for k in data if k.endswith("_error")]
    assert not bad, f"about carried *_error keys: {bad}"


def test_unknown_slash_reports_available_commands(gui, monkeypatch, tmp_path):
    _enable_agent(gui, monkeypatch, tmp_path)
    gui.input.setPlainText("/nope")
    gui._send()
    slash_entries = [m for m in gui._think if m["kind"] == "slash"]
    assert slash_entries
    assert "Unknown command" in slash_entries[0]["text"]
    assert "/status" in slash_entries[0]["text"]      # names list shown


def test_slash_model_list_never_blocks_on_picker(gui, monkeypatch, tmp_path):
    _enable_agent(gui, monkeypatch, tmp_path)
    gui.input.setPlainText("/model list")
    gui._send()
    slash_entries = [m for m in gui._think if m["kind"] == "slash"]
    assert slash_entries               # returned promptly, no input() hang


def test_slash_provider_without_args_uses_print_only_path(gui, monkeypatch,
                                                          tmp_path):
    _enable_agent(gui, monkeypatch, tmp_path)
    gui.input.setPlainText("/provider")     # would input() in the CLI
    gui._send()
    slash_entries = [m for m in gui._think if m["kind"] == "slash"]
    assert slash_entries
    low = slash_entries[0]["text"].lower()
    assert "provider" in low or "list" in low


def test_slash_autocomplete_popup_lists_real_commands(gui, monkeypatch,
                                                      tmp_path):
    _enable_agent(gui, monkeypatch, tmp_path)
    gui.input.setPlainText("/st")
    gui._maybe_show_slash_completions()
    model = gui._slash_completer.model()
    names = [str(model.data(model.index(i, 0)))
             for i in range(model.rowCount())]
    assert names, "no completions offered"
    assert any(n == "/status" for n in names)

    gui._insert_slash_completion("/status")
    assert gui.input.toPlainText(), "completion did not land in the input"


def test_slash_off_when_chat_mode(gui, monkeypatch, tmp_path):
    """Without agent mode a /line is ordinary chat text, not dispatched."""
    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(d)))
    gui._select_workdir()
    assert not gui.agent_mode
    gui.input.setPlainText("/status")
    gui._maybe_show_slash_completions()
    assert not gui._slash_completer.popup().isVisible()


# ── 11. session continuity — GUI-native /new /sessions /resume /compact (3.3) ─
def _seed_conv(gui, title, msgs):
    cid = gui.db.create_conversation(title)
    for role, content in msgs:
        gui.db.add_message(cid, role, content)
    return cid


def _slash_out(gui, text):
    gui._handle_slash_command(text)
    outs = [m["text"] for m in gui._think if m["kind"] == "slash"]
    return outs[-1] if outs else ""


def test_slash_sessions_lists_gui_conversations(gui):
    cid = _seed_conv(gui, "Alpha", [("user", "hi"), ("assistant", "hello")])
    gui.current_conv_id = cid
    out = _slash_out(gui, "/sessions")
    assert "#1" in out and "Alpha" in out and "2 msgs" in out
    assert "▶️" in out                 # current marked
    assert "/resume #1" in out


def test_slash_new_starts_fresh_conversation(gui):
    cid = _seed_conv(gui, "Old", [("user", "x")])
    gui.current_conv_id = cid
    out = _slash_out(gui, "/new")
    assert "New conversation started" in out
    assert gui.current_conv_id is None
    assert not [m for m in gui._chat_log if m.get("type") in ("user", "ai")]
    assert gui._compaction_summary == ""


def test_slash_resume_loads_conversation(gui):
    _seed_conv(gui, "Other", [("user", "z")])        # created first → #2
    target = _seed_conv(gui, "Target", [("user", "q"), ("assistant", "a")])  # #1 (latest)
    gui.current_conv_id = None
    out = _slash_out(gui, "/resume #1")
    assert "Resumed" in out and "2 messages" in out
    assert gui.current_conv_id == target
    conv = [m for m in gui._chat_log if m.get("type") in ("user", "ai")]
    assert len(conv) == 2
    assert gui.chat_title_btn.text() == "💬  Target"


def test_slash_resume_by_id_and_bad_ref(gui):
    target = _seed_conv(gui, "ById", [("user", "q")])
    out = _slash_out(gui, f"/resume {target}")
    assert "Resumed" in out and gui.current_conv_id == target
    out2 = _slash_out(gui, "/resume 99")
    assert "No conversation matches" in out2
    out3 = _slash_out(gui, "/resume")
    assert "Usage:" in out3


def test_slash_compact_sets_extra_context_for_next_run(gui, monkeypatch,
                                                       tmp_path):
    import core.agents as agents_mod
    monkeypatch.setattr(agents_mod, "compact_context", lambda text: "COMPACTED " + str(len(text)))
    cid = _seed_conv(gui, "Chat", [("user", "hello"), ("assistant", "hi")])
    gui.current_conv_id = cid
    d = tmp_path / "proj"
    d.mkdir(exist_ok=True)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(d)))
    gui._select_workdir()
    if not gui.agent_mode:
        gui._toggle_agent_mode()

    gui.input.setPlainText("/compact")
    gui._send()
    assert _pump_until(lambda: bool(gui._compaction_summary))
    assert gui._compaction_summary.startswith("COMPACTED")

    captured = {}
    def fake_run_agent(task, *, workdir, approver=None, on_event=None,
                       max_steps=24, agent="build", extra_context=""):
        captured["extra_context"] = extra_context
        return "ok"

    monkeypatch.setattr(agent_bridge, "run_agent", fake_run_agent)
    gui.input.setPlainText("continue")
    gui._send()
    assert _pump_until(lambda: gui.thread is None)
    assert captured.get("extra_context") == gui._compaction_summary


def test_slash_compact_requires_messages(gui):
    out = _slash_out(gui, "/compact")
    assert "Nothing to compact" in out or "start a conversation" in out
    gui.current_conv_id = _seed_conv(gui, "Solo", [("user", "only one")])
    out2 = _slash_out(gui, "/compact")
    assert "at least 2 messages" in out2


# ── 12. AGENTS.md indicator in the state strip (3.4) ─────────────────────────
def test_agents_md_indicator_shows_check_when_present(gui, monkeypatch,
                                                      tmp_path):
    (tmp_path / "AGENTS.md").write_text("# repo rules\n- no TDD\n",
                                        encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(tmp_path)))
    gui._select_workdir()
    gui._toggle_agent_mode()
    assert "AGENTS.md ✓" in gui.agent_state_label.text()
    # the bit is driven by the real dev-assist discovery
    assert str(tmp_path / "AGENTS.md") in [str(p) for p in gui._discover_agents(str(tmp_path))]


def test_agents_md_indicator_shows_cross_when_missing(gui, monkeypatch,
                                                      tmp_path):
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(tmp_path)))
    gui._select_workdir()
    gui._toggle_agent_mode()
    assert "AGENTS.md ✗" in gui.agent_state_label.text()
    assert gui._discover_agents(str(tmp_path)) == []


def test_agents_md_indicator_absent_outside_agent_mode(gui, monkeypatch,
                                                       tmp_path):
    (tmp_path / "AGENTS.md").write_text("rules", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory",
                        staticmethod(lambda *a, **k: str(tmp_path)))
    gui._select_workdir()
    assert "AGENTS.md" not in gui.agent_state_label.text()


# ── 13. /audit + question tool wiring (3.5) ──────────────────────────────────
def test_slash_audit_runs_real_code_audit_off_thread(gui, tmp_path, monkeypatch):
    from modules import code_audit
    called = {}

    def fake_run(text):
        called["args"] = text
        print(f"AUDIT OUTPUT for args='{text}'")
    monkeypatch.setattr(code_audit, "run", fake_run)
    gui.agent_mode = True
    gui.agent_workdir = str(tmp_path)
    gui._handle_slash_command("/audit")
    assert _pump_until(lambda: "args" in called)
    assert called["args"] == ""
    assert _pump_until(lambda: any(
        e.get("kind") == "slash" and "AUDIT OUTPUT" in e.get("text", "")
        for e in gui._think))
    assert gui._audit_thread is None


def test_audit_passes_raw_args_through(gui, tmp_path, monkeypatch):
    from modules import code_audit
    called = {}
    monkeypatch.setattr(code_audit, "run",
                        lambda t: called.setdefault("args", t))
    gui.agent_mode = True
    gui.agent_workdir = str(tmp_path)
    gui._handle_slash_command("/audit --no-sensitive")
    assert _pump_until(lambda: "args" in called)
    assert called["args"] == "--no-sensitive"


def test_audit_blocks_input_until_done(gui, tmp_path, monkeypatch):
    import threading

    from modules import code_audit
    gate = threading.Event()

    def slow_run(text):
        print("AUDIT START")
        gate.wait(5)
        print("AUDIT END")
    monkeypatch.setattr(code_audit, "run", slow_run)
    gui.agent_mode = True
    gui.agent_workdir = str(tmp_path)
    gui._handle_slash_command("/audit")
    assert gui._audit_running
    assert not gui.send_btn.isEnabled()
    gate.set()
    assert _pump_until(lambda: gui._audit_thread is None)
    assert not gui._audit_running
    assert gui.send_btn.isEnabled()


def test_question_handler_wired_and_cleared(gui):
    from core.toolimpl import store
    gui._toggle_agent_mode()          # ON installs the GUI renderer
    handler = store.get_question_handler()
    assert handler is not None
    assert handler.__self__ is gui
    assert handler.__func__ is gui._gui_question_handler.__func__
    gui._toggle_agent_mode()          # OFF restores the CLI default
    assert store.get_question_handler() is None


def test_question_tool_roundtrip_with_gui_dialog(gui, tmp_path, monkeypatch):
    from PyQt6.QtCore import QThread as _QThread
    from PyQt6.QtWidgets import QInputDialog

    from core import tools
    picks = iter(["refactor", "✍️ Type your own answer"])

    def fake_get_item(parent, title, label, items, current=0, editable=False,
                      *a, **k):
        return next(picks), True

    def fake_get_text(parent, title, label, *a, **k):
        return "custom answer", True
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(fake_get_item))
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(fake_get_text))
    gui._toggle_agent_mode()          # installs the GUI handler

    questions = [{
        "question": "Which approach?",
        "header": "Choose", "multiple": False, "custom": True,
        "options": [{"label": "refactor", "description": ""},
                    {"label": "rewrite", "description": ""}],
    }, {
        "question": "Any notes?",
        "header": "Notes", "multiple": False, "custom": True,
        "options": [{"label": "none", "description": ""}],
    }]

    class _Runner(_QThread):
        def __init__(self, fn):
            super().__init__()
            self.fn = fn
            self.out = None

        def run(self):
            self.out = self.fn()

    runner = _Runner(lambda: tools._tool_question(
        {"questions": questions}, str(tmp_path)))
    runner.start()
    assert _pump_until(lambda: runner.out is not None)
    assert runner.wait(4000)
    assert '"Which approach?"="refactor"' in runner.out
    assert '"Any notes?"="custom answer"' in runner.out
