"""
Agent — the tool-calling loop.

This is what makes `da` act rather than just answer: the model is given
the tool registry from core.tools, and on each turn it either calls
tools (which we execute and feed back) or produces a final answer. The
loop runs until the model stops requesting tools, or MAX_STEPS is hit.

Works against two backends with the same code path:
  - Ollama's native tool calling (`ollama.chat(tools=...)`)
  - OpenAI-compatible chat completions (Groq, etc.)

Both accept the same JSON-Schema tool declarations and return tool calls
in near-identical shapes, so the only real difference is transport.

Destructive tools (write/edit/bash) are gated behind an approval
callback. The default callback auto-approves read-only tools and refuses
destructive ones unless an approver is supplied — so nothing can silently
modify a repo just because the loop was invoked programmatically.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable

from core.tools import (
    DESTRUCTIVE_TOOLS,
    TOOL_SCHEMAS,
    describe_call,
    execute_tool,
)
from core import change_tracker

MAX_STEPS = 24

SYSTEM_PROMPT = """You are dev-assist, a coding agent that works directly in the user's project.

You have tools. Use them instead of guessing:
- Explore with list_dir, glob, and grep before assuming where code lives.
- Always read_file before you edit_file, so you can quote the original text exactly.
- edit_file needs old_string to match the raw file exactly and appear exactly once. Never include the line-number prefixes that read_file adds.
- Use bash for builds, tests, and git — not for reading or editing files.

Work in small, verifiable steps. After changing code, check your work (run the test, re-read the file) rather than assuming it worked.

When the task is done, stop calling tools and reply with a short summary of what you changed. Be concise. Do not pad the answer with restatements of the question.

Working directory: {workdir}"""


# ─────────────────────────────────────────────────────────────────────
# Approval
# ─────────────────────────────────────────────────────────────────────
def auto_approve_readonly(name: str, args: dict) -> bool:
    """Default policy: allow read-only tools, refuse destructive ones."""
    return name not in DESTRUCTIVE_TOOLS


def approve_everything(name: str, args: dict) -> bool:
    """Opt-in policy for non-interactive runs. Use with care."""
    return True


# ─────────────────────────────────────────────────────────────────────
# Backend adapters — normalise tool calls into (id, name, args)
# ─────────────────────────────────────────────────────────────────────
def _parse_args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _normalise_tool_calls(message: Any) -> list[tuple[str, str, dict]]:
    """Pull tool calls out of either backend's message shape."""
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
            args = _parse_args(fn.get("arguments"))
        else:
            fn = getattr(call, "function", None)
            call_id = getattr(call, "id", None) or f"call_{i}"
            name = getattr(fn, "name", "") if fn else ""
            args = _parse_args(getattr(fn, "arguments", None) if fn else None)
        if name:
            out.append((call_id, name, args))
    return out


def _message_text(message: Any) -> str:
    if isinstance(message, dict):
        return message.get("content") or ""
    return getattr(message, "content", "") or ""


def _call_ollama(messages: list[dict], model: str) -> Any:
    import ollama
    response = ollama.chat(model=model, messages=messages, tools=TOOL_SCHEMAS)
    return response["message"] if isinstance(response, dict) else response.message


def _call_api(messages: list[dict], cfg) -> Any:
    import urllib.request
    from core.ai import _get_api_key, _get_api_model, _get_api_url

    payload = json.dumps({
        "model": _get_api_model(cfg),
        "messages": messages,
        "tools": TOOL_SCHEMAS,
        "stream": False,
    }).encode()

    req = urllib.request.Request(
        _get_api_url(cfg),
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_get_api_key(cfg)}",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]


def _assistant_turn(message: Any, calls: list[tuple[str, str, dict]]) -> dict:
    """Rebuild the assistant message for the next request's history."""
    turn: dict[str, Any] = {"role": "assistant", "content": _message_text(message)}
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


# ─────────────────────────────────────────────────────────────────────
# The loop
# ─────────────────────────────────────────────────────────────────────
def run_agent(
    task: str,
    *,
    workdir: str | None = None,
    approver: Callable[[str, dict], bool] | None = None,
    on_event: Callable[[str, str], None] | None = None,
    max_steps: int = MAX_STEPS,
) -> str:
    """
    Run the agent until it stops calling tools.

    task      — what the user asked for.
    workdir   — project root; defaults to the current directory.
    approver  — called as approver(tool_name, args) -> bool before any
                destructive tool runs. Defaults to refusing them.
    on_event  — optional progress hook, called as on_event(kind, text)
                with kind in {"tool", "result", "text", "warn"}.

    Returns the agent's final text answer.
    """
    workdir = os.path.abspath(workdir or os.getcwd())
    approver = approver or auto_approve_readonly
    tracker = change_tracker.new_run()

    def emit(kind: str, text: str) -> None:
        if on_event:
            on_event(kind, text)

    from core.ai import _get_engine, _get_ollama_model, _load_config
    cfg = _load_config()
    engine = _get_engine(cfg)
    model = _get_ollama_model(cfg) if engine == "ollama" else None

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT.format(workdir=workdir)},
        {"role": "user", "content": task},
    ]

    final_text = ""

    for step in range(max_steps):
        try:
            message = (
                _call_ollama(messages, model) if engine == "ollama"
                else _call_api(messages, cfg)
            )
        except Exception as exc:
            hint = ""
            if engine == "ollama":
                hint = (
                    "\nIf this says the model does not support tools, switch to a "
                    "tool-capable model (e.g. qwen2.5-coder:7b or llama3.1:8b)."
                )
            return f"⚠️  Model call failed: {exc}{hint}"

        calls = _normalise_tool_calls(message)
        text = _message_text(message)

        # No tool calls → the model is done talking.
        if not calls:
            final_text = text.strip()
            if final_text:
                emit("text", final_text)
            break

        if text.strip():
            emit("text", text.strip())

        messages.append(_assistant_turn(message, calls))

        for call_id, name, args in calls:
            emit("tool", describe_call(name, args))

            if name in DESTRUCTIVE_TOOLS and not approver(name, args):
                reason = getattr(approver, "last_reason", None)
                result = (
                    f"Denied by user: the {name} call was not approved"
                    + (f" (reason: {reason})" if reason else "")
                    + ". Do not retry it as-is; ask the user how to proceed, "
                      "or try a different approach if one is clear from the reason given."
                )
                emit("warn", f"denied: {describe_call(name, args)}")
            else:
                if name in ("write_file", "edit_file"):
                    from core.tools import resolve_path
                    file_path = resolve_path(args.get("path"), workdir)
                    tracker.snapshot(file_path)

                result = execute_tool(name, args, workdir)
                emit("result", result)

                if name in ("write_file", "edit_file") and not result.startswith("Error"):
                    try:
                        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                            tracker.record(file_path, f.read())
                    except Exception:
                        pass

            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": result,
            })
    else:
        final_text = (
            f"⚠️  Stopped after {max_steps} steps without finishing. "
            f"The task may be too large — try breaking it into smaller pieces."
        )
        emit("warn", final_text)

    return final_text or "(no response)"
