"""
dev-assist — Chainlit Web Interface (no auth)

Features:
  - Direct access — no login or registration required
  - Per-session chat history (in-memory)
  - Per-session model settings
  - File upload → AI reads and answers
  - Ollama start/stop and status via action buttons + commands
  - clear / history / help / ollama on|off|status commands
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import threading
from pathlib import Path
from typing import Optional

# -- Path bootstrap -----------------------------------------------------------
def _bootstrap_path() -> None:
    if getattr(sys, "frozen", False):
        _mei = getattr(sys, "_MEIPASS", None)
        if _mei and _mei not in sys.path:
            sys.path.insert(0, _mei)
    else:
        _root = os.path.dirname(os.path.abspath(__file__))
        if _root not in sys.path:
            sys.path.insert(0, _root)

_bootstrap_path()
# -----------------------------------------------------------------------------

import chainlit as cl
from chainlit.input_widget import Select

MAX_CONTEXT_MESSAGES = 20

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_global_config() -> dict:
    try:
        from core.config import load_config
        cfg = load_config()
        return cfg.model_dump() if hasattr(cfg, "model_dump") else (cfg if isinstance(cfg, dict) else {})
    except Exception:
        p = os.path.join(
            os.environ.get("DEV_ASSIST_CONFIG_DIR", ""),
            "settings.json",
        ) or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config", "settings.json"
        )
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            return {}


def _get_ollama_models() -> list[str]:
    """
    Return installed model names by querying the running ollama binary/API.
    Returns an empty list if ollama is stopped or not installed — no hardcoded fallback.
    """
    if _ollama_status() != "running":
        return []
    try:
        import ollama
        names = [m.model for m in ollama.list().models]
        return names
    except Exception:
        # Fallback: call 'ollama list' subprocess directly (works when frozen too)
        try:
            import subprocess
            result = subprocess.run(
                ["ollama", "list"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                lines = result.stdout.strip().splitlines()
                # Skip header line ("NAME   ID   SIZE   MODIFIED")
                names = []
                for line in lines[1:]:
                    parts = line.split()
                    if parts:
                        names.append(parts[0])
                return names
        except Exception:
            pass
        return []


async def _reload_model_settings() -> None:
    """
    Re-build ChatSettings with a fresh model list from the running ollama.
    Called after ollama is started so the dropdown fills automatically.
    """
    global_cfg   = _load_global_config()
    engine       = (cl.user_session.get("settings") or {}).get("engine") or global_cfg.get("ai_engine", "ollama")
    models       = _get_ollama_models()
    saved_model  = (cl.user_session.get("settings") or {}).get("ollama_model") or global_cfg.get("ollama_model", "")

    if models:
        initial_model = saved_model if saved_model in models else models[0]
        model_values  = models
    else:
        # Ollama stopped — show a placeholder; user cannot pick a real model
        initial_model = "⚠ ollama stopped — no models"
        model_values  = [initial_model]

    settings = await cl.ChatSettings([
        Select(id="engine", label="AI Engine",
               values=["ollama", "api"], initial_value=engine),
        Select(id="ollama_model", label="Ollama Model",
               values=model_values,
               initial_value=initial_model),
    ]).send()
    cl.user_session.set("settings", settings)

# ---------------------------------------------------------------------------
# Ollama status / control  (delegates to core.ollama_status)
# ---------------------------------------------------------------------------

def _ollama_status() -> str:
    """Return 'running' | 'stopped' | 'not_installed'."""
    try:
        from core.ollama_status import get_status
        return get_status()
    except Exception:
        return "unknown"


def _ollama_status_line() -> str:
    status = _ollama_status()
    if status == "running":
        return "🟢 ollama **running**"
    elif status == "stopped":
        return "🔴 ollama **stopped**"
    elif status == "not_installed":
        return "⚫ ollama **not installed**"
    return "❓ ollama **unknown**"


async def _ollama_start() -> str:
    try:
        from core.ollama_status import start_ollama
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, start_ollama)
    except Exception as exc:
        return f"❌ Error: {exc}"


async def _ollama_stop() -> str:
    try:
        from core.ollama_status import stop_ollama
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, stop_ollama)
    except Exception as exc:
        return f"❌ Error: {exc}"


async def _send_ollama_status_msg() -> None:
    """Send current ollama status with on/off action buttons."""
    status = _ollama_status()
    line   = _ollama_status_line()

    if status == "running":
        actions = [
            cl.Action(name="ollama_stop",   payload={"action": "stop"},   label="⏹  Stop ollama"),
            cl.Action(name="ollama_status", payload={"action": "status"}, label="🔄  Refresh status"),
        ]
    elif status == "stopped":
        actions = [
            cl.Action(name="ollama_start",  payload={"action": "start"},  label="▶  Start ollama"),
            cl.Action(name="ollama_status", payload={"action": "status"}, label="🔄  Refresh status"),
        ]
    else:
        actions = [
            cl.Action(name="ollama_status", payload={"action": "status"}, label="🔄  Refresh status"),
        ]

    await cl.Message(content=line, actions=actions, author="ollama").send()

# ---------------------------------------------------------------------------
# In-memory chat history (per session)
# ---------------------------------------------------------------------------

def _get_history(limit: int = 100) -> list[dict]:
    history = cl.user_session.get("history") or []
    return history[-limit:]


def _save_msg(role: str, content: str) -> None:
    history = cl.user_session.get("history") or []
    history.append({"role": role, "content": content})
    cl.user_session.set("history", history)


def _clear_history() -> None:
    cl.user_session.set("history", [])

# ---------------------------------------------------------------------------
# AI streaming
# ---------------------------------------------------------------------------

async def _stream_response(user_text: str, file_context: str = ""):
    from core.ai import _load_config as _ai_cfg, _get_engine, _get_ollama_model

    cfg    = _ai_cfg()
    engine = _get_engine(cfg)

    settings = cl.user_session.get("settings") or {}
    if settings.get("engine"):
        engine = settings["engine"]

    history = _get_history(limit=MAX_CONTEXT_MESSAGES)
    msgs    = [{"role": m["role"], "content": m["content"]} for m in history]
    full_q  = f"{user_text}\n\n--- Attached file ---\n{file_context}" if file_context else user_text
    msgs.append({"role": "user", "content": full_q})

    if engine == "ollama":
        # Guard: refuse to call if ollama is not running
        if _ollama_status() != "running":
            yield "⚠️ Ollama is stopped. Start it with `ollama on` or the **▶ Start ollama** button, then try again."
            return
        try:
            import ollama
            model = settings.get("ollama_model") or _get_ollama_model(cfg)
            # Guard: block placeholder value written when ollama was stopped
            if not model or model.startswith("⚠"):
                yield "⚠️ No valid model selected. Start ollama and pick a model from the settings panel."
                return
            token_queue: queue.Queue = queue.Queue()

            def _producer() -> None:
                try:
                    for chunk in ollama.chat(model=model, messages=msgs, stream=True):
                        token = (
                            chunk.get("message", {}).get("content", "")
                            if isinstance(chunk, dict)
                            else getattr(getattr(chunk, "message", None), "content", "")
                        )
                        if token:
                            token_queue.put(token)
                except Exception as exc:
                    token_queue.put(f"\n\n⚠️ Ollama error: {exc}")
                finally:
                    token_queue.put(None)  # sentinel

            t = threading.Thread(target=_producer, daemon=True)
            t.start()

            loop = asyncio.get_running_loop()
            while True:
                token = await loop.run_in_executor(None, token_queue.get)
                if token is None:
                    break
                yield token
        except Exception as exc:
            yield f"\n\n⚠️ Ollama error: {exc}"
    else:
        try:
            from core.ai import _get_api_key, _get_api_url, _get_api_model
            import urllib.request, json as _j

            api_key = _get_api_key(cfg)
            api_url = settings.get("api_url") or _get_api_url(cfg)
            model   = settings.get("api_model") or _get_api_model(cfg)
            payload = _j.dumps({"model": model, "messages": msgs, "stream": False}).encode()
            req = urllib.request.Request(
                api_url, data=payload,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {api_key}"},
            )

            def _call():
                with urllib.request.urlopen(req, timeout=60) as r:
                    return _j.loads(r.read())

            loop = asyncio.get_running_loop()
            data = await loop.run_in_executor(None, _call)
            yield data["choices"][0]["message"]["content"]
        except Exception as exc:
            yield f"\n\n⚠️ API error: {exc}"

# ---------------------------------------------------------------------------
# Chainlit lifecycle
# ---------------------------------------------------------------------------

@cl.on_chat_start
async def on_start():
    _clear_history()

    global_cfg   = _load_global_config()
    engine       = global_cfg.get("ai_engine", "ollama")
    saved_model  = global_cfg.get("ollama_model", "")
    models       = _get_ollama_models()  # empty [] when ollama is stopped

    if models:
        initial_model = saved_model if saved_model in models else models[0]
        model_values  = models
        active_model  = initial_model if engine == "ollama" else "api"
    else:
        # Ollama is not running — show placeholder, block real model selection
        initial_model = "⚠ ollama stopped — no models"
        model_values  = [initial_model]
        active_model  = "⚠ ollama stopped" if engine == "ollama" else "api"

    settings = await cl.ChatSettings([
        Select(id="engine", label="AI Engine",
               values=["ollama", "api"], initial_value=engine),
        Select(id="ollama_model", label="Ollama Model",
               values=model_values,
               initial_value=initial_model),
    ]).send()
    cl.user_session.set("settings", settings)

    await cl.Message(content=f"""## dev-assist

🤖 `{engine}/{active_model}`

---
📎 Attach files · `clear` · `history` · `ollama on/off/status` · `help`
""").send()

    # Show ollama status with action buttons
    await _send_ollama_status_msg()


@cl.on_settings_update
async def on_settings_update(settings: dict):
    cl.user_session.set("settings", settings)
    engine = settings.get("engine", "ollama")
    model  = settings.get("ollama_model", "")
    # Ignore the placeholder value written when ollama is stopped
    if model.startswith("⚠"):
        await cl.Message(content="⚠️ Ollama is stopped. Start it first with `ollama on`, then reload the settings.").send()
        return
    await cl.Message(content=f"✅ Model updated → `{engine}/{model}`").send()


@cl.action_callback("ollama_start")
async def on_ollama_start(action: cl.Action):
    await cl.Message(content="⏳ Starting ollama…", author="ollama").send()
    result = await _ollama_start()
    await cl.Message(content=result, author="ollama").send()
    await _send_ollama_status_msg()
    # Reload model dropdown so real models appear after start
    if _ollama_status() == "running":
        await _reload_model_settings()
        await cl.Message(content="🔄 Model list reloaded — select your model from settings.").send()


@cl.action_callback("ollama_stop")
async def on_ollama_stop(action: cl.Action):
    await cl.Message(content="⏳ Stopping ollama…", author="ollama").send()
    result = await _ollama_stop()
    await cl.Message(content=result, author="ollama").send()
    await _send_ollama_status_msg()


@cl.action_callback("ollama_status")
async def on_ollama_status(action: cl.Action):
    await _send_ollama_status_msg()


@cl.on_message
async def on_message(message: cl.Message):
    raw = message.content.strip()
    cmd = raw.lower()

    # ── Built-in commands ──────────────────────────────────────────────────
    if cmd in ("clear", "/clear", "clear history"):
        _clear_history()
        await cl.Message(content="🗑️ Chat history cleared.").send()
        return

    if cmd in ("history", "/history"):
        hist = _get_history(limit=20)
        if not hist:
            await cl.Message(content="No history yet.").send()
            return
        lines = []
        for m in hist:
            role  = "**You**" if m["role"] == "user" else "**AI**"
            short = m["content"][:120].replace("\n", " ")
            lines.append(f"{role}: {short}{'…' if len(m['content']) > 120 else ''}")
        await cl.Message(content="### 📜 Recent history\n\n" + "\n\n".join(lines)).send()
        return

    if cmd in ("help", "/help"):
        await cl.Message(content="""### Commands
- `clear` — clear chat history
- `history` — show recent messages
- `ollama on` — start ollama serve
- `ollama off` — stop ollama serve
- `ollama status` — show ollama status
- `help` — show this help
""").send()
        return

    # ── Ollama control commands ────────────────────────────────────────────
    if cmd in ("ollama on", "ollama start"):
        await cl.Message(content="⏳ Starting ollama…", author="ollama").send()
        result = await _ollama_start()
        await cl.Message(content=result, author="ollama").send()
        await _send_ollama_status_msg()
        if _ollama_status() == "running":
            await _reload_model_settings()
            await cl.Message(content="🔄 Model list reloaded — select your model from settings.").send()
        return

    if cmd in ("ollama off", "ollama stop"):
        await cl.Message(content="⏳ Stopping ollama…", author="ollama").send()
        result = await _ollama_stop()
        await cl.Message(content=result, author="ollama").send()
        await _send_ollama_status_msg()
        return

    if cmd in ("ollama status", "ollama"):
        await _send_ollama_status_msg()
        return

    # ── File upload ────────────────────────────────────────────────────────
    file_context = ""
    if message.elements:
        parts = []
        for el in message.elements:
            try:
                if hasattr(el, "path") and el.path:
                    content = Path(el.path).read_text(errors="replace")
                elif hasattr(el, "content") and el.content:
                    content = (el.content.decode("utf-8", errors="replace")
                               if isinstance(el.content, bytes) else str(el.content))
                else:
                    continue
                parts.append(f"[File: {el.name}]\n{content[:8000].replace(chr(0), '')}")
            except Exception as e:
                parts.append(f"[Could not read {getattr(el, 'name', '?')}: {e}]")
        file_context = "\n\n".join(parts)

    saved = raw
    if file_context:
        names = ", ".join(el.name for el in message.elements if hasattr(el, "name"))
        saved += f"\n[attached: {names}]"
    _save_msg("user", saved)

    # ── Stream AI response ─────────────────────────────────────────────────
    reply_msg  = cl.Message(content="")
    await reply_msg.send()
    full_reply = []
    async for token in _stream_response(raw, file_context):
        await reply_msg.stream_token(token)
        full_reply.append(token)
    await reply_msg.update()
    _save_msg("assistant", "".join(full_reply))
