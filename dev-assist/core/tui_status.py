"""
TUI status — shared state that drives the persistent bottom toolbar.

main.py's PromptSession renders this on every keystroke/refresh tick, and
modules/agent_mode.py updates the "activity" line while an agent run is
in progress. Kept deliberately tiny and dependency-free (just string
building) so it's cheap to call on every render.

Ollama's status check involves a local HTTP/socket call (and sometimes a
subprocess fallback), so it's cached for a couple of seconds — a toolbar
that refreshes several times a second must not turn that into several
status checks a second.
"""

from __future__ import annotations

import os
import time

_STATUS_TTL = 2.0  # seconds
_cached_status: tuple[float, str] | None = None  # (checked_at, status)

_activity: str | None = None


# ─────────────────────────────────────────────────────────────────────
# Agent activity — set while `do <task>` is running, cleared after
# ─────────────────────────────────────────────────────────────────────
def set_activity(text: str) -> None:
    global _activity
    _activity = text


def clear_activity() -> None:
    global _activity
    _activity = None


def get_activity() -> str | None:
    return _activity


# ─────────────────────────────────────────────────────────────────────
# Cached ollama status
# ─────────────────────────────────────────────────────────────────────
def _cached_ollama_status() -> str:
    global _cached_status
    now = time.time()
    if _cached_status and (now - _cached_status[0]) < _STATUS_TTL:
        return _cached_status[1]
    try:
        from core.ollama_status import get_status
        status = get_status()
    except Exception:
        status = "unknown"
    _cached_status = (now, status)
    return status


_STATUS_DOTS = {
    "running": "🟢",
    "stopped": "🔴",
    "not_installed": "⚫",
    "unknown": "❓",
}


# ─────────────────────────────────────────────────────────────────────
# Toolbar rendering
# ─────────────────────────────────────────────────────────────────────
def render_bottom_toolbar():
    """
    Build the bottom_toolbar content for prompt_toolkit's PromptSession.
    Returns a list of (style, text) fragments (prompt_toolkit's
    "formatted text" tuple form), so no HTML/ANSI parsing is needed.
    """
    try:
        from core.ai import get_current_model
        model = get_current_model()
    except Exception:
        model = "?"

    status = _cached_ollama_status()
    dot = _STATUS_DOTS.get(status, "❓")

    try:
        from modules.shell_exec import get_cwd
        cwd = get_cwd()
    except Exception:
        cwd = os.getcwd()
    home = os.path.expanduser("~")
    if cwd.startswith(home):
        cwd = "~" + cwd[len(home):]

    fragments = [
        ("class:toolbar", f" {dot} ollama:{status}  "),
        ("class:toolbar", f"🤖 {model}  "),
        ("class:toolbar", f"📁 {cwd}"),
    ]

    activity = get_activity()
    if activity:
        fragments.append(("class:toolbar.activity", f"   ⏳ {activity}"))

    return fragments
