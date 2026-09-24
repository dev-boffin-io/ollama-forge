"""
The `task` tool — foreground sub-agents (a Python port of opencode's TaskTool).

opencode's version runs fully nested sessions with background jobs, abort
plumbing and per-agent permissions; dev-assist's model is a single agent, so
this port keeps the ergonomic surface (subagent_type, description, prompt,
task_id resume) but runs everything in the foreground with the same provider
and tool registry as the parent loop, a step budget, and a depth guard.

Depth is tracked through `store.ACTIVE_SUBAGENT_DEPTH` so a `task` invocation
inside a `task` invocation is refused beyond MAX_SUBAGENT_DEPTH.
"""

from __future__ import annotations

import json

_MAX_STEPS = 24

_SUBAGENT_SYSTEM = (
    "You are a sub-agent of dev-assist, working on the single task given to you "
    "by the parent agent, in the user's project.\n"
    "You have the same tools as the parent agent. Use them instead of guessing. "
    "Work in small, verifiable steps. When the task is done, stop calling tools "
    "and reply with a concise summary of what you did and any files you changed.\n"
    "Your output will not be shown directly to the user — summarize clearly so "
    "the parent agent can act on it.\n"
    "Working directory: {workdir}"
)


def _normalise_tool_calls(message) -> list[tuple[str, str, dict]]:
    if isinstance(message, dict):
        calls = message.get("tool_calls") or []
    else:
        calls = getattr(message, "tool_calls", None) or []
    out = []
    for i, call in enumerate(calls):
        if isinstance(call, dict):
            fn = call.get("function", {}) or {}
            call_id = call.get("id") or f"call_{i}"
            name = fn.get("name", "")
            raw = fn.get("arguments")
        else:
            fn = getattr(call, "function", None)
            call_id = getattr(call, "id", None) or f"call_{i}"
            name = getattr(fn, "name", "") if fn else ""
            raw = getattr(fn, "arguments", None) if fn else None
        if isinstance(raw, dict):
            args = raw
        else:
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else {}
                args = parsed if isinstance(parsed, dict) else {}
            except json.JSONDecodeError:
                args = {}
        if name:
            out.append((call_id, name, args))
    return out


def _message_text(message) -> str:
    if isinstance(message, dict):
        return message.get("content") or ""
    return getattr(message, "content", "") or ""


def _assistant_turn(message, calls: list[tuple[str, str, dict]]) -> dict:
    turn: dict = {"role": "assistant", "content": _message_text(message)}
    if calls:
        turn["tool_calls"] = [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)},
            }
            for call_id, name, args in calls
        ]
    return turn


def _render_output(session_id: str, state: str, text: str, summary: str = "") -> str:
    tag = "task_error" if state == "error" else "task_result"
    lines = [f'<task id="{session_id}" state="{state}">']
    if summary:
        lines.append(f"<summary>{summary}</summary>")
    lines += [f"<{tag}>", text, f"</{tag}>", "</task>"]
    return "\n".join(lines)


def run_task(
    prompt: str,
    description: str,
    workdir: str,
    *,
    messages: list[dict] | None = None,
    for_id: str,
    max_steps: int = _MAX_STEPS,
) -> dict:
    """Run a foreground sub-agent to completion. Returns a renderable result dict.

    Raises RuntimeError for pre-flight problems (depth, provider) so the caller
    can turn them into a faithful <task_error> without swallowing detail.
    """
    from core import ai as _ai

    cfg = _ai._load_config()
    provider = _ai.get_provider(cfg)
    provider.resolve_model()

    from core.tools import all_tool_schemas, execute_tool

    if messages is None:
        messages = [
            {"role": "system", "content": _SUBAGENT_SYSTEM.format(workdir=workdir)},
            {"role": "user", "content": prompt},
        ]

    final_text = ""
    steps = 0
    while steps < max_steps:
        steps += 1
        try:
            message = provider.chat(messages, tools=all_tool_schemas())
        except Exception as exc:
            return {
                "state": "error",
                "text": f"Subagent model call failed: {exc}",
                "steps": steps,
                "messages": messages,
            }

        calls = _normalise_tool_calls(message)
        text = _message_text(message)

        if not calls:
            final_text = text.strip()
            break

        messages.append(_assistant_turn(message, calls))

        for call_id, name, args in calls:
            if name == "task":
                return {
                    "state": "error",
                    "text": "Sub-agent attempted to launch a nested sub-agent, which is not allowed.",
                    "steps": steps,
                    "messages": messages,
                }
            try:
                result = execute_tool(name, args, workdir)
            except Exception as exc:
                result = f"Error in {name}: {exc}"
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": result,
            })
    else:
        final_text = f"Sub-agent stopped after {max_steps}-step budget without a final answer."

    return {
        "state": "completed" if final_text else "error",
        "text": final_text or "(no response)",
        "steps": steps,
        "messages": messages,
    }


def subagent_depth() -> int:
    """Current nesting depth of `task` invocations (module-level read)."""
    from core.toolimpl.store import ACTIVE_SUBAGENT_DEPTH

    return ACTIVE_SUBAGENT_DEPTH
