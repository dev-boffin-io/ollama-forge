"""
Code Audit — AI-powered git diff review.

Improvements:
- Sensitive file filtering (configurable)
- Rich diff summary table
- Uses prompt template engine
- Shows file-by-file breakdown
"""

from __future__ import annotations

import fnmatch
import os
import re

from core.shell import run_git, RunResult

# Default sensitive patterns to exclude from diff
DEFAULT_SENSITIVE = [
    "*.pem", "*.key", "*.p12", "*.pfx",
    "id_rsa", "id_rsa.pub", "id_ed25519",
    ".env", ".env.*", "*secret*", "*credential*",
    "*.lock",  # lock files add noise without value
]


def run(text: str = "") -> None:
    _print("🔍 Running code audit...\n")

    # Sensitive file filtering
    sensitive = _load_sensitive_patterns()
    skip_sensitive = "--no-sensitive" not in text

    diff = _get_best_diff()
    if not diff:
        _print("✅ No changes detected (nothing staged or committed yet).")
        return

    # Apply sensitive filter
    if skip_sensitive:
        diff, skipped_files = _filter_sensitive(diff, sensitive)
        if skipped_files:
            _print(f"🔒 Skipped {len(skipped_files)} sensitive file(s):")
            for f in skipped_files:
                _print(f"   {f}")
            _print("")

    lines = diff.splitlines()
    if not lines:
        _print("✅ No changes left after filtering sensitive files.")
        return

    _print(f"📋 {len(lines)} lines of changes.\n")
    _print_diff_summary(diff)

    # Build prompt via template
    try:
        from core.prompts import render
        prompt = render("code_audit", diff=diff[:3000])
    except Exception:
        prompt = (
            "Review this git diff for bugs, security issues, and improvements.\n"
            "Be concise. Format: bullet points only.\n\n"
            f"```diff\n{diff[:3000]}\n```"
        )

    try:
        from core.ai import ask_ai
        _print("🤖 AI Review:\n")
        ask_ai(prompt)
    except Exception as exc:
        _print(f"⚠️  AI unavailable: {exc}")


def _get_best_diff() -> str:
    """Try staged → HEAD → unstaged."""
    for flag in (["--cached"], ["HEAD"], []):
        res = run_git("diff", *(flag + ["--unified=3"]))
        if res.ok and res.stdout.strip():
            return res.stdout.strip()
    return ""


def _load_sensitive_patterns() -> list[str]:
    """Load sensitive patterns from config."""
    try:
        from core.config import load_config
        cfg = load_config()
        if hasattr(cfg, "audit"):
            return cfg.audit.sensitive_patterns
        return cfg.get("audit", {}).get("sensitive_patterns", DEFAULT_SENSITIVE)
    except Exception:
        return DEFAULT_SENSITIVE


def _filter_sensitive(diff: str, patterns: list[str]) -> tuple[str, list[str]]:
    """
    Remove hunks belonging to sensitive files from diff.
    Returns (filtered_diff, list_of_skipped_files).
    """
    # Parse diff into per-file sections
    file_sections: list[tuple[str, str]] = []  # (filename, section_text)
    current_file = None
    current_lines: list[str] = []

    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git"):
            if current_file is not None:
                file_sections.append((current_file, "".join(current_lines)))
            # Extract filename from "diff --git a/foo b/foo"
            m = re.search(r"b/(.+)$", line.strip())
            current_file = m.group(1) if m else ""
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_file is not None:
        file_sections.append((current_file, "".join(current_lines)))

    kept = []
    skipped = []
    for filename, section in file_sections:
        basename = os.path.basename(filename)
        if any(fnmatch.fnmatch(basename, pat) for pat in patterns):
            skipped.append(filename)
        else:
            kept.append(section)

    return "".join(kept), skipped


def _print_diff_summary(diff: str) -> None:
    lines = diff.splitlines()
    added = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))

    # Count changed files
    changed_files = [l for l in lines if l.startswith("diff --git")]

    try:
        from rich.table import Table
        from rich.console import Console
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Metric", style="bold")
        table.add_column("Value")
        table.add_row("Files changed", str(len(changed_files)))
        table.add_row("Lines added",   f"[green]+{added}[/green]")
        table.add_row("Lines removed", f"[red]-{removed}[/red]")
        Console().print(table)
        Console().print()
    except ImportError:
        print(f"  Files: {len(changed_files)}  ✚ {added}  ✖ {removed}\n")


def _print(msg: str) -> None:
    try:
        from rich.console import Console
        Console().print(msg)
    except ImportError:
        import re
        print(re.sub(r"\[/?[^\]]*\]", "", msg))
