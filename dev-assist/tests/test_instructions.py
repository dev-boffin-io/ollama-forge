"""
Tests for core/instructions.py — AGENTS.md discovery and loading.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from core import instructions as instr


@pytest.fixture(autouse=True)
def clear_cache():
    instr.reset_cache()
    yield
    instr.reset_cache()


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(
        instr, "_global_candidates",
        lambda h=None: [
            str(home / ".config" / "dev-assist" / "AGENTS.md"),
            str(home / ".config" / "opencode" / "AGENTS.md"),
        ],
    )
    return home


class TestDiscover:
    def test_empty_workdir(self, tmp_path):
        assert instr.discover(str(tmp_path)) == []

    def test_finds_nested_agents(self, tmp_path):
        root = tmp_path / "app"
        root.mkdir(parents=True)
        (root / "AGENTS.md").write_text("root rules")
        (root / "pkg").mkdir()
        (root / "pkg" / "AGENTS.md").write_text("pkg rules")
        (root / "pkg" / "sub").mkdir()
        (root / "pkg" / "sub" / "AGENTS.md").write_text("sub rules")

        found = instr.discover(str(root / "pkg" / "sub"))

        assert len(found) == 3
        # every ancestor AGENTS.md is included regardless of walk-up order
        for expected in ("app", "pkg", "sub"):
            assert any(os.path.basename(os.path.dirname(p)) == expected for p in found)

    def test_global_files_appended_last(self, tmp_path, fake_home):
        (fake_home / ".config" / "dev-assist").mkdir(parents=True)
        (fake_home / ".config" / "dev-assist" / "AGENTS.md").write_text("global")
        (fake_home / ".config" / "opencode").mkdir(parents=True)
        (fake_home / ".config" / "opencode" / "AGENTS.md").write_text("opencode global")

        found = instr.discover(str(tmp_path))
        assert len(found) == 2
        assert found[-1].endswith("opencode/AGENTS.md")

    def test_dedupes_repeated_walkups(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("x")
        found = instr.discover(str(tmp_path / "nonexistent" / "deep"))
        assert len(found) == 1


class TestLoadInstructions:
    def test_empty_when_no_rules(self, tmp_path):
        assert instr.load_instructions(str(tmp_path)) == ""

    def test_concatenates_files(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("Project rules.")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "AGENTS.md").write_text("Sub rules.")
        text = instr.load_instructions(str(tmp_path / "sub"))
        assert "## AGENTS.md instructions" in text
        assert "Project rules." in text
        assert "Sub rules." in text

    def test_empty_file_skipped(self, tmp_path):
        (tmp_path / "AGENTS.md").write_text("   \n")
        assert instr.load_instructions(str(tmp_path)) == ""

    def test_cache_serves_second_lookup(self, tmp_path, monkeypatch):
        calls = {"n": 0}
        real = instr.discover

        def counting_discover(workdir):
            calls["n"] += 1
            return real(workdir)

        monkeypatch.setattr(instr, "discover", counting_discover)
        (tmp_path / "AGENTS.md").write_text("cached!")
        instr.load_instructions(str(tmp_path))
        instr.load_instructions(str(tmp_path))
        assert calls["n"] == 1  # cached: discover ran once

    def test_invalidate_forces_reload(self, tmp_path, monkeypatch):
        real = instr.discover
        calls = {"n": 0}

        def counting_discover(workdir):
            calls["n"] += 1
            return real(workdir)

        monkeypatch.setattr(instr, "discover", counting_discover)
        (tmp_path / "AGENTS.md").write_text("a")
        instr.load_instructions(str(tmp_path))
        assert calls["n"] == 1
        instr.invalidate(str(tmp_path))
        instr.load_instructions(str(tmp_path))
        assert calls["n"] == 2


class TestRuntimeHook:
    def test_load_is_callable_from_agent(self, tmp_path, monkeypatch):
        from core import instructions as _ins
        (tmp_path / "AGENTS.md").write_text("Verify with pytest -q.")
        blob = _ins.load_instructions(str(tmp_path))
        assert "pytest" in blob
