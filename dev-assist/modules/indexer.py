"""
Indexer — Scan any local folder and chunk code/text files.

Improvements over v1:
- Semantic chunking: splits Python/Go/JS at function/class boundaries
- nomic-embed-text or sentence-transformers embeddings (optional)
- Rich progress bar output
- Skips binary files safely
- Re-indexes only changed files (mtime-based)

Usage (REPL):
  index /path/to/your/project
  index .
  index status
  index clear
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path
from typing import Iterator

from core.vector_store import save_chunks, get_stats, clear_index

# ── Supported extensions ─────────────────────────────────────────────────────
CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".go", ".rs", ".c", ".cpp", ".h", ".hpp",
    ".java", ".kt", ".rb", ".php", ".swift",
    ".sh", ".bash", ".zsh",
    ".md", ".txt", ".rst",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".html", ".css", ".scss",
    ".sql",
}

SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "env", ".env", "dist", "build", ".idea", ".vscode",
    "*.egg-info", ".pytest_cache", "htmlcov", ".mypy_cache",
    ".ruff_cache", "target",  # Rust
}

MAX_FILE_SIZE_KB = 500
CHUNK_SIZE = 50         # lines per chunk (reduced for better granularity)
CHUNK_OVERLAP = 8       # lines overlap between chunks

# Patterns that signal a semantic boundary (start of function/class)
_BOUNDARY_PATTERNS = [
    re.compile(r"^(def |class |async def )", re.MULTILINE),          # Python
    re.compile(r"^(func |type \w+ struct|type \w+ interface)", re.MULTILINE),  # Go
    re.compile(r"^(function |class |export (default )?function |const \w+ = \()", re.MULTILINE),  # JS/TS
    re.compile(r"^(pub fn |fn |impl |struct |enum |trait )", re.MULTILINE),    # Rust
    re.compile(r"^(public |private |protected |class )", re.MULTILINE),        # Java/Kotlin
]


def run(text: str = "") -> None:
    """Main entry — dispatch index commands."""
    parts = text.strip().split(None, 1)
    cmd = parts[0].lower() if parts else "index"
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("index", "idx") and not arg:
        _show_usage()
    elif arg.lower() == "status" or "status" in text.lower():
        _show_status()
    elif "clear" in text.lower():
        _do_clear()
    else:
        path = _extract_path(text)
        if path:
            index_folder(path)
        else:
            _show_usage()


def index_folder(folder: str) -> None:
    """Scan folder and index all supported files."""
    folder = os.path.expanduser(folder)
    folder = os.path.abspath(folder)

    if not os.path.isdir(folder):
        _print(f"⚠️  Not a directory: {folder}")
        return

    _print(f"🔍 Scanning: {folder}\n")

    files = list(_collect_files(folder))
    if not files:
        _print("⚠️  No supported files found.")
        return

    _print(f"📁 Found {len(files)} files to index...\n")

    indexed = 0
    skipped = 0
    total_chunks = 0
    t0 = time.time()

    # Try rich progress bar
    try:
        from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
        from rich.console import Console
        console = Console()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Indexing...", total=len(files))

            for filepath in files:
                rel = os.path.relpath(filepath, folder)
                try:
                    chunks = _chunk_file(filepath)
                    if chunks:
                        save_chunks(filepath, chunks)
                        total_chunks += len(chunks)
                        indexed += 1
                        progress.update(task, advance=1, description=f"[cyan]{rel[:50]}")
                    else:
                        skipped += 1
                        progress.update(task, advance=1)
                except Exception as exc:
                    skipped += 1
                    progress.update(task, advance=1)

    except ImportError:
        # Plain fallback
        for filepath in files:
            rel = os.path.relpath(filepath, folder)
            try:
                chunks = _chunk_file(filepath)
                if chunks:
                    save_chunks(filepath, chunks)
                    total_chunks += len(chunks)
                    indexed += 1
                    print(f"  ✓ {rel:<50} ({len(chunks)} chunks)")
                else:
                    skipped += 1
            except Exception as exc:
                print(f"  ✗ {rel:<50} (skip: {exc})")
                skipped += 1

    elapsed = time.time() - t0

    # Update session with indexed path
    try:
        from core.session import get_session
        get_session().set_indexed_path(folder)
    except Exception:
        pass

    _print(f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
✅ Index complete!  ({elapsed:.1f}s)
   Files indexed : {indexed}
   Files skipped : {skipped}
   Total chunks  : {total_chunks}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Now ask anything:
  > What does main.py do?
  > Explain this project's architecture
  > Are there any bugs?
""")


# ── File collection ──────────────────────────────────────────────────────────

def _collect_files(folder: str) -> Iterator[str]:
    """Walk folder tree and yield all indexable files."""
    for root, dirs, files in os.walk(folder):
        # Skip unwanted dirs in-place
        dirs[:] = [
            d for d in dirs
            if d not in SKIP_DIRS and not d.startswith(".")
        ]
        for fname in sorted(files):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in CODE_EXTENSIONS:
                continue
            fpath = os.path.join(root, fname)
            try:
                size_kb = os.path.getsize(fpath) / 1024
                if size_kb > MAX_FILE_SIZE_KB:
                    continue
                # Quick binary check
                if _is_binary(fpath):
                    continue
            except OSError:
                continue
            yield fpath


def _is_binary(filepath: str) -> bool:
    """Heuristic binary check — read first 8KB."""
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(8192)
        return b"\x00" in chunk
    except OSError:
        return True


# ── Chunking ─────────────────────────────────────────────────────────────────

def _chunk_file(filepath: str) -> list[dict]:
    """
    Read file and split into chunks.

    Strategy:
    - Python/Go/JS/TS/Rust/Java: try semantic splitting at boundaries
    - Everything else: fixed sliding window with overlap
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError:
        return []

    if not content.strip():
        return []

    ext = Path(filepath).suffix.lower()

    if ext in (".py", ".go", ".js", ".ts", ".jsx", ".tsx", ".rs", ".java", ".kt"):
        chunks = _semantic_chunks(filepath, content)
        if chunks:
            return chunks

    # Fallback: line-based sliding window
    return _line_chunks(filepath, content)


def _semantic_chunks(filepath: str, content: str) -> list[dict]:
    """
    Split at function/class boundaries for better retrieval quality.
    Each chunk = one logical unit (function, class, method).
    Falls back to line chunks if no boundaries found.
    """
    lines = content.splitlines(keepends=True)
    if not lines:
        return []

    # Find boundary line numbers
    boundary_lines: list[int] = [0]
    for pattern in _BOUNDARY_PATTERNS:
        for m in pattern.finditer(content):
            line_no = content[:m.start()].count("\n")
            boundary_lines.append(line_no)

    boundary_lines = sorted(set(boundary_lines))
    boundary_lines.append(len(lines))  # sentinel

    if len(boundary_lines) <= 2:
        return []  # no boundaries → use line chunks

    chunks = []
    for i in range(len(boundary_lines) - 1):
        start = boundary_lines[i]
        end = boundary_lines[i + 1]

        # Merge tiny sections with next
        if end - start < 5 and i + 1 < len(boundary_lines) - 1:
            continue

        # Cap very large sections
        if end - start > 80:
            # Sub-chunk with overlap
            sub = _line_chunks_range(filepath, lines, start, end)
            chunks.extend(sub)
            continue

        chunk_lines = lines[start:end]
        chunk_content = "".join(chunk_lines).strip()
        if chunk_content:
            chunks.append({
                "content": f"# File: {filepath}\n# Lines: {start+1}-{end}\n\n{chunk_content}",
                "start_line": start + 1,
                "end_line": end,
                "filepath": filepath,
            })

    return chunks


def _line_chunks(filepath: str, content: str) -> list[dict]:
    """Fixed sliding-window chunking."""
    lines = content.splitlines(keepends=True)
    return _line_chunks_range(filepath, lines, 0, len(lines))


def _line_chunks_range(
    filepath: str,
    lines: list[str],
    start: int,
    end: int,
) -> list[dict]:
    """Chunk a range of lines with overlap."""
    chunks = []
    step = CHUNK_SIZE - CHUNK_OVERLAP
    for s in range(start, end, step):
        e = min(s + CHUNK_SIZE, end)
        chunk_content = "".join(lines[s:e]).strip()
        if chunk_content:
            chunks.append({
                "content": f"# File: {filepath}\n# Lines: {s+1}-{e}\n\n{chunk_content}",
                "start_line": s + 1,
                "end_line": e,
                "filepath": filepath,
            })
        if e == end:
            break
    return chunks


# ── Status / helpers ─────────────────────────────────────────────────────────

def _show_status() -> None:
    stats = get_stats()
    if stats["total_files"] == 0:
        _print("📭 Index is empty. Run: index /path/to/project")
        return
    _print(f"""
📊 Index Status
   Files  : {stats['total_files']}
   Chunks : {stats['total_chunks']}

Indexed files:""")
    for f in stats["files"][:20]:
        _print(f"  • {f}")
    if len(stats["files"]) > 20:
        _print(f"  ... and {len(stats['files'])-20} more")


def _do_clear() -> None:
    confirm = input("⚠️  Clear entire index? [y/N]: ").strip().lower()
    if confirm == "y":
        clear_index()
        try:
            from core.session import get_session
            get_session().set_indexed_path(None)
        except Exception:
            pass
        _print("✅ Index cleared.")


def _extract_path(text: str) -> str:
    """Extract a file path from command text."""
    match = re.search(r"(?:index|idx)\s+([^\s]+)", text, re.IGNORECASE)
    if match:
        return match.group(1)
    tokens = text.split()
    for tok in tokens:
        if tok.startswith("/") or tok.startswith("./") or tok == ".":
            return tok
    return ""


def _show_usage() -> None:
    _print("""
📁 Indexer — usage:
  index /path/to/project    ← index a folder
  index .                   ← index current directory
  index status              ← show what's indexed
  index clear               ← wipe the index
""")


def _print(msg: str) -> None:
    try:
        from rich.console import Console
        Console().print(msg)
    except ImportError:
        print(msg)
