#!/usr/bin/env python3
"""Live end-to-end check for the dev-assist agent loop against a real
OpenAI-compatible endpoint. Read-only: never mutates the working tree.

Env that drives it (all optional):
  FORGE_TEST_ENDPOINT   base URL of an OpenAI-compatible /chat/completions API
  FORGE_TEST_MODEL      model name to run the checks against
  FORGE_TEST_API_KEY    bearer key, only sent if set

Exit codes:
  0 + "SKIPPED"      when FORGE_TEST_ENDPOINT is unset/empty
  0 + "NOT VERIFIED" when the endpoint/model is unreachable or unusable
  0 + "VERIFIED"     when all checks pass
  1 + "FAILED"       when a reachable endpoint misbehaves (agent self-report,
                     missing tool round trip, …)

Checks:
  1. plain chat round trip (also the reachability probe)
  2. agent run for "hi" — the final answer must NOT fall back to advertising the
     agent's own setup (scaffolding / self-report). Heuristic: none of the
     self-report markers below and a short reply.
  3. agent tool-call round trip in a temp working directory — a file carrying a
     random marker must be read via a real tool call and echoed in the answer.
"""

import os
import secrets
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "dev-assist"))

try:  # dev-assist stack — only needed for a real run (SKIPPED must work bare)
    from core import ai as _core_ai  # noqa: E402  (dev-assist/ on path)
    from core.agent import auto_approve_readonly, run_agent  # noqa: E402
    from core.providers import PROVIDERS, make_provider  # noqa: E402
    _STACK_ERROR = None
except Exception as _exc:  # pragma: no cover - env-dependent
    _STACK_ERROR = _exc

# Scaffolding / self-report markers: verbatim text an agent would emit when it
# volunteers its own setup instead of answering the prompt. Heuristic, kept
# deliberately narrow so normal chat replies are not false positives.
_SELF_REPORT_MARKERS = (
    "dev-assist",
    "my working directory",
    "i have access to",
    "available tools",
    "my capabilities",
    "here's what i can do",
    "tool list",
    "i am an ai",
    "i'm an ai",
)

ENDPOINT = os.environ.get("FORGE_TEST_ENDPOINT", "").strip()
MODEL = os.environ.get("FORGE_TEST_MODEL", "").strip()
API_KEY = os.environ.get("FORGE_TEST_API_KEY", "").strip()


def _say(msg: str) -> None:
    print(msg, flush=True)


def _fail(what: str) -> None:
    _say(f"\nFAILED: {what}")
    sys.exit(1)


def _make_provider():
    profile = dict(PROVIDERS["custom"])
    profile["default_model"] = MODEL
    provider = make_provider("custom", profile, API_KEY)
    provider.base_url = ENDPOINT.rstrip("/")
    return provider


def _chat_reply(provider, text: str) -> str:
    msg = provider.chat(
        [{"role": "user", "content": text}], model=MODEL)
    content = (msg.get("content") or "").strip()
    if not content:
        _fail(f"empty reply from the endpoint ({ENDPOINT})")
    return content


def _install_forge_provider(provider):
    """Point core.ai.get_provider (used by run_agent) at the FORGE provider."""
    original = _core_ai.get_provider
    _core_ai.get_provider = lambda cfg=None, provider_id=None, key="": provider
    return original


def check_chat(provider):
    _say(f"[1/3] plain chat round trip ({MODEL} @ {ENDPOINT})…")
    reply = _chat_reply(provider, "Reply with exactly: FORGE-PONG")
    ok = "FORGE-PONG" in reply
    _say(f"      reply: {reply!r}")
    if not ok:
        _fail("the endpoint did not echo the FORGE-PONG round trip")
    return True


def check_agent_hi(provider):
    _say("[2/3] agent run for \"hi\" (must not self-report)…")
    original = _install_forge_provider(provider)
    answered = False
    try:
        with tempfile.TemporaryDirectory() as workdir:
            answer = run_agent(
                "hi",
                workdir=workdir,
                approver=auto_approve_readonly,
                agent="build",
                max_steps=4,
            ).strip()
        answered = True
    finally:
        _core_ai.get_provider = original
    _say(f"      answer: {answer[:200]!r}")
    if not answered:
        _fail("agent produced no final answer")
    low = answer.lower()
    hits = [m for m in _SELF_REPORT_MARKERS if m in low]
    if hits:
        _fail(f"agent self-reported its setup instead of answering: {hits}")
    if len(answer) > 600:
        _fail(f"agent replied with a long monologue for \"hi\" "
              f"({len(answer)} chars) — smells like a setup dump")
    return True


def check_agent_tool_round_trip(provider):
    _say("[3/3] agent tool-call round trip (read a marker file in a temp dir)…")
    marker = "FORGE-" + secrets.token_hex(6)
    events = []
    original = _install_forge_provider(provider)
    finished = False
    last_answer = ""
    try:
        with tempfile.TemporaryDirectory() as workdir:
            with open(os.path.join(workdir, "note.txt"), "w") as f:
                f.write(marker + "\n")
            last_answer = run_agent(
                "Open the file note.txt in this project and report exactly "
                "what it says.",
                workdir=workdir,
                approver=auto_approve_readonly,
                on_event=lambda kind, text: events.append((kind, text)),
                agent="build",
                max_steps=6,
            ).strip()
        finished = True
    finally:
        _core_ai.get_provider = original
    _say(f"      answer: {last_answer[:200]!r}")
    if not finished:
        _fail("tool-call agent run produced no final answer")
    if not any(kind == "tool" for kind, _ in events):
        _fail("agent never issued a real tool call in a task that requires one")
    if marker not in last_answer:
        _fail("agent answer did not echo the marker read from note.txt")
    _say(f"      tool events: {sum(1 for k, _ in events if k == 'tool')}")
    return True


def main() -> int:
    if not ENDPOINT:
        _say("SKIPPED: FORGE_TEST_ENDPOINT is not set — refusing to guess a "
             "target URL.")
        return 0
    if not MODEL:
        _say("SKIPPED: FORGE_TEST_MODEL is not set "
             "(FORGE_TEST_ENDPOINT is).")
        return 0
    if _STACK_ERROR is not None:
        _say(f"NOT VERIFIED: dev-assist stack is not importable here: "
             f"{_STACK_ERROR}")
        return 0

    _say(f"live check: model={MODEL or '<none>'} endpoint={ENDPOINT}")
    try:
        provider = _make_provider()
    except Exception as exc:
        _say(f"NOT VERIFIED: could not build the provider: {exc}")
        return 0

    try:
        check_chat(provider)
    except KeyboardInterrupt:
        _say("\ninterrupted")
        return 130
    except Exception as exc:
        _say(f"NOT VERIFIED: endpoint/model unusable ({ENDPOINT}): {exc}")
        return 0

    for check in (check_agent_hi, check_agent_tool_round_trip):
        try:
            check(provider)
        except KeyboardInterrupt:
            _say("\ninterrupted")
            return 130

    _say("\nVERIFIED: 3/3 checks passed against a live endpoint.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
