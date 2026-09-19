"""
Repo map — compact project structure for the agent's context window.

Builds a small "map" of a project before the agent starts: the file tree
plus the top-level function/class *signatures* of each code file (not the
full code). The agent gets this injected into its system prompt so it
knows the layout from the start instead of having to list_dir/glob/grep
its way to first bearing.

For large projects the full map would blow the context budget, so it falls
back to `core.vector_store.search` (the existing indexer's retrieval): only
the files relevant to the task at hand are mapped, via similarity.

File discovery reuses the indexer's own rules (same extensions, skip-dirs,
size/binary filters) through modules.indexer._collect_files, so what the
agent sees in the map matches what the index knows about.
"""

from __future__ import annotations

import os
import re

# How many chars of map text fit alongside the system prompt comfortably.
REPO_MAP_CHAR_CAP = 20_000
RAG_TOP_K = 8            # files pulled in when falling back to similarity
SIG_READ_BYTES = 60_000  # don't parse more than this per file for signatures
SIG_MAX_LINES = 400
MAX_SIGNATURES_PER_FILE = 25
MAX_START = 200_000      # before falling back, we always try a small index run

# Mirror the indexer's supported-set so the fallback walk matches behaviour.
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env", ".env",
    "dist", "build", ".idea", ".vscode", "*.egg-info", ".pytest_cache",
    "htmlcov", ".mypy_cache", ".ruff_cache", "target",
}
_CODE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".c", ".cpp", ".h",
    ".hpp", ".java", ".kt", ".rb", ".php", ".swift", ".sh", ".bash", ".zsh",
    ".md", ".txt", ".rst", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".html", ".css", ".scss", ".sql",
}


# ── Signature extraction ────────────────────────────────────────────────────

def _sig_patterns(ext: str) -> list[re.Pattern] | None:
    if ext in (".py",):
        return [re.compile(r"(?:async\s+)?(?:def|class)\s+\w+")]
    if ext == ".rb":
        return [re.compile(r"(?:def|class)\s+\w+")]
    if ext == ".go":
        return [re.compile(r"(?:func|type)\s+\w+")]
    if ext == ".rs":
        return [re.compile(
            r"(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:fn|struct|enum|trait|impl)\s+\w+"
        )]
    if ext in (".js", ".jsx"):
        return [
            re.compile(r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+\w+"),
            re.compile(r"(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?(?:\(.*?=>|[\w.]+\s*=>)"),
        ]
    if ext in (".ts", ".tsx"):
        return [
            re.compile(r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+\w+"),
            re.compile(r"(?:export\s+)?(?:interface|class|enum|type)\s+\w+"),
            re.compile(r"(?:export\s+)?(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?(?:\(.*?=>|[\w.]+\s*=>)"),
        ]
    if ext in (".c", ".cpp", ".cc", ".h", ".hpp", ".java", ".kt", ".swift",
               ".php", ".cs"):
        # Brace-style languages share one tolerant "definition-ish line" regex.
        return [_GENERIC_SIG]
    return None


_GENERIC_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "new", "case",
    "do", "else", "try", "goto", "throw", "assert", "delete",
}
_GENERIC_SIG = re.compile(
    r"^\s*(?:(?:public|private|protected|static|abstract|final|sealed|"
    r"virtual|override|async|synchronized|extern|inline|constexpr)\s+)*"
    r"(?:[\w<>,\[\]?*&:.]+\s+)*"
    r"([A-Za-z_]\w*)\s*\([^;{}]*?\)\s*(?:const\s*|noexcept\s*|override\s*|\{)?$"
)


def _file_signatures(path: str) -> str:
    """Top-level function/class signatures for one file, or ''."""
    patterns = _sig_patterns(os.path.splitext(path)[1].lower())
    if not patterns:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(SIG_READ_BYTES)
    except Exception:
        return ""
    lines = text.splitlines()[:SIG_MAX_LINES]

    out = []
    generic = patterns is _GENERIC_SIG or (isinstance(patterns, list) and
                                           len(patterns) == 1 and
                                           patterns[0] is _GENERIC_SIG)
    for i, ln in enumerate(lines):
        if generic:
            m = _GENERIC_SIG.search(ln)
            if m and m.group(1) not in _GENERIC_KEYWORDS:
                out.append(f"  :{i + 1} {ln.strip()[:160]}")
        else:
            for pat in patterns:
                if pat.match(ln):
                    out.append(f"  :{i + 1} {ln.strip()[:160]}")
                    break
        if len(out) >= MAX_SIGNATURES_PER_FILE:
            break
    return "\n".join(out)


# ── Tree ────────────────────────────────────────────────────────────────────

def _render_tree(rels: list[str], max_entries: int = 300) -> list[str]:
    root: dict = {}
    for rel in rels:
        node = root
        parts = rel.split(os.sep)
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = None  # leaf = file

    lines = ["."]
    entry_count = 0

    def walk(node: dict, prefix: str) -> None:
        nonlocal entry_count
        entries = sorted(node.items(), key=lambda kv: (kv[1] is None, kv[0]))
        for i, (name, child) in enumerate(entries):
            last = i == len(entries) - 1
            connector = "└── " if last else "├── "
            lines.append(prefix + connector + name +
                         ("/" if child is not None else ""))
            entry_count += 1
            if child is not None:
                walk(child, prefix + ("    " if last else "│   "))

    walk(root, "")
    if entry_count > max_entries:
        over = entry_count - max_entries
        return lines[: max_entries + 1] + [f"  ... ({over} more entries)"]
    return lines


# ── File collection ─────────────────────────────────────────────────────────

def _collect_files(folder: str) -> list[str]:
    """Same file set the indexer uses (extensions, skip dirs, size, binary)."""
    try:
        from modules.indexer import _collect_files as indexer_collect
        return list(indexer_collect(folder))
    except Exception:
        pass

    files = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = [d for d in dirs
                   if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in sorted(names):
            if os.path.splitext(name)[1].lower() not in _CODE_EXTS:
                continue
            full = os.path.join(root, name)
            try:
                if os.path.getsize(full) > 500 * 1024:
                    continue
                with open(full, "rb") as f:
                    if b"\x00" in f.read(8192):
                        continue
            except OSError:
                continue
            files.append(full)
    return files


# ── Map builders ────────────────────────────────────────────────────────────

def _build_full_map(files: list[str], root: str) -> str:
    rels = sorted(os.path.relpath(f, root) for f in files)
    tree = _render_tree(rels)
    parts = [
        f"# Project: {root}",
        _TREE_HEADER,
        "\n".join(tree),
    ]
    blocks = []
    for rel in rels:
        sig = _file_signatures(os.path.join(root, rel))
        if sig:
            blocks.append(f"\n## {rel}\n{sig}")
    return "\n".join(parts) + "".join(blocks)


def _build_focused_map(query: str, root: str, max_chars: int) -> str | None:
    """Similarity-narrowed map: only files relevant to `query`."""
    from core import vector_store as _vs

    root_abs = os.path.abspath(root)
    try:
        hits = _vs.search(query, top_k=RAG_TOP_K)
    except Exception:
        return None

    paths = []
    for h in hits:
        fp = os.path.abspath(h.get("filepath") or "")
        if fp == root_abs or fp.startswith(root_abs + os.sep):
            if fp not in paths:
                paths.append(fp)

    blocks: list[str] = []
    total = 0
    for fp in paths:
        rel = os.path.relpath(fp, root_abs)
        sig = _file_signatures(fp)
        block = f"\n## {rel}\n{sig}" if sig else f"\n## {rel}"
        if total + len(block) > max_chars and blocks:
            break
        blocks.append(block)
        total += len(block)

    if not blocks:
        return None

    header = f"# Project: {root_abs}  (similarity-selected files)"
    text = header + "".join(blocks)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0] + "\n... (truncated)"
    return text


_TREE_HEADER = "# File tree (code/text files only)"


def build_repo_map(
    query: str,
    workdir: str,
    *,
    max_chars: int = REPO_MAP_CHAR_CAP,
) -> dict:
    """
    Build the project map injected into the agent's context.

    Returns {"text": str, "focused": bool}. `text` is the map (may be
    empty when nothing indexable exists); `focused` is True when the
    project was too large and the map was narrowed by similarity search.
    """
    root = os.path.abspath(os.path.expanduser(workdir))
    files = _collect_files(root)
    if not files:
        return {"text": "", "focused": False}

    full = _build_full_map(files, root)
    if len(full) <= max_chars:
        return {"text": full, "focused": False}

    focused = _build_focused_map(query, root, max_chars)
    if focused is not None:
        return {"text": focused, "focused": True}

    truncated = full[:max_chars].rsplit("\n", 1)[0]
    return {
        "text": (truncated + "\n\n# ... (map truncated to fit context; "
                 "the project is large and not indexed — run the `index` "
                 "command on it to enable similarity-based narrowing)"),
        "focused": False,
    }