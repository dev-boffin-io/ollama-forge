"""
Git Helper — Fix common git issues with AI-assisted explanations.

Improvements:
- Uses core.shell for robust subprocess handling
- AI explains errors in plain language
- Rich table output for status
"""

from __future__ import annotations

import os
from core.shell import run_git, RunResult


def run(text: str = "") -> None:
    text_lower = text.lower()

    if "conflict" in text_lower:
        _show_conflicts()
    elif "push" in text_lower:
        _fix_push()
    elif "pull" in text_lower:
        _fix_pull()
    elif "rebase" in text_lower:
        _guide_rebase()
    elif "sync" in text_lower or "branch" in text_lower:
        _sync_branch()
    else:
        _git_status()


def _git_status() -> None:
    _print("📊 Git Status:\n")

    status  = run_git("status", "--short")
    branch  = run_git("branch", "--show-current")
    log     = run_git("log", "--oneline", "-5")
    remote  = run_git("remote", "-v")

    branch_name = branch.stdout.strip() if branch.ok else "unknown"

    try:
        from rich.table import Table
        from rich.console import Console
        console = Console()
        console.print(f"  Branch  : [cyan]{branch_name}[/cyan]")
        if status.ok and status.stdout.strip():
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_column("Status", style="yellow")
            table.add_column("File")
            for line in status.stdout.strip().splitlines():
                if len(line) >= 3:
                    table.add_row(line[:2], line[3:])
            console.print(table)
        else:
            console.print("  Changes : [green]clean[/green]")

        if log.ok and log.stdout.strip():
            console.print("\n  Recent commits:")
            for l in log.stdout.strip().splitlines():
                console.print(f"    [dim]{l}[/dim]")
    except ImportError:
        print(f"  Branch: {branch_name}")
        print(f"  Changes:\n{status.stdout.strip() or '  (clean)'}")
        print(f"\n  Recent commits:\n{log.stdout.strip() or '  (none)'}")


def _fix_push() -> None:
    _print("🚀 Git Push Fixer\n")

    branch = run_git("branch", "--show-current")
    branch_name = branch.stdout.strip() if branch.ok else "main"

    # Try to detect the actual error
    test_push = run_git("push", "--dry-run", "origin", branch_name)
    if not test_push.ok:
        _explain_git_error(test_push, operation="push", branch=branch_name)

    _print(f"  Current branch: [cyan]{branch_name}[/cyan]\n")
    _print("  Common fixes:")
    _print(f"  [1] Force push (careful):  git push --force-with-lease origin {branch_name}")
    _print(f"  [2] Pull then push:         git pull --rebase origin {branch_name} && git push")
    _print(f"  [3] Set upstream:           git push -u origin {branch_name}")

    choice = input("\n  Apply fix [1/2/3/skip]: ").strip()
    if choice == "1":
        res = run_git("push", "--force-with-lease", "origin", branch_name)
        _print_result(res)
    elif choice == "2":
        r1 = run_git("pull", "--rebase", "origin", branch_name)
        _print_result(r1)
        if r1.ok:
            r2 = run_git("push")
            _print_result(r2)
    elif choice == "3":
        res = run_git("push", "-u", "origin", branch_name)
        _print_result(res)


def _fix_pull() -> None:
    _print("⬇️  Git Pull Fixer\n")
    _print("  [1] Stash local changes and pull")
    _print("  [2] Merge (default)")
    _print("  [3] Rebase")

    choice = input("\n  Choose [1/2/3/skip]: ").strip()
    if choice == "1":
        _print_result(run_git("stash"))
        _print_result(run_git("pull"))
        _print_result(run_git("stash", "pop"))
    elif choice == "2":
        _print_result(run_git("pull", "--no-rebase"))
    elif choice == "3":
        _print_result(run_git("pull", "--rebase"))


def _show_conflicts() -> None:
    _print("⚔️  Merge Conflict Resolver\n")

    conflicted = run_git("diff", "--name-only", "--diff-filter=U")
    if not conflicted.ok or not conflicted.stdout.strip():
        _print("  ✅ No conflicts found!")
        return

    files = conflicted.stdout.strip().splitlines()
    _print(f"  Conflicted files ({len(files)}):")
    for f in files:
        _print(f"    [yellow]•[/yellow] {f}")

    _print("\n  Quick actions:")
    _print("  [a] Accept ours (current branch) for all")
    _print("  [b] Accept theirs (incoming) for all")
    _print("  [t] Open mergetool")
    _print("  [x] Abort merge")

    choice = input("\n  Choice [a/b/t/x/skip]: ").strip().lower()
    if choice == "a":
        for f in files:
            _print_result(run_git("checkout", "--ours", f))
        _print_result(run_git("add", "."))
    elif choice == "b":
        for f in files:
            _print_result(run_git("checkout", "--theirs", f))
        _print_result(run_git("add", "."))
    elif choice == "t":
        _print_result(run_git("mergetool"))
    elif choice == "x":
        _print_result(run_git("merge", "--abort"))


def _guide_rebase() -> None:
    branch = run_git("branch", "--show-current")
    branch_name = branch.stdout.strip() if branch.ok else "main"
    _print(f"🔀 Interactive Rebase Guide  (branch: [cyan]{branch_name}[/cyan])\n")
    _print("  Rebase onto main:")
    _print("    git fetch origin")
    _print("    git rebase origin/main")
    _print("  Interactive (squash/edit commits):")
    _print("    git rebase -i HEAD~3")
    _print("  After fixing conflicts:")
    _print("    git rebase --continue")
    _print("  Abort rebase:")
    _print("    git rebase --abort")


def _sync_branch() -> None:
    branch = run_git("branch", "--show-current")
    branch_name = branch.stdout.strip() if branch.ok else "main"
    _print(f"🔄 Syncing branch '[cyan]{branch_name}[/cyan]' with origin/main...\n")

    choice = input("  Run: git fetch + rebase? [y/N]: ").strip().lower()
    if choice == "y":
        _print_result(run_git("fetch", "origin"))
        _print_result(run_git("rebase", "origin/main"))


def _explain_git_error(result: RunResult, operation: str = "", branch: str = "") -> None:
    """Ask AI to explain a git error in plain language."""
    error_text = result.stderr.strip() or result.stdout.strip()
    if not error_text:
        return
    try:
        from core.prompts import render
        from core.ai import ask_ai
        prompt = render(
            "git_fix",
            error_output=error_text,
            branch=branch,
            operation=operation,
        )
        _print("🤖 AI explanation:\n")
        ask_ai(prompt)
    except Exception:
        _print(result.friendly_error())


def _print_result(result: RunResult) -> None:
    if result.ok:
        if result.output:
            _print(result.output)
    else:
        _print(result.friendly_error())


def _print(msg: str) -> None:
    try:
        from rich.console import Console
        Console().print(msg)
    except ImportError:
        import re
        print(re.sub(r"\[/?[^\]]*\]", "", msg))
