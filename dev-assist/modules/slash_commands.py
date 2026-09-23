"""
Slash (`/`) commands — port of opencode's command system.

opencode exposes commands through the `/` prefix: two built-ins (`/init`,
`/review`), user-defined commands from config (`command` section), and skills
(as `/skill_name`). Typing `/` at the prompt opens the command palette and
completion; `ctrl+p` does the same anywhere in the input.

This module ports that system onto dev-assist's REPL:

  /init         guided AGENTS.md setup
  /review       review changes [commit|branch|pr] (defaults to uncommitted)
  /help         list every slash command with its hints
  /new          start a fresh persistent session
  /sessions     list saved sessions (most recent first)
  /resume       resume a saved session  (/resume #2 or /resume <id>)
  /rename       rename the current session
  /delete       delete a saved session
  /<config>     user commands from the `commands` section of settings.json
  /<skill>      any discovered skill, run as a command

Template rendering ($1..$N, $ARGUMENTS, $N continuation semantics) mirrors
opencode's prompt.ts command pipeline: the highest-numbered `$N` captures all
remaining arguments, missing placeholders render empty, and arguments are
appended when the template has no placeholders to consume them.

Copyright (c) 2025 dev-assist contributors. MIT License.
Commit templates initialize.txt / review.txt are ported from opencode
(Copyright (c) 2025 opencode, MIT License).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from rich.console import Console

_console = Console()


# ── Templates (ported from opencode) ─────────────────────────────────────────

PROMPT_INITIALIZE = """\
Create or update `AGENTS.md` for this repository.

The goal is a compact instruction file that helps future OpenCode sessions avoid mistakes and ramp up quickly. Every line should answer: "Would an agent likely miss this without help?" If not, leave it out.

User-provided focus or constraints (honor these):
$ARGUMENTS

## How to investigate

Read the highest-value sources first:
- `README*`, root manifests, workspace config, lockfiles
- build, test, lint, formatter, typecheck, and codegen config
- CI workflows and pre-commit / task runner config
- existing instruction files (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules/`, `.cursorrules`, `.github/copilot-instructions.md`)
- repo-local OpenCode config such as `opencode.json`

If architecture is still unclear after reading config and docs, inspect a small number of representative code files to find the real entrypoints, package boundaries, and execution flow. Prefer reading the files that explain how the system is wired together over random leaf files.

Prefer executable sources of truth over prose. If docs conflict with config or scripts, trust the executable source and only keep what you can verify.

## What to extract

Look for the highest-signal facts for an agent working in this repo:
- exact developer commands, especially non-obvious ones
- how to run a single test, a single package, or a focused verification step
- required command order when it matters, such as `lint -> typecheck -> test`
- monorepo or multi-package boundaries, ownership of major directories, and the real app/library entrypoints
- framework or toolchain quirks: generated code, migrations, codegen, build artifacts, special env loading, dev servers, infra deploy flow
- repo-specific style or workflow conventions that differ from defaults
- testing quirks: fixtures, integration test prerequisites, snapshot workflows, required services, flaky or expensive suites
- important constraints from existing instruction files worth preserving

Good `AGENTS.md` content is usually hard-earned context that took reading multiple files to infer.

## Questions

Only ask the user questions if the repo cannot answer something important. Use the `question` tool for one short batch at most.

Good questions:
- undocumented team conventions
- branch / PR / release expectations
- missing setup or test prerequisites that are known but not written down

Do not ask about anything the repo already makes clear.

## Writing rules

Include only high-signal, repo-specific guidance such as:
- exact commands and shortcuts the agent would otherwise guess wrong
- architecture notes that are not obvious from filenames
- conventions that differ from language or framework defaults
- setup requirements, environment quirks, and operational gotchas
- references to existing instruction sources that matter

Exclude:
- generic software advice
- long tutorials or exhaustive file trees
- obvious language conventions
- speculative claims or anything you could not verify
- content better stored in another file referenced via `opencode.json` `instructions`

When in doubt, omit.

Prefer short sections and bullets. If the repo is simple, keep the file simple. If the repo is large, summarize the few structural facts that actually change how an agent should work.

If `AGENTS.md` already exists at `${path}`, improve it in place rather than rewriting blindly. Preserve verified useful guidance, delete fluff or stale claims, and reconcile it with the current codebase.
"""

PROMPT_REVIEW = """\
You are a code reviewer. Your job is to review code changes and provide actionable feedback.

---

Input: $ARGUMENTS

---

## Determining What to Review

Based on the input provided, determine which type of review to perform:

1. **No arguments (default)**: Review all uncommitted changes
   - Run: `git diff` for unstaged changes
   - Run: `git diff --cached` for staged changes
   - Run: `git status --short` to identify untracked (net new) files

2. **Commit hash** (40-char SHA or short hash): Review that specific commit
   - Run: `git show $ARGUMENTS`

3. **Branch name**: Compare current branch to the specified branch
   - Run: `git diff $ARGUMENTS...HEAD`

4. **PR URL or number** (contains "github.com" or "pull" or looks like a PR number): Review the pull request
   - Run: `gh pr view $ARGUMENTS` to get PR context
   - Run: `gh pr diff $ARGUMENTS` to get the diff

Use best judgement when processing input.

---

## Gathering Context

**Diffs alone are not enough.** After getting the diff, read the entire file(s) being modified to understand the full context. Code that looks wrong in isolation may be correct given surrounding logic—and vice versa.

- Use the diff to identify which files changed
- Use `git status --short` to identify untracked files, then read their full contents
- Read the full file to understand existing patterns, control flow, and error handling
- Check for existing style guide or conventions files (CONVENTIONS.md, AGENTS.md, .editorconfig, etc.)

---

## What to Look For

**Bugs** - Your primary focus.
- Logic errors, off-by-one mistakes, incorrect conditionals
- If-else guards: missing guards, incorrect branching, unreachable code paths
- Edge cases: null/empty/undefined inputs, error conditions, race conditions
- Security issues: injection, auth bypass, data exposure
- Broken error handling that swallows failures, throws unexpectedly or returns error types that are not caught.

**Structure** - Does the code fit the codebase?
- Does it follow existing patterns and conventions?
- Are there established abstractions it should use but doesn't?
- Excessive nesting that could be flattened with early returns or extraction

**Performance** - Only flag if obviously problematic.
- O(n²) on unbounded data, N+1 queries, blocking I/O on hot paths

**Behavior Changes** - If a behavioral change is introduced, raise it (especially if it's possibly unintentional).

---

## Before You Flag Something

**Be certain.** If you're going to call something a bug, you need to be confident it actually is one.

- Only review the changes - do not review pre-existing code that wasn't modified
- Don't flag something as a bug if you're unsure - investigate first
- Don't invent hypothetical problems - if an edge case matters, explain the realistic scenario where it breaks
- If you need more context to be sure, use the tools below to get it

**Don't be a zealot about style.** When checking code against conventions:

- Verify the code is *actually* in violation. Don't complain about else statements if early returns are already being used correctly.
- Some "violations" are acceptable when they're the simplest option. A `let` statement is fine if the alternative is convoluted.
- Excessive nesting is a legitimate concern regardless of other style choices.
- Don't flag style preferences as issues unless they clearly violate established project conventions.

---

## Tools

Use these to inform your review:

- **Explore agent** - Find how existing code handles similar problems. Check patterns, conventions, and prior art before claiming something doesn't fit.
- **Web Search** - Research best practices if you're unsure about a pattern.

If you're uncertain about something and can't verify it with the tools, say "I'm not sure about X" rather than flagging it as a definite issue.

---

## Output

1. If there is a bug, be direct and clear about why it is a bug.
2. Clearly communicate severity of issues. Do not overstate severity.
3. Critiques should clearly and explicitly communicate the scenarios, environments, or inputs that are necessary for the bug to arise. The comment should immediately indicate that the issue's severity depends on these factors.
4. Your tone should be matter-of-fact and not accusatory or overly positive. It should read as a helpful AI assistant suggestion without sounding too much like a human reviewer.
5. Write so the reader can quickly understand the issue without reading too closely.
6. AVOID flattery, do not give any comments that are not helpful to the reader. Avoid phrasing like "Great job ...", "Thanks for ...".
"""


# ── Command model ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    source: str  # "builtin" | "command" | "skill"
    template: str | None = None
    subtask: bool = False
    handler: Callable[[CommandSpec, str, str], bool] | None = None
    hints: tuple[str, ...] = ()


def _cwd() -> str:
    try:
        from modules.shell_exec import get_cwd
        return get_cwd()
    except Exception:
        import os
        return os.getcwd()


def _print(msg: str) -> None:
    _console.print(msg)


# ── Argument parsing / template rendering (opencode port) ───────────────────

_QUOTED_TOKEN_RE = re.compile(r'"([^"]*)"|\'([^\']*)\'|(\S+)')
_NUM_PLACEHOLDER_RE = re.compile(r"\$(\d+)")


def parse_arguments(raw: str) -> list[str]:
    """Split a raw argument string into positional args, honouring quotes."""
    tokens: list[str] = []
    for a, b, c in _QUOTED_TOKEN_RE.findall(raw):
        tokens.append(a or b or c)
    return tokens


def hints(template: str) -> list[str]:
    """Variables a template can consume: $1..$N plus $ARGUMENTS."""
    result: list[str] = []
    for match in re.finditer(r"\$(\d+|\w+)", template):
        result.append("$" + match.group(1))
    if "$ARGUMENTS" in template and "$ARGUMENTS" not in result:
        result.insert(0, "$ARGUMENTS")
    seen: set[str] = set()
    unique: list[str] = []
    for h in result:
        if h not in seen:
            seen.add(h)
            unique.append(h)
    return unique


def render_template(template: str, raw_arguments: str, workdir: str) -> str:
    """
    Substitute `${path}`, `$N` positionals and `$ARGUMENTS` into a command
    template, mirroring opencode's prompt.ts pipeline:

      - the highest-numbered positional captures every remaining argument;
      - missing positionals render as an empty string;
      - if a template has no positional/ARGUMENTS placeholders, the raw
        arguments are appended below it.
    """
    template = template.replace("${path}", workdir)

    args = parse_arguments(raw_arguments)
    placeholders = _NUM_PLACEHOLDER_RE.findall(template)
    last = max((int(n) for n in placeholders), default=0)

    def _replace(match: re.Match) -> str:
        position = int(match.group(1))
        arg_index = position - 1
        if arg_index >= len(args):
            return ""
        if position == last:
            return " ".join(args[arg_index:])
        return args[arg_index]

    with_args = _NUM_PLACEHOLDER_RE.sub(_replace, template)
    uses_arguments = "$ARGUMENTS" in template

    text = with_args.replace("$ARGUMENTS", raw_arguments.strip())
    if not placeholders and not uses_arguments and raw_arguments.strip():
        text = text + "\n\n" + raw_arguments.strip()
    return text.strip()


# ── Command registry ─────────────────────────────────────────────────────────

def _config_commands() -> dict[str, dict]:
    """User-defined `commands` from settings.json (opencode 'command' section)."""
    try:
        from core.config import load_config
        cfg = load_config()
        if _is_app_config(cfg):
            return dict(cfg.commands or {})
        if isinstance(cfg, dict):
            raw = cfg.get("commands") or {}
            return raw if isinstance(raw, dict) else {}
    except Exception:
        pass
    return {}


def _is_app_config(cfg: object) -> bool:
    from core.config import AppConfig
    return isinstance(cfg, AppConfig)


def _skill_commands(workdir: str) -> dict[str, dict]:
    """Every discovered skill, exposed as a `/name` command."""
    out: dict[str, dict] = {}
    try:
        from core.toolimpl import skills
        for name in skills.list_skills(workdir):
            info = skills.load_skill(name, workdir)
            if info is None:
                continue
            content = str(info.get("content") or "")
            first_line = next((ln.strip() for ln in content.splitlines() if ln.strip()), "")
            out[name] = {
                "description": first_line[:80],
                "content": content,
                "base_dir": str(info.get("dir") or ""),
            }
    except Exception:
        pass
    return out


# ── Handlers ─────────────────────────────────────────────────────────────────

def _run_template(spec: CommandSpec, raw_args: str, workdir: str) -> bool:
    """Execute a template command via the agent (init/review/config/skill)."""
    template = render_template(spec.template or "", raw_args, workdir)
    from modules.agent_mode import run_task
    run_task(template, workdir=workdir)
    return True


def _cmd_new(spec: CommandSpec, raw_args: str, workdir: str) -> bool:
    from core import session_store
    _print(f"📂 New session started — [bold]session[/bold] "
           f"[cyan]{session_store.new_session(workdir).id[:8]}[/cyan]")
    return True


def _cmd_sessions(spec: CommandSpec, raw_args: str, workdir: str) -> bool:
    from core import session_store
    sessions = session_store.list_sessions(limit=20)
    if not sessions:
        _print("No saved sessions yet. Chat a bit, then run [bold]/sessions[/bold] again.")
        return True
    _print("\n📚 Saved sessions (latest first):\n")
    for i, s in enumerate(sessions, 1):
        label = s.title or "(no messages yet)"
        icon = "▶️ " if s.id == session_store.current_session_id() else "  "
        _print(f"  {icon}[bold]#{i}[/bold]  {label[:70]}  "
               f"[dim]{session_store.message_count(s.id)} msgs · {_stamp(s.updated)}[/dim]")
        _print(f"        [dim]{s.id}[/dim]  [bold]/resume #{i}[/bold] or [bold]/resume {s.id[:8]}[/bold]")
    _print("")
    return True


def _stamp(epoch: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")


def _resolve_session(ref: str):
    """Resolve `#N`, a full/prefix id, or a title substring to a session."""
    from core import session_store
    sessions = session_store.list_sessions()
    if ref.startswith("#"):
        try:
            idx = int(ref[1:]) - 1
        except ValueError:
            return None
        return sessions[idx] if 0 <= idx < len(sessions) else None
    for s in sessions:
        if s.id == ref or s.id.startswith(ref):
            return s
    matches = [s for s in sessions if ref.lower() in s.title.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        _print(f"⚠️  {len(matches)} sessions match {ref!r}. Use an id or [bold]/sessions[/bold] a number.")
    return None


def _cmd_resume(spec: CommandSpec, raw_args: str, workdir: str) -> bool:
    from core import session_store
    ref = raw_args.strip()
    if not ref:
        _print("Usage: [bold]/resume #<N>[/bold] or [bold]/resume <session-id>[/bold]  "
               "([bold]/sessions[/bold] to list)")
        return True
    target = _resolve_session(ref)
    if target is None:
        next_session = session_store.current_session()
        if next_session:
            _print(f"Stayed on [cyan]{next_session.id[:8]}[/cyan] "
                   f"({next_session.title or 'untitled'}).")
        return True
    restored = session_store.resume_session(target.id)
    if restored is not None:
        _print(f"▶️  Resumed [cyan]{restored.id[:8]}[/cyan] — {restored.title or '(no messages)'} "
               f"({session_store.message_count(restored.id)} messages)")
    return True


def _cmd_rename(spec: CommandSpec, raw_args: str, workdir: str) -> bool:
    from core import session_store
    sid = session_store.current_session_id()
    title = raw_args.strip()
    if not sid or not title:
        _print("Usage: [bold]/rename <new title>[/bold]")
        return True
    if session_store.rename_session(sid, title):
        _print(f"✏️  Session renamed to [bold]{title}[/bold]")
    return True


def _cmd_delete(spec: CommandSpec, raw_args: str, workdir: str) -> bool:
    from core import session_store
    ref = raw_args.strip()
    if not ref:
        _print("Usage: [bold]/delete #<N>[/bold] or [bold]/delete <session-id>[/bold]")
        return True
    target = _resolve_session(ref)
    if target is None:
        _print("No matching session to delete.")
        return True
    if session_store.delete_session(target.id):
        _print(f"🗑️  Deleted session [cyan]{target.id[:8]}[/cyan] ({target.title or 'untitled'}).")
        if target.id == session_store.current_session_id():
            session_store.new_session(_cwd())
            _print("Started a fresh session.")
    return True


def _cmd_help(spec: CommandSpec, raw_args: str, workdir: str) -> bool:
    commands = list_commands(workdir)
    _print("\n[bold]Slash commands[/bold]  (type [bold]/<name>[/bold] or press [bold]ctrl+p[/bold] for the palette)\n")
    for name, cmd in commands.items():
        base = f"  [bold]/{name}[/bold]"
        desc = cmd.description or "(no description)"
        if cmd.hints:
            base += f"  [dim]→ {', '.join(cmd.hints)}[/dim]"
        _print(base)
        _print(f"     [dim]{desc}[/dim]")
    if not any(c.source == "command" for c in commands.values()):
        _print("\n[dim]Tip: add custom `/` commands under [bold]\"commands\"[/bold] in "
               "settings.json — e.g. /component with a template using $ARGUMENTS.[/dim]")
    _print("")
    return True


def _cmd_agents(spec: CommandSpec, raw_args: str, workdir: str) -> bool:
    from core import agents
    settings = agents._load_settings()
    _print(agents.describe())
    default = settings.get("default_agent") or "build"
    state = "on" if settings.get("enabled", True) else "off"
    _print(f"[dim]automatic routing: {state} · default agent: [bold]{default}[/bold][/dim]")
    _print("[dim]Force an agent with [bold]--agent <name>[/bold] on 'do' tasks, "
           "or start a task with 'review:' / 'explore:'.[/dim]")
    _print("")
    return True


def _cmd_compact(spec: CommandSpec, raw_args: str, workdir: str) -> bool:
    """Manually compact the current session's history into a summary turn."""
    from core import agents, session_store
    sid = session_store.current_session_id()
    if not sid:
        _print("No active session to compact.")
        return True
    messages = session_store.get_messages(sid)
    if not messages:
        _print("Nothing to compact — the session has no messages yet.")
        return True
    total = sum(len(m.content) for m in messages)
    _print(f"[dim]🗜️  Compacting {len(messages)} messages ({total:,} chars)…[/dim]")
    summary = agents.compact_context(agents._msgs_text(messages))
    if not summary:
        _print("Compaction produced no summary (empty conversation?).")
        return True
    stored = session_store.append_message(sid, "assistant", summary, agent="compaction")
    if stored is None:
        _print("⚠️  Could not write the compacted summary.")
        return True
    _print(f"[green]✔ Compacted history → stored summary "
           f"({len(summary)} chars, message #{stored.id}).[/green]")
    _print("[dim](/resume <id> to see it, or just keep working — the next "
           "automatic run will pick it up as prior context.)[/dim]")
    return True


# ── Provider / model switching (opencode-style /provider and /model) ─────────

def _print_provider_table() -> None:
    """Table of every provider with its configured default model."""
    from core import providers as _providers
    from core.ai import _load_config
    cfg = _load_config()
    active = _providers.active_provider(cfg)
    _print("\n📦 [bold]AI providers:[/bold]\n")
    for pid in _providers.PROVIDER_ORDER:
        base = _providers.PROVIDERS.get(pid) or {}
        prof = _providers.provider_profile(cfg, pid)
        label = base.get("label") or pid
        kind = prof.get("kind") or base.get("kind") or ""
        default = prof.get("default_model") or base.get("default_model", "")
        marker = " ✅  [dim]← active[/dim]" if pid == active else ""
        _print(f"  [bold]{pid:<10}[/bold] {label}{marker}")
        _print(f"           [dim]{kind} · default: {default or '(none)'}[/dim]")
    _print(f"\n💡 Active provider: [cyan]{active}[/cyan]  →  [bold]/provider <id>[/bold]  "
           f"([bold]/provider list[/bold])\n")


def _cmd_provider(spec: CommandSpec, raw_args: str, workdir: str) -> bool:
    """Switch AI providers dynamically: /provider, /provider list, /provider <id>."""
    from core import providers as _providers
    from core.ai import _load_config, get_current_model, known_provider_ids, switch_provider
    from core.providers import ProviderError

    args = parse_arguments(raw_args)
    if not args:
        _print(f"🤖 Active provider: [cyan]{_providers.active_provider(_load_config())}[/cyan]  "
               f"→ model [green]{get_current_model()}[/green]")
        return _pick_provider()

    sub = args[0].lower()
    if sub == "list":
        _print_provider_table()
        return True

    pid = args[0].lower()
    if pid not in _providers.PROVIDERS:
        _print(f"⚠️  Unknown provider [bold]{args[0]}[/bold]. "
               f"Known: {', '.join(known_provider_ids())}")
        _print("   [bold]/provider list[/bold] shows all providers.")
        return True
    try:
        switch_provider(pid)
    except ProviderError as exc:
        _print(f"⚠️  {exc}")
        return True
    label = (_providers.PROVIDERS.get(pid) or {}).get("label", pid)
    _print(f"✅ Provider → [green]{label}[/green]  (model: {get_current_model()})")
    return True


def _pick_provider() -> bool:
    """Interactive numbered provider picker."""
    from core import providers as _providers
    from core.ai import get_current_model, known_provider_ids, switch_provider
    from core.providers import ProviderError
    order = known_provider_ids()
    _print("[bold]Switch provider[/bold]  ([dim]Enter to cancel[/dim])\n")
    for i, pid in enumerate(order, 1):
        label = (_providers.PROVIDERS.get(pid) or {}).get("label", pid)
        _print(f"  [bold]{i:>2}[/bold]  {pid:<10} {label}")
    try:
        choice = input("   pick a provider (number): ").strip()
    except (KeyboardInterrupt, EOFError):
        _print("\nCancelled.")
        return True
    if not choice:
        _print("Cancelled.")
        return True
    try:
        idx = int(choice)
    except ValueError:
        _print(f"⚠️  Not a number: {choice!r}")
        return True
    if not (1 <= idx <= len(order)):
        _print(f"⚠️  No provider #{idx}.")
        return True
    pid = order[idx - 1]
    try:
        switch_provider(pid)
    except ProviderError as exc:
        _print(f"⚠️  {exc}")
        return True
    _print(f"✅ Provider → [green]{pid}[/green]  (model: {get_current_model()})")
    return True


def _print_models_for(pid: str) -> None:
    """Numbered list of a provider's models (live list, catalog fallback)."""
    from core import providers as _providers
    from core.ai import _load_config, resolve_provider_live_models
    models = resolve_provider_live_models(pid)
    cfg = _load_config()
    default = _providers.provider_profile(cfg, pid).get("default_model") or ""
    label = (_providers.PROVIDERS.get(pid) or {}).get("label", pid)
    _print(f"\n📚 [bold]{label}[/bold] [dim]({pid})[/dim] — {len(models)} models:\n")
    for i, m in enumerate(models, 1):
        marker = "  ✅ ← active" if m == default else ""
        _print(f"  {i:>2}.  {m}{marker}")
    _print("\n💡 Set: [bold]/model set <name>[/bold]   (or directly: [bold]/model <name>[/bold])\n")


def _cmd_model(spec: CommandSpec, raw_args: str, workdir: str) -> bool:
    """Show / switch the active model: /model, /model list, /model set <name>."""
    from core import providers as _providers
    from core.ai import _load_config, get_current_model, set_provider_model
    from core.providers import ProviderError

    args = parse_arguments(raw_args)
    cfg = _load_config()
    active = _providers.active_provider(cfg)

    if not args:
        _print(f"🤖 Active model : [green]{get_current_model()}[/green]")
        _print(f"   Provider     : [cyan]{active}[/cyan]  "
               f"([bold]/provider <id>[/bold] to switch)\n")
        return _pick_model()

    sub = args[0].lower()
    if sub == "list":
        pid = args[1].lower() if len(args) >= 2 else active
        if pid not in _providers.PROVIDERS:
            _print(f"⚠️  Unknown provider [bold]{pid}[/bold]. "
                   f"Known: {', '.join(_providers.PROVIDER_ORDER)}")
            return True
        _print_models_for(pid)
        return True

    if sub == "set" and len(args) >= 2:
        model_name = " ".join(args[1:])
    elif sub == "set":
        _print("Usage: [bold]/model set <name>[/bold]")
        return True
    else:
        model_name = " ".join(args)
    if not model_name:
        _print("Usage: [bold]/model set <name>[/bold]")
        return True
    try:
        pair = set_provider_model(model_name, active)
    except ProviderError as exc:
        _print(f"⚠️  {exc}")
        return True
    _print(f"✅ Model → [green]{pair}[/green]")
    return True


def _pick_model() -> bool:
    """Interactive numbered model picker for the active provider."""
    from core import providers as _providers
    from core.ai import (
        _load_config,
        get_current_model,
        resolve_provider_live_models,
        set_provider_model,
    )
    from core.providers import ProviderError
    cfg = _load_config()
    active = _providers.active_provider(cfg)
    models = resolve_provider_live_models(active)
    _print(f"[bold]Switch model[/bold] ([cyan]{active}[/cyan])  ([dim]Enter to cancel[/dim])\n")
    for i, m in enumerate(models, 1):
        marker = ""
        if m == _providers.provider_profile(cfg, active).get("default_model"):
            marker = "  ✅ active"
        _print(f"  [bold]{i:>2}[/bold]  {m}{marker}")
    if not models:
        try:
            name = input("   no models listed — type a model name (Enter to cancel): ").strip()
        except (KeyboardInterrupt, EOFError):
            _print("\nCancelled.")
            return True
        if not name:
            _print("Cancelled.")
            return True
    else:
        try:
            choice = input("   pick a model (number or exact name): ").strip()
        except (KeyboardInterrupt, EOFError):
            _print("\nCancelled.")
            return True
        if not choice:
            _print("Cancelled.")
            return True
        try:
            idx = int(choice)
        except ValueError:
            name = choice
        else:
            if 1 <= idx <= len(models):
                name = models[idx - 1]
            else:
                _print(f"⚠️  No model #{idx}.")
                return True
    try:
        pair = set_provider_model(name, active)
    except ProviderError as exc:
        _print(f"⚠️  {exc}")
        return True
    _print(f"✅ Model → [green]{pair}[/green]  (from {get_current_model()})")
    return True


def _builtin_specs() -> dict[str, CommandSpec]:
    return {
        "init": CommandSpec(
            name="init",
            description="guided AGENTS.md setup",
            source="builtin",
            template=PROMPT_INITIALIZE,
            hints=tuple(hints(PROMPT_INITIALIZE)),
            handler=_run_template,
        ),
        "review": CommandSpec(
            name="review",
            description="review changes [commit|branch|pr], defaults to uncommitted",
            source="builtin",
            template=PROMPT_REVIEW,
            subtask=True,
            hints=tuple(hints(PROMPT_REVIEW)),
            handler=_run_template,
        ),
        "agents": CommandSpec(
            name="agents",
            description="list specialised agents and automatic routing state",
            source="builtin",
            handler=_cmd_agents,
        ),
        "compact": CommandSpec(
            name="compact",
            description="compact the current session's history into a summary",
            source="builtin",
            handler=_cmd_compact,
        ),
        "provider": CommandSpec(
            name="provider",
            description="show / switch the AI provider (interactive picker with no args)",
            source="builtin",
            hints=("list", "<id>"),
            handler=_cmd_provider,
        ),
        "model": CommandSpec(
            name="model",
            description="show / switch the active model (interactive picker with no args)",
            source="builtin",
            hints=("list", "set <name>"),
            handler=_cmd_model,
        ),
        "new": CommandSpec(name="new", description="start a fresh session", source="builtin", handler=_cmd_new),
        "sessions": CommandSpec(name="sessions", description="list saved sessions", source="builtin", handler=_cmd_sessions),
        "resume": CommandSpec(name="resume", description="resume a saved session (#N or <id>)", source="builtin", handler=_cmd_resume),
        "rename": CommandSpec(name="rename", description="rename the current session", source="builtin", handler=_cmd_rename),
        "delete": CommandSpec(name="delete", description="delete a saved session (#N or <id>)", source="builtin", handler=_cmd_delete),
        "help": CommandSpec(name="help", description="list all slash commands", source="builtin", handler=_cmd_help),
    }


def list_commands(workdir: str | None = None) -> dict[str, CommandSpec]:
    """
    Full command registry: built-ins first, then config commands (may override
    built-ins like opencode allows), then skills (never override existing).
    """
    workdir = workdir or _cwd()
    commands: dict[str, CommandSpec] = {}

    for name, spec in _builtin_specs().items():
        commands[name] = spec

    for name, command in _config_commands().items():
        template = command.get("template") or ""
        commands[name] = CommandSpec(
            name=name,
            description=str(command.get("description") or ""),
            source="command",
            template=str(template),
            subtask=bool(command.get("subtask")),
            hints=tuple(hints(str(template))),
            handler=_run_template,
        )

    for name, skill in _skill_commands(workdir).items():
        if name in commands:
            continue
        template = _skill_template(skill, workdir)
        commands[name] = CommandSpec(
            name=name,
            description=str(skill.get("description") or ""),
            source="skill",
            template=template,
            hints=tuple(hints(template)),
            handler=_run_template,
        )

    return commands


def _skill_template(skill: dict, workdir: str) -> str:
    """A skill command's prompt = skill instructions + its base directory note."""
    content = str(skill.get("content") or "")
    base_dir = str(skill.get("base_dir") or "")
    if not base_dir:
        return content
    return "\n".join([
        content,
        "",
        f"Base directory for this skill: {base_dir}",
        "Relative paths in this skill (e.g., scripts/, references/) are relative to this base directory.",
    ])


def command_names(workdir: str | None = None) -> list[str]:
    """Command names for prompt completion (sorted, no leading slash)."""
    return sorted(list_commands(workdir).keys())


# ── Public entry points ──────────────────────────────────────────────────────

def run(raw: str) -> None:
    """Execute a slash line like `/review HEAD~1`."""
    text = (raw or "").strip()
    if not text.startswith("/"):
        return
    rest = text[1:]
    name, _, raw_args = rest.partition(" ")
    execute(name.strip().lower(), raw_args.strip(), _cwd())


def execute(name: str, raw_args: str = "", workdir: str | None = None) -> bool:
    """Dispatch one command by name. Returns True if something ran."""
    workdir = workdir or _cwd()
    spec = list_commands(workdir).get(name)
    if spec is None:
        available = ", ".join("/" + n for n in command_names(workdir))
        _print(f"⚠️  Unknown command [bold]/{name}[/bold]. Available: {available}")
        return False
    if spec.handler is not None:
        return bool(spec.handler(spec, raw_args, workdir))
    return _run_template(spec, raw_args, workdir)


def palette(prefill: str = "") -> bool:
    """
    Interactive command palette (ctrl+p). Prints every `/` command, lets the
    user pick one by number (or type a `/name`). Returns True when a command
    ran so the caller can continue; False when cancelled.
    """
    workdir = _cwd()
    commands = list_commands(workdir)
    _print("\n[bold]Command palette[/bold]  ([dim]Enter to cancel[/dim])\n")
    for i, (name, cmd) in enumerate(commands.items(), 1):
        base = f"  [bold]{i:>2}[/bold]  [bold]/{name}[/bold]"
        if cmd.hints:
            base += f"  [dim]→ {', '.join(cmd.hints)}[/dim]"
        _print(base)
        if cmd.description:
            _print(f"       [dim]{cmd.description}[/dim]")
    _print("")
    try:
        choice = input(f"   pick a command (e.g. 1, or '/name'){(' — your input kept: ' + prefill) if prefill else ''}: ").strip()
    except (KeyboardInterrupt, EOFError):
        _print("\nCancelled.")
        return False

    if not choice:
        _print("Cancelled.")
        return False
    if choice.startswith("/"):
        _print(f"▶️  Running [bold]{choice}[/bold]")
        run(choice)
        return True
    try:
        idx = int(choice)
    except ValueError:
        _print(f"⚠️  Not a number: {choice!r}")
        return False
    names = list(commands.keys())
    if 1 <= idx <= len(names):
        name = names[idx - 1]
        _print(f"▶️  Running [bold]/{name}[/bold]")
        return execute(name, prefill, workdir)
    _print(f"⚠️  No command #{idx}.")
    return False
