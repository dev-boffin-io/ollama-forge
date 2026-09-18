"""
Change tracker — snapshots + undo for agent-made file edits.

Every destructive tool call (write_file, edit_file) that the agent loop
executes gets recorded here: the file's content *before* the change is
snapshotted the first time it's touched in a run, so the whole run can
be undone even if several tools edited the same file multiple times.

This is deliberately separate from core.tools (which stays pure and
stateless) and from core.agent (which orchestrates *when* tracking
happens). A module-level singleton mirrors core.session's pattern, so
`undo` works from the router across calls within one REPL process.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field


@dataclass
class ChangeEntry:
    path: str
    kind: str            # "created" | "edited" | "overwritten"
    before: str | None   # None means the file didn't exist before
    after: str
    timestamp: float = field(default_factory=time.time)


def _line_delta(before: list[str], after: list[str]) -> tuple[int, int]:
    """Cheap +/- line counts via difflib, good enough for a diffstat summary."""
    import difflib
    sm = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    added = removed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag in ("replace", "delete"):
            removed += i2 - i1
        if tag in ("replace", "insert"):
            added += j2 - j1
    return added, removed


class ChangeTracker:
    """Tracks the files touched during one agent run."""

    def __init__(self) -> None:
        self._first_seen: dict[str, str | None] = {}  # path -> content before this run (None = didn't exist)
        self.entries: list[ChangeEntry] = []

    def snapshot(self, path: str) -> None:
        """Record the file's current content, but only the first time it's touched this run."""
        if path in self._first_seen:
            return
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    self._first_seen[path] = f.read()
            except Exception:
                self._first_seen[path] = None
        else:
            self._first_seen[path] = None

    def record(self, path: str, after: str) -> None:
        """Log the file's content after a successful write/edit."""
        before = self._first_seen.get(path)
        kind = "created" if before is None else "edited"
        self.entries.append(ChangeEntry(path=path, kind=kind, before=before, after=after))

    def touched_paths(self) -> list[str]:
        # de-duplicate, keep first-touched order
        seen, out = set(), []
        for e in self.entries:
            if e.path not in seen:
                seen.add(e.path)
                out.append(e.path)
        return out

    def diffstat(self) -> str:
        if not self.entries:
            return "No files changed."
        paths = self.touched_paths()
        # Use the LATEST entry per path for final add/remove counts against
        # the ORIGINAL snapshot, so repeated edits to one file aren't double
        # counted turn-by-turn.
        total_added = total_removed = 0
        created = 0
        for path in paths:
            before = self._first_seen.get(path)
            after = next(e.after for e in reversed(self.entries) if e.path == path)
            if before is None:
                created += 1
                total_added += len(after.splitlines())
            else:
                a, r = _line_delta(before.splitlines(), after.splitlines())
                total_added += a
                total_removed += r
        n = len(paths)
        parts = [f"{n} file{'s' if n != 1 else ''} changed"]
        if total_added:
            parts.append(f"+{total_added}")
        if total_removed:
            parts.append(f"-{total_removed}")
        if created:
            parts.append(f"{created} created")
        return ", ".join(parts)

    def undo(self) -> list[str]:
        """
        Revert every touched file to its pre-run content. Files that were
        newly created are deleted. Returns the list of paths restored.
        """
        restored = []
        for path in self.touched_paths():
            before = self._first_seen.get(path)
            try:
                if before is None:
                    if os.path.isfile(path):
                        os.remove(path)
                else:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(before)
                restored.append(path)
            except Exception:
                pass
        self.entries.clear()
        self._first_seen.clear()
        return restored

    def has_changes(self) -> bool:
        return bool(self.entries)


# ─────────────────────────────────────────────────────────────────────
# Module-level singleton — one tracker per "run", kept around afterward
# so `undo` can reach it from a later router call in the same session.
# ─────────────────────────────────────────────────────────────────────
_current: ChangeTracker | None = None


def new_run() -> ChangeTracker:
    """Start tracking a fresh agent run. Call this at the top of run_agent()."""
    global _current
    _current = ChangeTracker()
    return _current


def get_tracker() -> ChangeTracker | None:
    """The tracker for the most recent agent run, or None if none has run yet."""
    return _current
