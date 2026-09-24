"""
Tests for core/permissions.py — allow / ask / deny evaluation.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.permissions import evaluate, is_destructive


class TestEvaluateBasic:
    def test_empty_rules_no_verdict(self):
        assert evaluate({}, "write_file", {}) == ""
        assert evaluate(None, "write_file", {}) == ""

    def test_exact_tool_name_beats_group(self):
        rules = {"edit": "ask", "write_file": "allow"}
        assert evaluate(rules, "write_file", {}) == "allow"
        assert evaluate(rules, "edit_file", {}) == "ask"

    def test_group_applies_to_members(self):
        rules = {"edit": "deny"}
        for tool in ("write_file", "edit_file", "apply_patch"):
            assert evaluate(rules, tool, {}) == "deny"

    def test_read_group(self):
        rules = {"read": "allow"}
        for tool in ("read_file", "list_dir", "glob", "grep"):
            assert evaluate(rules, tool, {}) == "allow"

    def test_unknown_tool_ignored(self):
        rules = {"edit": "deny"}
        assert evaluate(rules, "web_search", {}) == ""

    def test_invalid_verdict_ignored(self):
        assert evaluate({"edit": "maybe"}, "write_file", {}) == ""


class TestBashPrefixRules:
    def test_allow_list(self):
        rules = {"bash": {"allow": ["git log*", "git status*"]}}
        assert evaluate(rules, "bash", {"command": "git log --oneline"}) == "allow"
        assert evaluate(rules, "bash", {"command": "git status --short"}) == "allow"

    def test_ask_list(self):
        rules = {"bash": {"ask": ["git push*"]}}
        assert evaluate(rules, "bash", {"command": "git push origin main"}) == "ask"

    def test_deny_list(self):
        rules = {"bash": {"deny": ["git reset*", "git rebase*"]}}
        assert evaluate(rules, "bash", {"command": "git reset --hard HEAD"}) == "deny"
        assert evaluate(rules, "bash", {"command": "git rebase main"}) == "deny"

    def test_unmatched_command_no_verdict(self):
        rules = {"bash": {"deny": ["git reset*"]}}
        assert evaluate(rules, "bash", {"command": "git status"}) == ""

    def test_matching_is_case_insensitive(self):
        rules = {"bash": {"deny": ["git push*"]}}
        assert evaluate(rules, "bash", {"command": "GIT PUSH origin"}) == "deny"

    def test_missing_command_no_verdict(self):
        rules = {"bash": {"deny": ["git reset*"]}}
        assert evaluate(rules, "bash", {}) == ""


class TestCommonVerdicts:
    def test_string_verdict(self):
        assert evaluate({"read_file": "ask"}, "read_file", {}) == "ask"

    def test_dict_with_true_flag(self):
        rules = {"webfetch": {"allow": True}}
        assert evaluate(rules, "web_fetch", {}) == "allow"


class TestIsDestructive:
    def test_destructive_tools(self):
        for tool in ("write_file", "edit_file", "bash", "apply_patch"):
            assert is_destructive(tool) is True

    def test_read_tools_not_destructive(self):
        for tool in ("read_file", "grep", "glob", "list_dir", "web_search"):
            assert is_destructive(tool) is False
