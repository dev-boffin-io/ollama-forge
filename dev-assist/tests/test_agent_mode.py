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
        assert flags == {"auto": False, "auto_yes": False, "verbose": False}

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