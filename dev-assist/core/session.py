"""
Session Context — Conversation history and context management for the REPL.

Keeps track of:
- Conversation turns (user + assistant messages)
- Active indexed project path
- Session metadata

History is in-memory only (not persisted across sessions).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

MAX_HISTORY_TURNS = 20      # keep last N user+assistant pairs
MAX_CONTEXT_CHARS = 8000    # trim history before sending to AI


@dataclass
class Turn:
    role: Literal["user", "assistant"]
    content: str
    timestamp: float = field(default_factory=time.time)

    def age_seconds(self) -> float:
        return time.time() - self.timestamp


class SessionContext:
    """Single-session conversation context."""

    def __init__(self) -> None:
        self._history: list[Turn] = []
        self.indexed_path: str | None = None
        self.session_start: float = time.time()
        self._model_used: str = ""

    # ── History management ─────────────────────────────────────────────────

    def add_user(self, text: str) -> None:
        self._history.append(Turn(role="user", content=text))
        self._trim()

    def add_assistant(self, text: str) -> None:
        self._history.append(Turn(role="assistant", content=text))
        self._trim()

    def _trim(self) -> None:
        """Keep only recent turns, respecting MAX_HISTORY_TURNS."""
        # Each "turn" = 1 user + 1 assistant → keep 2×N messages
        if len(self._history) > MAX_HISTORY_TURNS * 2:
            self._history = self._history[-(MAX_HISTORY_TURNS * 2):]

    def get_history(self) -> list[Turn]:
        return list(self._history)

    def build_history_prompt(self, current_query: str) -> str:
        """
        Build a context-aware prompt that includes recent conversation history.
        Trims to MAX_CONTEXT_CHARS to avoid overflowing the model context.
        """
        if not self._history:
            return current_query

        lines = ["=== Conversation History ==="]
        total_chars = 0

        # Walk history backwards, insert from newest to oldest
        relevant = []
        for turn in reversed(self._history):
            chunk = f"[{turn.role.upper()}]: {turn.content}"
            total_chars += len(chunk)
            if total_chars > MAX_CONTEXT_CHARS:
                break
            relevant.insert(0, chunk)

        if relevant:
            lines.extend(relevant)
            lines.append("=== Current Question ===")

        lines.append(current_query)
        return "\n\n".join(lines)

    def clear_history(self) -> None:
        self._history.clear()

    def history_summary(self) -> str:
        turns = len(self._history)
        if turns == 0:
            return "No conversation history yet."
        pairs = turns // 2
        elapsed = int(time.time() - self.session_start)
        mins, secs = divmod(elapsed, 60)
        return (
            f"{pairs} exchange(s) in session  "
            f"({mins}m {secs}s elapsed)"
        )

    # ── Project context ────────────────────────────────────────────────────

    def set_indexed_path(self, path: str) -> None:
        self.indexed_path = path

    def get_indexed_path(self) -> str | None:
        return self.indexed_path

    # ── Ollama multi-turn format ───────────────────────────────────────────

    def to_ollama_messages(self) -> list[dict[str, str]]:
        """
        Convert history to Ollama chat messages format.
        Used when AI engine supports multi-turn conversation natively.
        """
        return [
            {"role": t.role, "content": t.content}
            for t in self._history
        ]


# ── Module-level singleton ─────────────────────────────────────────────────

_session = SessionContext()


def get_session() -> SessionContext:
    """Return the current session context (module singleton)."""
    return _session


def reset_session() -> None:
    """Reset session (useful for testing)."""
    global _session
    _session = SessionContext()
