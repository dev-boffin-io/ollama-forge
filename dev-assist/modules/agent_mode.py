"""
Agent mode — the CLI front-end for core.agent.

Renders what the agent is doing as it happens, shows real unified diffs
for pending file changes, asks the user before anything destructive
runs, and offers undo once the run finishes. Invoked from the router
via `do <task>` / `agent <task>` / `undo`.
"""

from __future__ import annotations

import difflib
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
    "run_tests": "🧪", "web_search": "🌐", "web_fetch": "🕸️",
    "todowrite": "✅", "question": "❓", "task": "🤖", "skill": "📚",
    "apply_patch": "🧩",
}


def _make_renderer(verbose: bool):
    from core import tui_status

    def on_event(kind: str, text: str) -> None:
        if kind == "tool":
            name = text.split(":", 1)[0]
            icon = _TOOL_ICONS.get(name, "•")
            tui_status.set_activity(text[:60])
            _print(f"  {icon} [cyan]{text}[/cyan]")
        elif kind == "plan":
            if _console:
                _console.print(Panel(text, title="Plan", border_style="blue"))
            else:
                _print(text)
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
# Diff preview — real unified diffs, not a naive old/new dump
# ─────────────────────────────────────────────────────────────────────
def _unified_diff(before: str, after: str, path: str) -> str:
    diff_lines = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=path, tofile=path, n=2,
    )
    return "".join(diff_lines)


def _render_diff(diff_text: str, path: str) -> None:
    if not diff_text.strip():
        _print(f"  [dim](no textual change to {path})[/dim]")
        return
    if _console:
        _console.print(Panel(
            Syntax(diff_text.rstrip(), "diff", theme="ansi_dark", word_wrap=True),
            title=f"[bold]{path}[/bold]", border_style="yellow", expand=False,
        ))
    else:
        print(diff_text)


def _preview_edit(args: dict, workdir: str) -> None:
    """
    Build the real before/after for an edit_file call by applying it
    in-memory against the actual file, so the preview includes real
    surrounding context and line numbers — not just the raw fragment.
    """
    path = args.get("path", "")
    full_path = os.path.join(workdir, path) if not os.path.isabs(path) else path
    old, new = args.get("old_string", ""), args.get("new_string", "")

    try:
        with open(full_path, encoding="utf-8", errors="replace") as f:
            before = f.read()
    except Exception:
        # File doesn't exist / unreadable — fall back to a bare fragment diff
        _render_diff(_unified_diff(old, new, path), path)
        return

    if before.count(old) != 1:
        # Same ambiguity edit_file itself will reject; just show the raw
        # intent so the user can still make an informed call.
        _render_diff(f"--- intended change ({'not found' if old not in before else 'not unique'} in file) ---\n"
                      + "".join(f"-{ln}\n" for ln in old.splitlines())
                      + "".join(f"+{ln}\n" for ln in new.splitlines()), path)
        return

    after = before.replace(old, new, 1)
    _render_diff(_unified_diff(before, after, path), path)


def _preview_write(args: dict, workdir: str) -> None:
    path = args.get("path", "")
    full_path = os.path.join(workdir, path) if not os.path.isabs(path) else path
    new = args.get("content", "")

    try:
        with open(full_path, encoding="utf-8", errors="replace") as f:
            before = f.read()
    except Exception:
        _print(f"  [green]new file[/green] {path} ({len(new.splitlines())} lines)")
        return

    _render_diff(_unified_diff(before, new, path), path)


def _preview_apply_patch(args: dict, workdir: str) -> None:
    """Preview an apply_patch call: show which files it will touch and the raw diff."""
    patch_text = args.get("patch_text", "")
    if not patch_text:
        _print("  [yellow]apply_patch: no patch text supplied[/yellow]")
        return

    from core.toolimpl import patch as patchlib

    try:
        hunks = patchlib.parse_patch(patch_text)
    except patchlib.PatchError as exc:
        _print(f"  [red]apply_patch: invalid patch ({exc})[/red]")
        return

    for h in hunks:
        if h.type == "add":
            _print(f"  [green]add[/green]    [bold]{h.path}[/bold]")
        elif h.type == "delete":
            _print(f"  [red]delete[/red] [bold]{h.path}[/bold]"
                   + (f" [dim]→ {h.move_path}[/dim]" if h.move_path else ""))
        else:
            _print(f"  [yellow]update[/yellow]  [bold]{h.path}[/bold]"
                   + (f" [dim]→ {h.move_path}[/dim]" if h.move_path else ""))

    shown = "\n".join(ln for ln in patch_text.splitlines() if ln.startswith((" ", "+", "-", "@@")) and not ln.startswith("+++") and not ln.startswith("---"))
    _render_diff(shown, "patch")


# ─────────────────────────────────────────────────────────────────────
# Approval
# ─────────────────────────────────────────────────────────────────────
class Approver:
    """
    Interactive approval gate. Remembers a session-wide 'always' answer
    per tool so the user isn't asked about every single edit in a long
    run. When the user declines, `last_reason` carries whatever they
    typed (or None), so the agent gets useful feedback instead of a
    bare refusal.
    """

    def __init__(self, workdir: str, *, auto_yes: bool = False) -> None:
        self.workdir = workdir
        self.auto_yes = auto_yes
        self.always: set[str] = set()
        self.last_reason: str | None = None

    def __call__(self, name: str, args: dict) -> bool:
        self.last_reason = None
        if self.auto_yes or name in self.always:
            return True

        if name == "edit_file":
            _preview_edit(args, self.workdir)
        elif name == "write_file":
            _preview_write(args, self.workdir)
        elif name == "apply_patch":
            _preview_apply_patch(args, self.workdir)
        elif name == "bash":
            _print(f"  [yellow]run[/yellow] [bold]{args.get('command', '')}[/bold]")

        try:
            answer = input("  approve? [y]es / [n]o / [a]lways: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False

        if answer in ("a", "always"):
            self.always.add(name)
            return True
        if answer in ("", "y", "yes"):
            return True

        # Declined — offer to say why, so the model can adapt instead of
        # just retrying the same thing blindly.
        try:
            reason = input("  reason (optional, Enter to skip): ").strip()
        except (EOFError, KeyboardInterrupt):
            reason = ""
        self.last_reason = reason or None
        return False


def make_approver(workdir: str, *, auto_yes: bool = False) -> Approver:
    return Approver(workdir, auto_yes=auto_yes)


# ─────────────────────────────────────────────────────────────────────
# Undo
# ─────────────────────────────────────────────────────────────────────
def undo(_text: str = "") -> None:
    """Router entry point for `undo`: revert the last agent run's changes."""
    from core.change_tracker import get_tracker

    tracker = get_tracker()
    if tracker is None or not tracker.has_changes():
        _print("[dim]Nothing to undo — no agent-made changes this session.[/dim]")
        return

    paths = tracker.touched_paths()
    _print(f"[yellow]This will revert {len(paths)} file(s):[/yellow]")
    for p in paths:
        _print(f"  [dim]{p}[/dim]")
    try:
        answer = input("undo these changes? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if answer not in ("y", "yes"):
        _print("[dim]Cancelled.[/dim]")
        return

    restored = tracker.undo()
    _print(f"[green]✔ Reverted {len(restored)} file(s).[/green]")


# ─────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────
_FLAG_RULES = (
    (r"\s--auto\b|\s--yolo\b", "auto"),
    (r"\s--yes\b|\s-y\b", "auto_yes"),
    (r"\s--verbose\b|\s-v\b", "verbose"),
)


def _parse_run_args(text: str) -> tuple[str, dict]:
    """Strip agent-mode flags from the raw user line.

    Returns (task, flags) with flags = {"auto", "auto_yes", "verbose"}.
    """
    flags = {"auto": False, "auto_yes": False, "verbose": False}
    for pattern, key in _FLAG_RULES:
        if re.search(pattern, text):
            flags[key] = True
            text = re.sub(pattern, "", text).strip()
    return text, flags


def run(text: str) -> None:
    """Router entry point. `text` is the raw user line."""
    task, flags = _parse_run_args(text)
    task = re.sub(r"^\s*(do|agent)\b[:\s]*", "", task, flags=re.I).strip()

    if not task:
        _print("[yellow]Usage:[/yellow] do <task>    e.g. [dim]do fix the failing test in tests/[/dim]")
        _print("[dim]Flags: --auto/--yolo (run with no approval, tracked for undo), "
               "--yes (approve prompts automatically), --verbose (show tool output)[/dim]")
        _print("[dim]After a run: 'undo' reverts every file it changed.[/dim]")
        return

    run_task(task, flags=flags)


def run_task(
    task: str,
    *,
    workdir: str | None = None,
    flags: dict | None = None,
) -> str:
    """
    Run the agent on an arbitrary task and return its final answer.

    This is the shared executor behind `do <task>` and the `/` commands
    (`/init`, `/review`, config commands, skills). When a persistent session
    is active, the exchange is recorded into it.
    """
    flags = dict(flags or {})
    auto = bool(flags.get("auto"))
    auto_yes = bool(flags.get("auto_yes"))
    verbose = bool(flags.get("verbose"))

    if workdir is None:
        try:
            from modules.shell_exec import get_cwd
            workdir = get_cwd()
        except Exception:
            workdir = os.getcwd()
    _print(f"[bold cyan]🤖 agent[/bold cyan] [dim]{workdir}[/dim]")
    _print(f"[dim]task:[/dim] {task}\n")

    from core import tui_status
    from core.agent import run_agent
    from core.change_tracker import get_tracker

    if auto:
        # Fully autonomous: skip the interactive Approver entirely and use the
        # "approve everything" policy. Changes are still snapshotted by the
        # change tracker, so `undo` works exactly as it always has. The
        # question tool is also skipped so the agent can't stall waiting for
        # input it will never get (it receives "Unanswered" instead).
        from core.agent import approve_everything
        from core.tools import set_question_auto_skip
        set_question_auto_skip(True)
        if _console:
            _console.print(Panel(
                "[red]Running WITHOUT approval prompts.[/red]\n"
                "The agent may run any command and modify any file without asking.\n"
                "Every change is snapshotted — you can type [bold]undo[/bold] "
                "afterwards to revert them all.",
                title="⚠ Auto mode", border_style="red",
            ))
        else:
            _print("[warning] Running WITHOUT approval prompts (--auto).\n"
                   "The agent may run any command and modify any file without "
                   "asking. Every change is snapshotted — you can type 'undo' "
                   "afterwards to revert them all.")
        approver = approve_everything
    else:
        approver = make_approver(workdir, auto_yes=auto_yes)

    final = ""
    try:
        final = run_agent(
            task,
            workdir=workdir,
            approver=approver,
            on_event=_make_renderer(verbose),
        )
    except KeyboardInterrupt:
        _print("\n[yellow]Interrupted.[/yellow]")
    finally:
        tui_status.clear_activity()

    tracker = get_tracker()
    if tracker and tracker.has_changes():
        _print(f"\n[bold]{tracker.diffstat()}[/bold]  [dim](type 'undo' to revert)[/dim]")

    _record_turn(task, final)
    return final


def _record_turn(task: str, final: str) -> None:
    """Record the agent exchange into the persistent session (best effort)."""
    if not final:
        return
    try:
        from core.session import get_session
        sess = get_session()
        if sess.store_id:
            sess.add_user(task)
            sess.add_assistant(final)
    except Exception:
        pass

