"""
Agent mode — the CLI front-end for core.agent.

Renders what the agent is doing as it happens, and asks the user before
anything destructive runs. Invoked from the router via `do <task>` /
`agent <task>`.
"""

from __future__ import annotations

import os
import re

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.syntax import Syntax
    _console = Console()
except Exception:
    _console = None


def _print(msg: str) -> None:
    if _console:
        _console.print(msg)
    else:
        print(re.sub(r"\[/?[a-z ]+\]", "", msg))


# ─────────────────────────────────────────────────────────────────────
# Rendering
# ─────────────────────────────────────────────────────────────────────
_TOOL_ICONS = {
    "read_file": "📖", "list_dir": "📁", "glob": "🔍", "grep": "🔎",
    "write_file": "📝", "edit_file": "✏️", "bash": "⚙️",
}


def _make_renderer(verbose: bool):
    def on_event(kind: str, text: str) -> None:
        if kind == "tool":
            name = text.split(":", 1)[0]
            icon = _TOOL_ICONS.get(name, "•")
            _print(f"  {icon} [cyan]{text}[/cyan]")
        elif kind == "result":
            if not verbose:
                return
            preview = text.strip().splitlines()
            shown = "\n".join(f"     [dim]{ln[:120]}[/dim]" for ln in preview[:6])
            if len(preview) > 6:
                shown += f"\n     [dim]… (+{len(preview) - 6} more lines)[/dim]"
            if shown:
                _print(shown)
        elif kind == "warn":
            _print(f"  [yellow]⚠ {text}[/yellow]")
        elif kind == "text":
            _print(f"\n[white]{text}[/white]\n")
    return on_event


# ─────────────────────────────────────────────────────────────────────
# Approval
# ─────────────────────────────────────────────────────────────────────
def _preview_edit(args: dict, workdir: str) -> None:
    """Show a minimal before/after for an edit so approval is informed."""
    old = args.get("old_string", "")
    new = args.get("new_string", "")
    body = ""
    for line in old.splitlines() or [""]:
        body += f"- {line}\n"
    for line in new.splitlines() or [""]:
        body += f"+ {line}\n"
    if _console:
        _console.print(Panel(
            Syntax(body.rstrip(), "diff", theme="ansi_dark", word_wrap=True),
            title=f"[bold]{args.get('path','')}[/bold]",
            border_style="yellow", expand=False,
        ))
    else:
        print(body)


def make_approver(workdir: str, *, auto_yes: bool = False):
    """
    Interactive approval gate. Remembers a session-wide 'always' answer
    per tool so the user isn't asked about every single edit in a long run.
    """
    always: set[str] = set()

    def approve(name: str, args: dict) -> bool:
        if auto_yes or name in always:
            return True

        if name == "edit_file":
            _preview_edit(args, workdir)
        elif name == "write_file":
            content = args.get("content", "")
            _print(f"  [yellow]write[/yellow] {args.get('path','')} "
                   f"({len(content.splitlines())} lines)")
        elif name == "bash":
            _print(f"  [yellow]run[/yellow] [bold]{args.get('command','')}[/bold]")

        try:
            answer = input("  approve? [y]es / [n]o / [a]lways: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False

        if answer in ("a", "always"):
            always.add(name)
            return True
        return answer in ("", "y", "yes")

    return approve


# ─────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────
def run(text: str) -> None:
    """Router entry point. `text` is the raw user line."""
    task = re.sub(r"^\s*(do|agent)\b[:\s]*", "", text, flags=re.I).strip()

    auto_yes = False
    if re.search(r"\s--yes\b|\s-y\b", task):
        auto_yes = True
        task = re.sub(r"\s--yes\b|\s-y\b", "", task).strip()

    verbose = False
    if re.search(r"\s--verbose\b|\s-v\b", task):
        verbose = True
        task = re.sub(r"\s--verbose\b|\s-v\b", "", task).strip()

    if not task:
        _print("[yellow]Usage:[/yellow] do <task>    e.g. [dim]do fix the failing test in tests/[/dim]")
        _print("[dim]Flags: --yes (skip approval prompts), --verbose (show tool output)[/dim]")
        return

    workdir = os.getcwd()
    _print(f"[bold cyan]🤖 agent[/bold cyan] [dim]{workdir}[/dim]")
    _print(f"[dim]task:[/dim] {task}\n")

    from core.agent import run_agent

    try:
        run_agent(
            task,
            workdir=workdir,
            approver=make_approver(workdir, auto_yes=auto_yes),
            on_event=_make_renderer(verbose),
        )
    except KeyboardInterrupt:
        _print("\n[yellow]Interrupted.[/yellow]")
