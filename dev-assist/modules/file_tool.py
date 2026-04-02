"""
File Tool — Bulk file operations with permission checks and dry-run mode.

Improvements:
- Permission check before any destructive operation
- --dry-run mode (preview without executing)
- Sensitive file detection (skips .env, *.key, etc.)
- Rich table output for listings
"""

from __future__ import annotations

import fnmatch
import os
import re
import glob
import shutil
import stat
from pathlib import Path

# Files that should never be auto-deleted or modified
SENSITIVE_PATTERNS = [
    "*.key", "*.pem", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*",
    ".env", ".env.*", "*_secret*", "*password*", "*credential*",
    "*.db", "*.sqlite", "*.sqlite3",
]


def run(text: str = "") -> None:
    text_lower = text.lower()

    if "rename" in text_lower:
        _bulk_rename(text)
    elif "clean" in text_lower:
        _cleanup(text)
    elif "find" in text_lower:
        _find_files(text)
    elif "replace" in text_lower or "search" in text_lower:
        _search_replace(text)
    else:
        _print_help()


def _print_help() -> None:
    _out("""📁 File Tool Commands:
  rename *.txt → *.md       (bulk extension rename)
  clean                     (remove temp/cache files)
  clean --dry-run           (preview only, no deletion)
  find *.log                (find files by pattern)
  replace foo bar *.py      (search-replace in files)""")


# ── Bulk rename ───────────────────────────────────────────────────────────────

def _bulk_rename(text: str, dry_run: bool = False) -> None:
    dry_run = dry_run or "--dry-run" in text
    _out("📝 Bulk Rename" + (" [DRY RUN]" if dry_run else "") + "\n")

    pattern = input("  Source pattern (e.g. *.txt): ").strip()
    if not pattern:
        return

    files = glob.glob(pattern)
    if not files:
        _out(f"  No files match: {pattern}")
        return

    ext_match = re.search(r"\*\.(\w+)", pattern)
    if not ext_match:
        _out("  ⚠️  Pattern must include extension like *.txt")
        return

    old_ext = ext_match.group(1)
    new_ext = input(f"  New extension (replaces .{old_ext}): ").strip().lstrip(".")

    # Permission check + sensitive file check
    blocked = []
    safe = []
    for f in files:
        if _is_sensitive(f):
            blocked.append(f)
        elif not _check_writable(f):
            blocked.append(f)
        else:
            safe.append(f)

    if blocked:
        _out(f"\n  ⚠️  Skipping {len(blocked)} protected/unwritable files:")
        for b in blocked[:5]:
            _out(f"    🔒 {b}")

    if not safe:
        _out("  Nothing left to rename.")
        return

    _out(f"\n  Will rename {len(safe)} files: .{old_ext} → .{new_ext}")
    for f in safe[:5]:
        new_name = f.replace(f".{old_ext}", f".{new_ext}")
        _out(f"    {f} → {new_name}")
    if len(safe) > 5:
        _out(f"    ... and {len(safe)-5} more")

    if dry_run:
        _out("\n  [DRY RUN] No files were changed.")
        return

    confirm = input("\n  Proceed? [y/N]: ").strip().lower()
    if confirm == "y":
        for f in safe:
            new_name = f.replace(f".{old_ext}", f".{new_ext}")
            os.rename(f, new_name)
        _out(f"  ✅ Renamed {len(safe)} files.")


# ── Cleanup ───────────────────────────────────────────────────────────────────

def _cleanup(text: str = "") -> None:
    dry_run = "--dry-run" in text
    _out("🗑️  Cleanup" + (" [DRY RUN]" if dry_run else "") + "\n")

    patterns = [
        "**/__pycache__", "**/*.pyc", "**/*.pyo",
        "**/.DS_Store", "**/Thumbs.db",
        "**/*.tmp", "**/*.bak",
        "**/.ruff_cache", "**/.mypy_cache", "**/.pytest_cache",
    ]

    found = []
    for pat in patterns:
        for path in glob.glob(pat, recursive=True):
            if not _is_sensitive(path):
                found.append(path)

    if not found:
        _out("✅ Already clean — no temp files found.")
        return

    total_size = sum(
        os.path.getsize(f) for f in found if os.path.isfile(f)
    )

    try:
        from rich.table import Table
        from rich.console import Console
        table = Table(title=f"Temp files ({total_size // 1024} KB)", show_lines=False)
        table.add_column("Path", style="dim")
        table.add_column("Type", style="cyan")
        for f in found[:15]:
            ftype = "dir" if os.path.isdir(f) else "file"
            table.add_row(f, ftype)
        if len(found) > 15:
            table.add_row(f"... and {len(found)-15} more", "")
        Console().print(table)
    except ImportError:
        _out(f"Found {len(found)} temp files ({total_size // 1024} KB):\n")
        for f in found[:10]:
            _out(f"   {f}")
        if len(found) > 10:
            _out(f"   ... and {len(found)-10} more")

    if dry_run:
        _out("\n[DRY RUN] No files were deleted.")
        return

    confirm = input("\n  Delete all? [y/N]: ").strip().lower()
    if confirm == "y":
        errors = 0
        for f in found:
            if not _check_writable(f):
                errors += 1
                continue
            try:
                if os.path.isfile(f):
                    os.remove(f)
                elif os.path.isdir(f):
                    shutil.rmtree(f)
            except Exception as exc:
                _out(f"  ⚠️  Skip {f}: {exc}")
                errors += 1
        msg = f"✅ Cleaned {len(found) - errors} items."
        if errors:
            msg += f" ({errors} skipped due to permissions)"
        _out(msg)


# ── Find files ────────────────────────────────────────────────────────────────

def _find_files(text: str) -> None:
    words = text.split()
    pattern = next((w for w in words if "*" in w or "." in w), "*.py")

    _out(f"🔎 Finding: {pattern}\n")
    files = glob.glob(f"**/{pattern}", recursive=True)

    if not files:
        _out(f"  No files found matching: {pattern}")
        return

    try:
        from rich.table import Table
        from rich.console import Console
        table = Table(show_lines=False)
        table.add_column("File", style="cyan")
        table.add_column("Size", justify="right")
        table.add_column("Writable", justify="center")
        for f in files:
            size = os.path.getsize(f) if os.path.isfile(f) else 0
            writable = "✓" if _check_writable(f) else "🔒"
            table.add_row(f, f"{size:,} bytes", writable)
        Console().print(table)
        Console().print(f"Total: {len(files)} files")
    except ImportError:
        for f in files:
            size = os.path.getsize(f) if os.path.isfile(f) else 0
            lock = "" if _check_writable(f) else " 🔒"
            _out(f"  {f}  ({size} bytes){lock}")
        _out(f"\n  Total: {len(files)} files")


# ── Search & replace ──────────────────────────────────────────────────────────

def _search_replace(text: str) -> None:
    dry_run = "--dry-run" in text
    _out("🔁 Search & Replace" + (" [DRY RUN]" if dry_run else "") + "\n")

    search_str = input("  Search for: ").strip()
    if not search_str:
        return

    replace_str = input("  Replace with: ").strip()
    file_pattern = input("  In files (e.g. *.py): ").strip() or "*.py"

    files = glob.glob(f"**/{file_pattern}", recursive=True)
    matched = []

    for f in files:
        if not os.path.isfile(f) or _is_sensitive(f):
            continue
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fh:
                content = fh.read()
            if search_str in content:
                matched.append((f, content.count(search_str)))
        except Exception:
            pass

    if not matched:
        _out(f"  '{search_str}' not found in any {file_pattern} files.")
        return

    _out(f"\n  Found '{search_str}' in {len(matched)} files:")
    for f, count in matched:
        lock = "" if _check_writable(f) else " 🔒"
        _out(f"    {f} ({count} occurrences){lock}")

    if dry_run:
        _out("\n[DRY RUN] No files were changed.")
        return

    confirm = input("\n  Replace all? [y/N]: ").strip().lower()
    if confirm == "y":
        done = 0
        for f, _ in matched:
            if not _check_writable(f):
                _out(f"  ⚠️  Skipping {f} (no write permission)")
                continue
            with open(f, "r", encoding="utf-8") as fh:
                content = fh.read()
            new_content = content.replace(search_str, replace_str)
            with open(f, "w", encoding="utf-8") as fh:
                fh.write(new_content)
            done += 1
        _out(f"  ✅ Replaced in {done} files.")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_sensitive(path: str) -> bool:
    """Return True if file matches sensitive patterns and should be protected."""
    name = os.path.basename(path)
    return any(fnmatch.fnmatch(name, pat) for pat in SENSITIVE_PATTERNS)


def _check_writable(path: str) -> bool:
    """Return True if current user can write to this path."""
    try:
        return os.access(path, os.W_OK)
    except Exception:
        return False


def _out(msg: str) -> None:
    try:
        from rich.console import Console
        Console().print(msg)
    except ImportError:
        print(msg)
