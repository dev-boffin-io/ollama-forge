"""
Agent — the tool-calling loop with multi-step planning.

This is what makes `da` act rather than just answer: the model breaks the
task into sub-tasks up front (a plan), then executes each one with the
full tool registry from core.tools, carrying the result of each prior
sub-task forward as context for the next. Each sub-task gets its own step
budget instead of one budget for the whole run, so large tasks no longer
hit a single hard limit partway through.

On each turn the model either calls tools (which we execute and feed back)
or produces a final answer for that sub-task. The loop stops when the model
stops requesting tools or the sub-task's budget is hit.

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
import re
from typing import Any, Callable

from core.tools import (
    DESTRUCTIVE_TOOLS,
    TOOL_SCHEMAS,
    describe_call,
    execute_tool,
)
from core import change_tracker

MAX_STEPS = 24             # per sub-task step budget
MAX_SUBTASKS = 5           # how many sub-tasks a plan may contain
TOTAL_STEPS_CAP = 120      # hard safety cap across all sub-tasks

SYSTEM_PROMPT = """You are dev-assist, a coding agent that works directly in the user's project.

You have tools. Use them instead of guessing:
- Explore with list_dir, glob, and grep before assuming where code lives.
- Always read_file before you edit_file, so you can quote the original text exactly.
- edit_file needs old_string to match the raw file exactly and appear exactly once. Never include the line-number prefixes that read_file adds.
- Use bash for builds, tests, and git — not for reading or editing files.

Work in small, verifiable steps. After changing code, check your work (run the test, re-read the file) rather than assuming it worked.

When the task is done, stop calling tools and reply with a short summary of what you changed. Be concise. Do not pad the answer with restatements of the question.

Working directory: {workdir}"""

PLANNING_PROMPT = """You are the task planner for dev-assist, a coding agent.
The user has a single overall request, but a long one that may require many steps.

Break the request into a short sequence of concrete sub-tasks, ordered so the
agent can execute them one after another. Each sub-task must be small enough that
a coding agent with file read/write/edit and bash tools can finish it in a handful
of tool calls. Prefer 2-5 sub-tasks; bundle related work to keep the plan tight.

Return a JSON object — and ONLY a JSON object, no prose, no markdown fences — with
the shape:

{"subtasks": [{"title": "short label", "goal": "what to accomplish in this step"}]}

The "goal" must be self-contained: it will be given to the agent as the only
instructions for that step, along with the results of earlier steps.
"""


def _plan_prompt_task(task: str) -> str:
    return f"Overall task:\n{task}"


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


def _call_ollama(messages: list[dict], model: str, *, tools: bool = True) -> Any:
    import ollama
    kwargs: dict = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = TOOL_SCHEMAS
    response = ollama.chat(**kwargs)
    return response["message"] if isinstance(response, dict) else response.message


def _call_api(messages: list[dict], cfg, *, tools: bool = True) -> Any:
    import urllib.request
    from core.ai import _get_api_key, _get_api_model, _get_api_url

    payload: dict[str, Any] = {
        "model": _get_api_model(cfg),
        "messages": messages,
        "stream": False,
    }
    if tools:
        payload["tools"] = TOOL_SCHEMAS

    req = urllib.request.Request(
        _get_api_url(cfg),
        data=json.dumps(payload).encode(),
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
# Planning
# ─────────────────────────────────────────────────────────────────────
def _plan_subtasks(
    task: str,
    cfg,
    engine: str,
    model: str | None,
) -> list[dict]:
    """Ask the model to break the task into sub-tasks.

    Returns a list of {"title", "goal"} dicts, or a single-element plan
    holding the whole task if the model can't/won't produce a usable plan.
    """
    messages = [
        {"role": "system", "content": PLANNING_PROMPT},
        {"role": "user", "content": _plan_prompt_task(task)},
    ]
    try:
        message = (
            _call_ollama(messages, model, tools=False) if engine == "ollama"
            else _call_api(messages, cfg, tools=False)
        )
        text = _message_text(message)
        return _parse_plan(text)
    except Exception:
        return [{"title": "Complete the task", "goal": task}]


def _parse_plan(text: str) -> list[dict]:
    """Parse the planner's JSON response very tolerantly."""
    if not text:
        return [{"title": "Complete the task", "goal": ""}]
    # Strip markdown fences if the model ignored the "no fences" rule.
    cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", text.strip())
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)

    def _extract(obj: Any) -> list[dict]:
        if isinstance(obj, dict) and isinstance(obj.get("subtasks"), list):
            items = obj["subtasks"]
        elif isinstance(obj, list):
            items = obj
        else:
            return []
        plan = []
        for it in items:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or it.get("name") or "").strip()
            goal = str(it.get("goal") or it.get("description") or "").strip()
            if title or goal:
                plan.append({"title": title or f"Step {len(plan) + 1}",
                             "goal": goal or title or ""})
        return plan

    try:
        plan = _extract(json.loads(cleaned))
        if plan:
            return plan[:MAX_SUBTASKS]
    except json.JSONDecodeError:
        pass

    # Fallback: find the first JSON array-looking span.
    for m in re.finditer(r"\[[^\[]*?\]", cleaned, re.DOTALL):
        try:
            plan = _extract(json.loads(m.group(0)))
            if plan:
                return plan[:MAX_SUBTASKS]
        except (json.JSONDecodeError, ValueError):
            continue

    # Fallback: numbered/bulleted list, one sub-task per line. Only accept
    # lines that actually start with a list marker so prose isn't misread
    # as a single-item plan.
    plan = []
    for ln in cleaned.splitlines():
        s = ln.strip()
        if not re.match(r"^(?:[-•*\d]+[.)]?)\s+\S", s):
            continue
        s = re.sub(r"^\s*(?:[-•*\d]+[.)]?)\s*", "", s)
        s = re.sub(r"^\s*(?:[-•*\d]+[.)]?)\s*", "", s)
        if len(s) >= 3 and not s.lower().startswith(("subtask", "overall task")):
            plan.append({"title": s[:60], "goal": s})
    if plan:
        return plan[:MAX_SUBTASKS]

    return [{"title": "Complete the task", "goal": ""}]


def _format_plan(plan: list[dict]) -> str:
    lines = ["📋 Plan:"]
    for i, st in enumerate(plan, 1):
        lines.append(f"  {i}. {st['title']}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────
# Sub-task execution
# ─────────────────────────────────────────────────────────────────────
def _build_subtask_context(
    task: str,
    plan: list[dict],
    idx: int,
    results: list[dict],
) -> str:
    """Context block for one sub-task, carrying prior sub-task results forward."""
    parts = [f"## Overall task\n{task}"]

    if len(plan) > 1:
        lines = ["\n## Plan"]
        for i, st in enumerate(plan, 1):
            marker = "← current" if i == idx else ("done" if i < idx else "")
            lines.append(f"  {i}. {st['title']} {('[' + marker + ']') if marker else ''}")
        parts.append("\n".join(lines))

    if results:
        lines = ["\n## Results so far"]
        for r in results:
            lines.append(f"- **{r['title']}**: {r['summary'][:500]}")
        parts.append("\n".join(lines))

    current = plan[idx - 1]
    parts.append(f"\n## Current sub-task ({idx} of {len(plan)})\n{current['title']}\n\n{current['goal']}")
    parts.append("\nWork on exactly this sub-task. When it is done, stop calling tools and "
                 "reply with a concise summary of what you did for this sub-task.")
    return "\n".join(parts)


def _run_subtask(
    idx: int,
    total: int,
    subtask_messages: list[dict],
    *,
    engine: str,
    cfg,
    model: str | None,
    workdir: str,
    approver: Callable[[str, dict], bool],
    emit: Callable[[str, str], None],
    budget: int,
) -> tuple[str, int]:
    """Run the tool loop for one sub-task with its own step budget.

    Returns (final_text, steps_used).
    """
    final_text = ""
    steps_used = 0

    while steps_used < budget:
        steps_used += 1
        try:
            message = (
                _call_ollama(subtask_messages, model) if engine == "ollama"
                else _call_api(subtask_messages, cfg)
            )
        except Exception as exc:
            hint = ""
            if engine == "ollama":
                hint = (
                    "\nIf this says the model does not support tools, switch to a "
                    "tool-capable model (e.g. qwen2.5-coder:7b or llama3.1:8b)."
                )
            return f"Model call failed: {exc}{hint}", steps_used

        calls = _normalise_tool_calls(message)
        text = _message_text(message)

        if not calls:
            final_text = text.strip()
            if final_text:
                emit("text", final_text)
            break

        if text.strip():
            emit("text", text.strip())

        subtask_messages.append(_assistant_turn(message, calls))

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
                    tracker = change_tracker.get_tracker()
                    tracker.snapshot(file_path)

                result = execute_tool(name, args, workdir)
                emit("result", result)

                if name in ("write_file", "edit_file") and not result.startswith("Error"):
                    try:
                        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                            tracker.record(file_path, f.read())
                    except Exception:
                        pass

            subtask_messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": result,
            })
    else:
        final_text = (
            f"Sub-task {idx}/{total} stopped after reaching its {budget}-step budget."
        )
        emit("warn", final_text)

    return final_text or "(no response)", steps_used


def _synthesize_final_answer(
    task: str,
    results: list[dict],
    cfg,
    engine: str,
    model: str | None,
) -> str:
    """If the task was split, produce one combined final answer."""
    if len(results) <= 1:
        return results[0]["summary"] if results else "(no response)"

    parts = [f"Overall task: {task}", "\nSub-task outcomes:"]
    for r in results:
        parts.append(f"\n### {r['title']}\n{r['summary']}")
    user_msg = (
        "\n".join(parts)
        + "\n\nWrite a single concise final answer for the user that summarizes "
          "everything that was done, the key files changed, and any follow-up "
          "the user should be aware of."
    )
    messages = [
        {"role": "system", "content": (
            "You are dev-assist. Produce a short, well-structured final answer "
            "for the user based on the completed sub-task results below."
        )},
        {"role": "user", "content": user_msg},
    ]
    try:
        message = (
            _call_ollama(messages, model, tools=False) if engine == "ollama"
            else _call_api(messages, cfg, tools=False)
        )
        text = _message_text(message).strip()
        return text or "(no response)"
    except Exception:
        # Fall back to concatenating the last sub-task summary.
        return results[-1]["summary"] or "(no response)"


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
    Run the agent, planning the task into sub-tasks and executing each
    with its own step budget, carrying prior results forward.

    task      — what the user asked for.
    workdir   — project root; defaults to the current directory.
    approver  — called as approver(tool_name, args) -> bool before any
                destructive tool runs. Defaults to refusing them.
    on_event  — optional progress hook, called as on_event(kind, text)
                with kind in {"tool", "result", "text", "warn", "plan"}.
    max_steps — per sub-task step budget (default 24).

    Returns the agent's final combined answer.
    """
    workdir = os.path.abspath(workdir or os.getcwd())
    approver = approver or auto_approve_readonly
    change_tracker.new_run()

    def emit(kind: str, text: str) -> None:
        if on_event:
            on_event(kind, text)

    from core.ai import _get_engine, _get_ollama_model, _load_config
    cfg = _load_config()
    engine = _get_engine(cfg)
    model = _get_ollama_model(cfg) if engine == "ollama" else None

    # ── Planning phase ──
    plan = _plan_subtasks(task, cfg, engine, model)
    emit("plan", _format_plan(plan))

    results: list[dict] = []
    steps_used_total = 0

    for idx, subtask in enumerate(plan, 1):
        if steps_used_total >= TOTAL_STEPS_CAP:
            emit("warn", f"Reached the overall {TOTAL_STEPS_CAP}-step safety cap; "
                         f"stopping before sub-task {idx}/{len(plan)}.")
            break

        context = _build_subtask_context(task, plan, idx, results)
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT.format(workdir=workdir)},
            {"role": "user", "content": context},
        ]

        remaining_budget = TOTAL_STEPS_CAP - steps_used_total
        budget = max(1, min(max_steps, remaining_budget))

        emit("text", f"▶ Sub-task {idx}/{len(plan)}: {subtask['title']}")

        summary, steps_used = _run_subtask(
            idx, len(plan), messages,
            engine=engine, cfg=cfg, model=model,
            workdir=workdir, approver=approver, emit=emit, budget=budget,
        )
        steps_used_total += steps_used

        results.append({"title": subtask["title"], "summary": summary})

    emit("text", "✓ All sub-tasks complete — writing final answer.")

    return _synthesize_final_answer(task, results, cfg, engine, model)