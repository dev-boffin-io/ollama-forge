"""
Tests for `da run` — the headless one-shot agent mode in main.py.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import main as main_mod


class TestSessionTag:
    def test_empty_without_active_session(self, tmp_path, monkeypatch):
        from core import session_store as ss
        monkeypatch.setenv("DEV_ASSIST_DATA_DIR", str(tmp_path))
        ss.reset_state()
        try:
            assert main_mod._session_tag() == ""
        finally:
            ss.reset_state()

    def test_prefix_of_active_session(self, tmp_path, monkeypatch):
        from core import session_store as ss
        monkeypatch.setenv("DEV_ASSIST_DATA_DIR", str(tmp_path))
        ss.reset_state()
        try:
            sess = ss.new_session(project="/tmp")
            ss.resume_session(sess.id)
            assert main_mod._session_tag()
            assert main_mod._session_tag() == sess.id[:6]
        finally:
            ss.reset_state()


class TestStartRun:
    def test_empty_task_exits_2(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main_mod._start_run([])
        assert exc.value.code == 2
        assert "Usage: da run" in capsys.readouterr().err

    def test_help_exits_0(self):
        with pytest.raises(SystemExit) as exc:
            main_mod._start_run(["--help"])
        assert exc.value.code == 0

    def test_success_exits_0(self, monkeypatch, tmp_path):
        def fake_run_task(task, workdir=None, flags=None):
            return "ALL DONE"

        import modules.agent_mode as am
        monkeypatch.setattr(am, "run_task", fake_run_task)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main_mod._start_run(["fix everything"])
        assert exc.value.code == 0

    def test_empty_result_exits_1(self, monkeypatch, tmp_path):
        import modules.agent_mode as am
        monkeypatch.setattr(am, "run_task", lambda task, workdir=None, flags=None: "")
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            main_mod._start_run(["task that produces nothing"])

    def test_error_exits_1(self, monkeypatch, tmp_path, capsys):
        import modules.agent_mode as am

        def boom(task, workdir=None, flags=None):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(am, "run_task", boom)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc:
            main_mod._start_run(["do it"])
        assert exc.value.code == 1
        assert "kaboom" in capsys.readouterr().out

    def test_flags_forwarded(self, monkeypatch, tmp_path):
        captured = {}

        def fake_run_task(task, workdir=None, flags=None):
            captured.update(flags or {})
            return "ok"

        import modules.agent_mode as am
        monkeypatch.setattr(am, "run_task", fake_run_task)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            main_mod._start_run([
                "refactor --agent reviewer --no(ignored)", "--auto", "--verbose",
            ])
        assert captured.get("auto") is True
        assert captured.get("verbose") is True
        assert captured.get("agent") == "reviewer"

    def test_output_json_flag(self, monkeypatch, tmp_path):
        captured = {}

        def fake_run_task(task, workdir=None, flags=None):
            captured.update(flags or {})
            return "ok"

        import modules.agent_mode as am
        monkeypatch.setattr(am, "run_task", fake_run_task)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            main_mod._start_run(["task --output json"])
        assert captured.get("output") == "json"

    def test_reads_task_from_stdin(self, monkeypatch, tmp_path):
        import io

        import modules.agent_mode as am

        captured = {}
        monkeypatch.setattr(sys, "stdin", io.StringIO("piped task"))
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        monkeypatch.setattr(am, "run_task",
                            lambda task, workdir=None, flags=None: captured.update({"task": task}) or "ok")
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit):
            main_mod._start_run([])
        assert captured.get("task") == "piped task"


class TestMainDispatch:
    def test_run_subcommand_routes_to_start_run(self, monkeypatch):
        called = {"n": 0}

        def fake_start_run(args):
            called["args"] = args
            called["n"] += 1

        monkeypatch.setattr(main_mod, "_start_run", fake_start_run)
        monkeypatch.setattr(sys, "argv", ["main.py", "run", "hello world"])
        main_mod.main()
        assert called["n"] == 1
        assert called["args"] == ["hello world"]

    def test_other_args_do_not_route_to_run(self, monkeypatch):
        called = {"n": 0}

        def fake_start_web(host, port):
            called["n"] += 1

        monkeypatch.setattr(main_mod, "_start_web", fake_start_web)
        monkeypatch.setattr(sys, "argv", ["main.py", "--web"])
        main_mod.main()
        assert called["n"] == 1


class TestParseArgs:
    def test_web_flag_not_collided_by_run(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["main.py", "run", "--web", "--json", "task"])
        # run path is taken before _parse_args, so --web stays out of it
        web, host, port, resume = main_mod._parse_args()
        assert web is False
        assert host == "127.0.0.1"

    def test_web_flag_without_run_still_works(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["main.py", "--web", "--port", "9000"])
        web, host, port, resume = main_mod._parse_args()
        assert web is True
        assert port == 9000
