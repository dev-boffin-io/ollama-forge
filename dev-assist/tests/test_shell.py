"""
Tests for core/shell.py — subprocess runner.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.shell import run, run_git, check_binary, RunResult


# ── RunResult tests ──────────────────────────────────────────────────────────

class TestRunResult:
    def test_ok_true_on_zero_returncode(self):
        r = RunResult(returncode=0, stdout="hello", stderr="", command=["echo"])
        assert r.ok is True

    def test_ok_false_on_nonzero(self):
        r = RunResult(returncode=1, stdout="", stderr="err", command=["false"])
        assert r.ok is False

    def test_ok_false_on_timeout(self):
        r = RunResult(returncode=0, stdout="", stderr="", command=["x"], timed_out=True)
        assert r.ok is False

    def test_output_combines_stdout_stderr(self):
        r = RunResult(returncode=1, stdout="out\n", stderr="err\n", command=["x"])
        assert "out" in r.output
        assert "err" in r.output

    def test_friendly_error_timeout(self):
        r = RunResult(returncode=-1, stdout="", stderr="", command=["sleep"], timed_out=True)
        assert "timed out" in r.friendly_error().lower()

    def test_friendly_error_permission_denied(self):
        r = RunResult(returncode=1, stdout="", stderr="Permission denied", command=["x"])
        assert "Permission" in r.friendly_error()

    def test_friendly_error_command_not_found(self):
        r = RunResult(returncode=1, stdout="", stderr="command not found", command=["xyz"])
        assert "not found" in r.friendly_error().lower()

    def test_friendly_error_git_auth(self):
        r = RunResult(returncode=128, stdout="", stderr="Authentication failed", command=["git"])
        assert "authentication" in r.friendly_error().lower()

    def test_friendly_error_port_in_use(self):
        r = RunResult(returncode=1, stdout="", stderr="Address already in use", command=["x"])
        assert "port" in r.friendly_error().lower()


# ── run() tests ──────────────────────────────────────────────────────────────

class TestRun:
    def test_echo_returns_ok(self):
        result = run(["echo", "hello"])
        assert result.ok
        assert "hello" in result.stdout

    def test_missing_binary_returns_127(self):
        result = run(["__nonexistent_binary_xyz__"])
        assert result.returncode == 127
        assert not result.ok

    def test_string_cmd_splits_correctly(self):
        result = run("echo world")
        assert result.ok
        assert "world" in result.stdout

    def test_false_returns_nonzero(self):
        result = run(["false"])
        assert not result.ok
        assert result.returncode != 0

    def test_timeout_fires(self):
        result = run(["sleep", "10"], timeout=1)
        assert result.timed_out
        assert not result.ok

    def test_cwd_is_respected(self):
        result = run(["pwd"], cwd="/tmp")
        assert result.ok
        assert "/tmp" in result.stdout


# ── run_git() tests ──────────────────────────────────────────────────────────

class TestRunGit:
    def test_git_version_ok(self):
        if not check_binary("git"):
            pytest.skip("git not installed")
        result = run_git("--version")
        assert result.ok
        assert "git" in result.stdout.lower()

    def test_git_status_outside_repo(self, tmp_path):
        if not check_binary("git"):
            pytest.skip("git not installed")
        result = run_git("status", cwd=str(tmp_path))
        # Should fail gracefully, not raise
        assert isinstance(result, RunResult)
        assert not result.ok
        assert "not a git repository" in result.stderr.lower()


# ── check_binary() tests ─────────────────────────────────────────────────────

class TestCheckBinary:
    def test_python_exists(self):
        assert check_binary("python3") or check_binary("python")

    def test_nonexistent_returns_false(self):
        assert check_binary("__definitely_not_a_real_binary__") is False

    def test_echo_exists(self):
        assert check_binary("echo")
