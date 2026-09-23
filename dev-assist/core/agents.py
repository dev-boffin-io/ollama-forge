"""
Automatic agent routing — dev-assist's specialised agents.

Port of opencode's agent system: instead of one hardcoded persona, the tool
keeps a registry of agents (build, coder, reviewer, explore, general,
compaction) and *routes* each task to the right one based on what the user
asked for. The build agent is the default executor; specialised agents swap
the system prompt (and, for reviewer/explore, disable editing) so the same
tool loop is pointed at the correct goal.

Routing is deterministic and never calls the model: keyword/score-based
intent detection over the task text (plus a short slice of recent session
context), with an explicit ``--agent <name>`` / ``/agent <name>`` override
that always wins. This keeps routing fast, offline and testable.

The same module hosts compaction — the "hidden" agent that compresses an
over-long conversation (``core/agents.py::compact_context``) so a fresh run
can continue from a summary. It triggers automatically during the thinking
phase when a persisted session grows past config.compaction_chars, and
manually via ``/compact``.

User-defined agents can be added (or built-ins overridden) through the
``agents`` section of settings.json — same shape as opencode's ``agent``
section: {name, description, system_prompt, read_only, uses_planning,
hidden, mode}. Custom agents are routable via ``--agent`` / intent rules.

Copyright (c) 2025 dev-assist contributors. MIT License.
Compaction prompt adapted from opencode (Copyright (c) 2025 opencode,
MIT License).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ── Agent model ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class AgentSpec:
    """One registered agent: a persona the tool loop can be pointed at."""

    id: str
    name: str
    description: str
    system_prompt: str
    mode: str = "primary"          # "primary" | "subagent" (like opencode)
    hidden: bool = False           # hidden agents are never routed-to directly
    read_only: bool = False        # reviewer/explore: no edits, no planning
    uses_planning: bool = True     # False → run as a single, direct step
    model_hint: str | None = None  # optional preferred model (informational)


# ── System prompts ────────────────────────────────────────────────────────────

_PROMPT_BUILD = """You are dev-assist, a coding agent that works directly in the user's project.

You have tools. Use them instead of guessing:
- Explore with list_dir, glob, and grep before assuming where code lives.
- Always read_file before you edit_file, so you can quote the original text exactly.
- edit_file needs old_string to match the raw file exactly and appear exactly once. Never include the line-number prefixes that read_file adds.
- Use apply_patch for multi-file edits, file moves, or additions/deletions in one call.
- Use bash for builds, tests, and git — not for reading or editing files.
- Use web_search and web_fetch to look up external info and read pages.
- Use todowrite to track the remaining work on a long task, and task to delegate an isolated chunk to a subagent.
- When you need a decision or preference from the user, use question instead of guessing.
- Load reusable instructions with skill before proceeding when one applies.

Work in small, verifiable steps. After changing code, check your work (run the test, re-read the file) rather than assuming it worked.

When the task is done, stop calling tools and reply with a short summary of what you changed. Be concise. Do not pad the answer with restatements of the question.

Working directory: {workdir}"""

_PROMPT_CODER = """You are the coder agent for dev-assist. Your job is implementation: write, edit and fix code with care.

Rules:
- Find the exact code first (list_dir, glob, grep, read_file). Never edit blind.
- Make the minimum change that satisfies the request. Do not refactor unrelated code.
- Follow the conventions already present in the file and in AGENTS.md / existing instruction files.
- After you change something, verify it: run the relevant test, typecheck, or at minimum re-read the modified region.
- Preserve existing behaviour unless the task explicitly changes it.
- If a request is ambiguous, read the surrounding code and other call sites before deciding; only use question when the code cannot answer it.
- When done, stop calling tools and summarize: what changed, which files, how you verified it.

Working directory: {workdir}"""

_PROMPT_REVIEWER = """You are the reviewer agent for dev-assist. Your job is to review code and report problems clearly — you never edit code.

Rules:
- Only review the code in scope of the request: the diff, the files, or the changes named. Do not audit unrelated pre-existing code.
- Gather real context: read the full files involved, not just a diff fragment. Check conventions (AGENTS.md, .editorconfig) before claiming a style issue.
- Flag bugs with confidence. If you cannot verify a suspected bug, investigate with read/grep/bash first; if still unsure, say "I'm not sure about X" rather than asserting it.
- Prioritise: bugs and security first, then structure/fit, then obviously-problematic performance. Skip style preferences that do not violate project conventions.
- Be matter-of-fact. State the concrete scenario or input required for a problem to occur and its severity. No flattery.
- Report as a ranked list of findings: what, where, why it matters, suggested fix (text only).
- Stop when the review is complete; do not offer to implement the fixes.

Working directory: {workdir}"""

_PROMPT_EXPLORE = """You are the explore agent for dev-assist. You answer questions about the codebase quickly and accurately.

Rules:
- Use list_dir, glob, grep and read_file to find the ground truth. Prefer reading the actual code or config over guessing.
- Use bash only for read-only commands (git log, find, greps) when needed.
- When asked "how does X work", trace the real call path from entrypoint to effect and quote the relevant lines.
- Answer directly and concisely, with file:line references. Do not pad. Do not edit files.

Working directory: {workdir}"""

_PROMPT_GENERAL = _PROMPT_BUILD  # subagent for delegated chunks: full toolset

# Compaction is a hidden agent: it never runs the tool loop directly, its
# prompt describes the summarisation task executed by compact_context().
_PROMPT_COMPACTION_AGENT = """You are a context summarization agent. You are given a conversation between a user and an assistant coding agent. Your goal is to produce a structured summary so that a fresh agent run can continue the work without the full transcript.

Preserve, when present:
- the user's goal(s) and any explicit constraints,
- decisions and their rationale,
- files touched / created and what was done to each,
- open questions, TODOs and known failures or errors,
- the exact next step the assistant was about to take.

Use terse bullets. Keep exact identifiers, paths and command names verbatim. Do not continue the conversation. Respond in the same language as the conversation. Output the summary only."""


# ── Registry ──────────────────────────────────────────────────────────────────

def _builtin_agents() -> dict[str, AgentSpec]:
    return {
        "build": AgentSpec(
            id="build",
            name="build",
            description="Default agent — executes tools and edits, with planning.",
            system_prompt=_PROMPT_BUILD,
        ),
        "coder": AgentSpec(
            id="coder",
            name="coder",
            description="Implementation specialist — minimal, verified code changes.",
            system_prompt=_PROMPT_CODER,
        ),
        "reviewer": AgentSpec(
            id="reviewer",
            name="reviewer",
            description="Read-only code reviewer — finds bugs, never edits.",
            system_prompt=_PROMPT_REVIEWER,
            read_only=True,
            uses_planning=False,
        ),
        "explore": AgentSpec(
            id="explore",
            name="explore",
            description="Read-only codebase explorer — fast answers with file:line refs.",
            system_prompt=_PROMPT_EXPLORE,
            read_only=True,
            uses_planning=False,
        ),
        "general": AgentSpec(
            id="general",
            name="general",
            description="General-purpose subagent for delegated research/implementation tasks.",
            system_prompt=_PROMPT_GENERAL,
            mode="subagent",
        ),
        "compaction": AgentSpec(
            id="compaction",
            name="compaction",
            description="Hidden agent — compresses over-long conversations into a summary.",
            system_prompt=_PROMPT_COMPACTION_AGENT,
            hidden=True,
            read_only=True,
            uses_planning=False,
            model_hint="compaction",
        ),
    }


def _load_settings() -> dict:
    """Read routing settings (enabled / default_agent / compaction_chars)
    and user-defined ``agents`` from settings.json. Never raises."""
    defaults = {
        "enabled": True,
        "default_agent": "build",
        "compaction_chars": 60000,
        "agents": {},
    }
    try:
        from core.config import AppConfig, load_config
        cfg = load_config()
        if isinstance(cfg, AppConfig):
            defaults["enabled"] = bool(getattr(cfg.routing, "enabled", True))
            defaults["default_agent"] = str(
                getattr(cfg.routing, "default_agent", "build") or "build"
            )
            defaults["compaction_chars"] = int(
                getattr(cfg.routing, "compaction_chars", 60000) or 60000
            )
            defaults["agents"] = dict(cfg.agents or {})
        elif isinstance(cfg, dict):
            routing = cfg.get("routing") or {}
            defaults["enabled"] = bool(routing.get("enabled", True))
            defaults["default_agent"] = str(routing.get("default_agent", "build") or "build")
            defaults["compaction_chars"] = int(routing.get("compaction_chars", 60000) or 60000)
            defaults["agents"] = dict(cfg.get("agents") or {})
    except Exception:
        pass
    return defaults


def all_agents() -> dict[str, AgentSpec]:
    """Built-ins merged with user-defined agents (which may override them)."""
    agents = _builtin_agents()
    for key, raw in (_load_settings()["agents"] or {}).items():
        if not isinstance(raw, dict):
            continue
        base = agents.get(key)
        agents[key] = AgentSpec(
            id=key,
            name=str(raw.get("name") or (base.name if base else key)),
            description=str(raw.get("description") or (base.description if base else "")),
            system_prompt=str(
                raw.get("system_prompt")
                or (base.system_prompt if base else _PROMPT_BUILD)
            ),
            mode=str(raw.get("mode") or (base.mode if base else "primary")),
            hidden=bool(raw.get("hidden", base.hidden if base else False)),
            read_only=bool(raw.get("read_only", base.read_only if base else False)),
            uses_planning=bool(raw.get("uses_planning", True if base is None else base.uses_planning)),
            model_hint=(str(raw["model_hint"]) if raw.get("model_hint") else
                        (base.model_hint if base else None)),
        )
    return agents


def resolve(agent_id: str | AgentSpec | None) -> AgentSpec:
    """Resolve a name/AgentSpec to a concrete spec. Unknown -> build."""
    if isinstance(agent_id, AgentSpec):
        return agent_id
    agents = all_agents()
    build_fallback = None
    for a in agents.values():
        if a.id == "build":
            build_fallback = a
            break
    if build_fallback is None:
        build_fallback = _builtin_agents()["build"]
    if not agent_id:
        return build_fallback
    spec = agents.get(str(agent_id).lower())
    if spec is not None and not spec.hidden:
        return spec
    # hidden/unknown ids fall back to the default executor
    return build_fallback


def routable_agents() -> dict[str, AgentSpec]:
    """Agents a task can be routed to (hidden markers excluded)."""
    return {k: v for k, v in all_agents().items() if not v.hidden}


# ── Routing (deterministic intent detection) ─────────────────────────────────

# Keyword → weight, per routable agent. Higher weight wins more strongly.
_ROUTE_RULES: dict[str, tuple[tuple[str, int], ...]] = {
    "reviewer": (
        ("review pr", 6), ("review the diff", 6), ("review this change", 6),
        ("review my code", 6), ("review the change", 5), ("review", 4),
        ("security review", 6), ("code review", 5), ("spot bugs", 5),
        ("find the bugs", 5), ("find bugs", 4), ("any issues", 4),
        ("do you see any problems", 5), ("audit", 4), ("check my code", 4),
        ("is this correct", 3), ("does this have bugs", 5), ("review the pull", 4),
        ("review the code", 5), ("critique", 3),
    ),
    "coder": (
        ("refactor", 4), ("implement", 4), ("fix the bug", 4),
        ("fix this bug", 4), ("fix the failing test", 4), ("write a function", 4),
        ("add a function", 4), ("add feature", 4), ("add tests", 4),
        ("write tests", 4), ("unit test", 3), ("optimize", 3), ("migrate", 3),
        ("add a", 2), ("update the", 2), ("change the", 2), ("extract", 3),
        ("rename", 3), ("make the", 2), ("create a", 2), ("build a", 3),
        ("add error handling", 4), ("write the", 2), ("modify ", 2),
    ),
    "explore": (
        ("where is", 4), ("where are", 4), ("which file", 4),
        ("find the file", 5), ("locate", 4), ("how does", 4), ("how do", 4),
        ("what does", 4), ("what is", 3), ("where does", 4), ("search for", 3),
        ("explain the", 3), ("explore", 3), ("find where", 4), ("find how", 4),
        ("find what", 3), ("find a", 4), ("find the ", 4), ("lookup", 3),
        ("figure out", 3),
    ),
}

# Tie-break / precedence order for equal scores.
_PRECEDENCE = ("reviewer", "coder", "explore", "build")


@dataclass(frozen=True)
class RouteDecision:
    """Outcome of routing: which agent handles a task and why."""

    agent_id: str
    confidence: float          # 0.0 (nothing matched) .. 1.0 (forced/strong)
    reason: str
    forced: bool = False

    @property
    def agent(self) -> AgentSpec:
        return resolve(self.agent_id)


def _score(text: str) -> dict[str, int]:
    scores: dict[str, int] = {}
    lowered = text.lower()
    for agent_id, rules in _ROUTE_RULES.items():
        total = 0
        for phrase, weight in rules:
            if phrase in lowered:
                total += weight
        if total:
            scores[agent_id] = total
    return scores


def route(
    task: str,
    *,
    context: str = "",
    forced: str | None = None,
    settings: dict | None = None,
    default_agent: str | None = None,
) -> RouteDecision:
    """
    Decide which agent should handle ``task``.

    ``context`` is optional recent session text (last turns); it boosts intent
    matching but never dominates the explicit task. ``forced`` is an explicit
    ``--agent`` override and always wins. ``settings`` overrides the routing
    config (used by tests / callers that already loaded it).
    """
    settings = settings if settings is not None else _load_settings()
    enabled = bool(settings.get("enabled", True))
    default = (default_agent or settings.get("default_agent") or "build").lower()
    task = (task or "").strip()
    context = (context or "").strip()

    forced = (forced or "").strip().lower()
    if forced:
        spec = resolve(forced)
        return RouteDecision(
            agent_id=spec.id,
            confidence=1.0,
            reason=f"explicitly requested agent ({forced})",
            forced=True,
        )

    if not enabled:
        base = resolve(default)
        return RouteDecision(
            agent_id=base.id,
            confidence=0.0,
            reason="routing disabled — using default agent",
            forced=False,
        )

    if not task:
        base = resolve(default)
        return RouteDecision(
            agent_id=base.id,
            confidence=0.0,
            reason="empty task — using default agent",
        )

    # Leading word of intent: "review ..." commits to the reviewer, "explore
    # ..." to explore — a fast, explicit signal.
    head = re.match(r"^([a-z]+)[\s:]+", task.lower())
    if head:
        lead = head.group(1)
        if lead == "review":
            return RouteDecision("reviewer", 1.0, "task begins with 'review'")
        if lead == "explore":
            return RouteDecision("explore", 1.0, "task begins with 'explore'")

    scores = _score(f"{task}\n{context if len(context) < 4000 else context[:4000]}")
    if not scores:
        base = resolve(default)
        return RouteDecision(base.id, 0.0, "no intent signal — using default agent")

    best_id = max(
        scores,
        key=lambda aid: (scores[aid], -_PRECEDENCE.index(aid) if aid in _PRECEDENCE else 0),
    )
    best_score = scores[best_id]
    high = best_score >= 4
    confidence = 0.9 if high else 0.5
    reason = f"intent matched ({best_id}: score {best_score})"
    if not high:
        base = resolve(default)
        return RouteDecision(
            base.id, 0.3, f"weak intent signal ({best_id}); using default agent"
        )
    return RouteDecision(best_id, confidence, reason)


def describe() -> str:
    """Human-readable agent inventory for the /agents command."""
    lines = ["\n[bold]Agents[/bold]\n"]
    for spec in routable_agents().values():
        mark = f"[bold]/{spec.id}[/bold]"
        lines.append(f"  {mark:<26} {spec.description}")
        if spec.read_only:
            lines[-1] += "  [dim](read-only)[/dim]"
    lines.append("")
    return "\n".join(lines)


# ── Compaction ────────────────────────────────────────────────────────────────

_COMPACT_CHUNK_CHARS = 28000  # rough per-call budget for small local models
_COMPACT_MAX_CHUNKS = 20


def _msgs_text(messages) -> str:
    """Render message objects/dicts into a user/assistant transcript."""
    lines: list[str] = []
    for m in messages:
        if isinstance(m, dict):
            role = m.get("role", "")
            content = str(m.get("content", "") or "")
        else:
            role = getattr(m, "role", "")
            content = str(getattr(m, "content", "") or "")
        if content.strip():
            prefix = "user" if role in ("user", "human") else "assistant"
            lines.append(f"{prefix}: {content.strip()}")
    return "\n\n".join(lines)


def should_compact(messages, threshold_chars: int) -> bool:
    """True when the accumulated conversation text warrants compaction."""
    if threshold_chars <= 0:
        return False
    total = 0
    count_user = 0
    for m in messages:
        content = str(m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "") or "")
        total += len(content)
        role = m.get("role", "") if isinstance(m, dict) else getattr(m, "role", "")
        if role == "user":
            count_user += 1
    return total > threshold_chars and count_user >= 2


def _msg_text(message) -> str:
    if isinstance(message, dict):
        return str(message.get("content") or "")
    return str(getattr(message, "content", "") or "")


def compact_context(
    transcript: str,
    *,
    provider=None,
    chunk_chars: int = _COMPACT_CHUNK_CHARS,
) -> str:
    """
    Compress a conversation transcript into a carry-forward summary using the
    active model. Progressive: long transcripts are summarised chunk-by-chunk
    so context limits are respected for any provider. Never raises — on any
    failure it returns a safe truncated excerpt so routing can continue.
    """
    if not (transcript or "").strip():
        return ""
    chunks = _chunk_text(transcript, chunk_chars)[:_COMPACT_MAX_CHUNKS]
    if not chunks:
        return ""

    try:
        if provider is None:
            from core.ai import get_provider
            provider = get_provider()

        accumulated = ""

        def _call(user_text: str) -> str:
            messages = [
                {"role": "system", "content": _PROMPT_COMPACTION_AGENT},
                {"role": "user", "content": user_text},
            ]
            out = provider.chat(messages, tools=None)
            return _msg_text(out).strip()

        for _idx, chunk in enumerate(chunks):
            if accumulated:
                user_text = (
                    f"Prior summary:\n{accumulated}\n\n"
                    f"Continue with the next part of the conversation:\n{chunk}"
                )
            else:
                user_text = (
                    f"Conversation:\n{chunk}\n\n"
                    "Summarize the conversation following your instructions."
                )
            piece = _call(user_text)
            accumulated = piece if not accumulated else f"{accumulated}\n\n{piece}"
            if not accumulated:
                break

        if accumulated:
            return (
                "Compacted conversation summary:\n" + accumulated
            )
    except Exception:
        pass

    # Safe fallback: the earliest non-trivial excerpt, capped.
    excerpt = " ".join(transcript.split())[:1200]
    return f"Compacted conversation (excerpt):\n{excerpt}"


def _chunk_text(text: str, chunk_chars: int) -> list[str]:
    span = max(1000, int(chunk_chars))
    if len(text) <= span:
        return [text]
    return [text[i:i + span] for i in range(0, len(text), span)]
