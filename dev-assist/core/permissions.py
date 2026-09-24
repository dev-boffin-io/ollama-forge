"""
Permission rules — allow / ask / deny gate for agent tools (opencode parity).

opencode lets users configure per-tool policies in config (`permission` /
`permissions`): a tool is `allow`ed, `ask`ed, or `deny`ed up front, and
`bash` additionally supports command-pattern match-lists. This module
evaluates those rules against a (tool_name, args) call and returns a
verdict the agent loop's approver can act on.

Config shape (settings.json):

    "permissions": {
      "edit":      "allow",                                  # write_file/edit_file/apply_patch
      "bash":      {"allow": ["git log*", "git status*"],    # prefix-matched commands
                    "ask":   ["git push*", "git pull*"],
                    "deny":   ["git reset*", "git rebase*"]},
      "webfetch":  "ask",
      "read_file": "deny"
    }

Precedence: an exact rule for the tool name beats a group rule; groups are
`edit`, `bash`, `webfetch`, `read`, `task`, `mcp`. An empty verdict means
"no rule — fall back to the default policy" (read tools allowed,
destructive tools asked).
"""

from __future__ import annotations

from typing import Any

_GROUPS: dict[str, tuple[str, ...]] = {
    "edit": ("write_file", "edit_file", "apply_patch"),
    "bash": ("bash",),
    "webfetch": ("web_fetch", "web_search"),
    "read": ("read_file", "list_dir", "glob", "grep", "skill", "run_tests", "lsp_diagnostics"),
    "task": ("task",),
}

_VERDICTS = ("allow", "ask", "deny")


def _match(patterns: Any, command: str) -> bool:
    """Does `command` start with (or glob-match) any/the pattern list?"""
    if not patterns:
        return False
    if isinstance(patterns, str):
        patterns = [patterns]
    needle = command.strip().lower()
    for pat in patterns:
        p = str(pat).strip().lower().rstrip("*")
        if needle.startswith(p):
            return True
    return False


def evaluate(rules: Any, name: str, args: dict) -> str:
    """
    Evaluate permission rules for a tool call.

    Returns one of "allow" | "ask" | "deny", or "" when no rule applies so
    the caller falls back to its default policy.
    """
    if not rules or not isinstance(rules, dict):
        return ""

    item = rules.get(name)
    if item is None:
        for group, members in _GROUPS.items():
            if name in members and rules.get(group) is not None:
                item = rules[group]
                break
    if item is None:
        return ""

    if name == "bash" and isinstance(item, dict):
        command = str(args.get("command") or "")
        if _match(item.get("allow"), command):
            return "allow"
        if _match(item.get("ask"), command):
            return "ask"
        if _match(item.get("deny"), command):
            return "deny"
        return ""

    if isinstance(item, dict):
        # A dict without a matching order → default to "ask".
        for key in ("allow", "ask", "deny"):
            if item.get(key) is True:
                return key
        return "ask"

    if isinstance(item, str):
        verdict = item.strip().lower()
        if verdict in _VERDICTS:
            return verdict

    return ""


def is_destructive(name: str) -> bool:
    """True for the file-modifying / command-running tools."""
    from core.tools import DESTRUCTIVE_TOOLS
    return name in DESTRUCTIVE_TOOLS
