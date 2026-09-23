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

Works against every provider in core.providers with the same code path:
  - Ollama's native tool calling (`ollama.chat(tools=...)`)
  - OpenAI-compatible chat completions (OpenAI, Groq, OpenRouter,
    Mistral, Azure, custom)
  - Anthropic's Messages API (tool_use/tool_result blocks converted
    to the same normalized message shape behind the adapter)

All providers accept the same JSON-Schema tool declarations; the
adapters normalise tool calls into a single (id, name, args) shape.

Destructive tools (write/edit/bash) are gated behind an approval
callback. The default callback auto-approves read-only tools and refuses
destructive ones unless an approver is supplied — so nothing can silently
modify a repo just because the loop was invoked programmatically.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from typing import Any

from core import agents as agent_registry
from core import change_tracker
from core.agents import AgentSpec
from core.repo_map import build_repo_map
from core.tools import (
    DESTRUCTIVE_TOOLS,
    TOOL_SCHEMAS,
    describe_call,
    execute_tool,
)

MAX_STEPS = 24             # per sub-task step budget
MAX_SUBTASKS = 5           # how many sub-tasks a plan may contain
TOTAL_STEPS_CAP = 120      # hard safety cap across all sub-tasks

# The default executor's prompt lives with the agent registry so routing and
# the loop stay in sync; kept as a module-level name for callers/tests.
SYSTEM_PROMPT = agent_registry.resolve("build").system_prompt

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


def _plan_prompt_task(task: str, repo_map: str = "") -> str:
    prompt = f"Overall task:\n{task}"
    if repo_map:
        prompt += f"\n\nProject layout:\n{repo_map}"
    return prompt


def _map_block(text: str) -> str:
    """Wrap a repo map for injection into a prompt, if there is one."""
    if not text:
        return ""
    return f"## Project layout\n{text}"


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


def _chat(provider, messages: list[dict], *, tools: bool = True) -> dict:
    """One non-streaming provider call in the agent loop."""
    return provider.chat(messages, tools=TOOL_SCHEMAS if tools else None)


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
    provider,
    repo_map: str = "",
) -> list[dict]:
    """Ask the model to break the task into sub-tasks.

    Returns a list of {"title", "goal"} dicts, or a single-element plan
    holding the whole task if the model can't/won't produce a usable plan.
    """
    messages = [
        {"role": "system", "content": PLANNING_PROMPT},
        {"role": "user", "content": _plan_prompt_task(task, repo_map)},
    ]
    try:
        message = _chat(provider, messages, tools=False)
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
    repo_map: str = "",
) -> str:
    """Context block for one sub-task, carrying prior sub-task results forward."""
    parts = [f"## Overall task\n{task}"]

    if repo_map:
        parts.append(f"\n{repo_map}")

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
    provider,
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
            message = _chat(provider, subtask_messages)
        except Exception as exc:
            hint = ""
            if provider.kind == "ollama":
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
                        with open(file_path, encoding="utf-8", errors="replace") as f:
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
    provider,
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
        message = _chat(provider, messages, tools=False)
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
    agent: str | AgentSpec = "build",
    extra_context: str = "",
) -> str:
    """
    Run the agent, planning the task into sub-tasks and executing each
    with its own step budget, carrying prior results forward.

    task      — what the user asked for.
    workdir   — project root; defaults to the current directory.
    approver  — called as approver(tool_name, args) -> bool before any
                destructive tool runs. Defaults to refusing them (which is
                also the behaviour read-only agents want).
    on_event  — optional progress hook, called as on_event(kind, text)
                with kind in {"tool", "result", "text", "warn", "plan",
                "route"}.
    max_steps — per sub-task step budget (default 24).
    agent     — which registered agent to run (core.agents.ALL_AGENTS):
                "build" (default), "coder", "reviewer", "explore", or a
                user-defined agent. Review/explore/persona agents skip
                multi-step planning and are read-only by prompt + policy.
    extra_context — extra carry-forward context (e.g. a compacted summary of
                an earlier conversation) appended to the system prompt.

    Before planning, the project's layout (file tree + top-level
    signatures) is injected as a repo map. For large projects the map is
    narrowed by similarity search to files relevant to the task — and, for
    multi-sub-task runs, re-narrowed per sub-task goal.

    Returns the agent's final combined answer.
    """
    workdir = os.path.abspath(workdir or os.getcwd())
    approver = approver or auto_approve_readonly
    change_tracker.new_run()

    def emit(kind: str, text: str) -> None:
        if on_event:
            on_event(kind, text)

    spec = agent_registry.resolve(agent)
    emit("route", f"{spec.id} · {spec.description}")

    from core.ai import _load_config
    from core.ai import get_provider as _get_provider
    cfg = _load_config()
    provider = _get_provider(cfg)
    provider.resolve_model()

    # ── Project layout ──
    # A compact repo map (file tree + top-level signatures) goes into the
    # system prompt so the agent knows the layout before it starts. Large
    # projects are narrowed by the indexer's similarity search instead.
    map_info = build_repo_map(task, workdir)
    map_block = _map_block(map_info["text"])

    # ── Thinking phase ──
    # Default agents plan the task into sub-tasks; point-agents (review,
    # explore) execute the request directly in a single step.
    if spec.uses_planning:
        plan = _plan_subtasks(task, provider, repo_map=map_block)
        emit("plan", _format_plan(plan))
    else:
        plan = [{"title": spec.name.capitalize(), "goal": task}]
        emit("plan", f"📋 {spec.name}: running as a single direct step.")

    system_content = spec.system_prompt.format(workdir=workdir)
    if extra_context.strip():
        system_content += "\n\n## Prior context\n" + extra_context.strip()
    if map_block:
        system_content += "\n\n" + map_block

    results: list[dict] = []
    steps_used_total = 0

    for idx, subtask in enumerate(plan, 1):
        if steps_used_total >= TOTAL_STEPS_CAP:
            emit("warn", f"Reached the overall {TOTAL_STEPS_CAP}-step safety cap; "
                         f"stopping before sub-task {idx}/{len(plan)}.")
            break

        context = _build_subtask_context(task, plan, idx, results)
        if map_info["focused"]:
            # Big project: re-narrow the map to what matters for THIS sub-task.
            sub = build_repo_map(subtask["goal"], workdir)
            sub_block = _map_block(sub["text"])
            if sub_block:
                context += "\n\n" + sub_block
        messages: list[dict] = [
            {"role": "system", "content": system_content},
            {"role": "user", "content": context},
        ]

        remaining_budget = TOTAL_STEPS_CAP - steps_used_total
        budget = max(1, min(max_steps, remaining_budget))

        emit("text", f"▶ Sub-task {idx}/{len(plan)}: {subtask['title']}")

        summary, steps_used = _run_subtask(
            idx, len(plan), messages,
            provider=provider,
            workdir=workdir, approver=approver, emit=emit, budget=budget,
        )
        steps_used_total += steps_used

        results.append({"title": subtask["title"], "summary": summary})

    emit("text", "✓ All sub-tasks complete — writing final answer.")

    return _synthesize_final_answer(task, results, provider)
