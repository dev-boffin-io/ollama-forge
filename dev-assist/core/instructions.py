"""
AGENTS.md discovery + loading — automatic instruction files (opencode parity).

opencode loads `AGENTS.md` rules automatically: the closest one walking up
from the project root, then a global one, and feeds them to the model. This
module ports that behaviour for dev-assist:

  - project AGENTS.md files: walking up from the working directory to the
    filesystem root (deepest first, so the most specific rules win),
  - a global `~/.config/dev-assist/AGENTS.md`,
  - and, so existing opencode users get the same guidance, a shared
    `~/.config/opencode/AGENTS.md`.

Result is cached briefly per workdir just like the toolbar status, since
the agent may build several prompts for one run.
"""

from __future__ import annotations

import os
import time

_CACHE_TTL = 2.0  # seconds
_cache: dict[str, tuple[float, list[str], str]] = {}


def _global_candidates(home: str | None = None) -> list[str]:
    home = home or os.path.expanduser("~")
    return [
        os.path.join(home, ".config", "dev-assist", "AGENTS.md"),
        os.path.join(home, ".config", "opencode", "AGENTS.md"),
    ]


def discover(workdir: str) -> list[str]:
    """
    Return every AGENTS.md that applies to `workdir`.

    Order: deepest project file first (inherently most specific), then
    closer-to-root project files, then the global files. Deduplicated.
    """
    paths: list[str] = []
    seen: set[str] = set()

    current = os.path.abspath(workdir)
    while True:
        candidate = os.path.join(current, "AGENTS.md")
        if os.path.isfile(candidate) and candidate not in seen:
            seen.add(candidate)
            # Deepest-first: newest (nearest) wins.
            paths.append(candidate)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    # Deepest-first means we appended nearest first; flip so the global last
    # and the root-most project file reads before the deepest one. Keep the
    # deepest-first semantic by reversing the project portion.
    paths = list(reversed(paths))

    for candidate in _global_candidates():
        if os.path.isfile(candidate) and candidate not in seen:
            seen.add(candidate)
            paths.append(candidate)

    return paths


def load_instructions(workdir: str) -> str:
    """
    Load and concatenate every applicable AGENTS.md into a single prompt
    block. Returns "" when nothing applies. Cached for a couple of seconds.
    """
    workdir = os.path.abspath(workdir)
    now = time.time()
    cached = _cache.get(workdir)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[2]

    paths = discover(workdir)
    blocks: list[str] = []
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError:
            continue
        content = content.strip()
        if not content:
            continue
        blocks.append(f"### {path}\n\n{content}")

    text = ""
    if blocks:
        text = "## AGENTS.md instructions\n\n" + "\n\n".join(blocks)

    _cache[workdir] = (now, paths, text)
    return text


def invalidate(workdir: str) -> None:
    """Drop the cache for one workdir (e.g. after /init rewrites AGENTS.md)."""
    _cache.pop(os.path.abspath(workdir), None)


def reset_cache() -> None:
    _cache.clear()
