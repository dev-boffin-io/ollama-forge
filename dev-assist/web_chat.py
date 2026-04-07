"""
dev-assist — Chainlit Web Interface (no auth)

Features:
  - Direct access — no login or registration required
  - Per-session chat history (in-memory)
  - Per-session model settings
  - File upload → AI reads and answers
  - clear / history / help commands
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
    try:
        import ollama
        names = [m.model for m in ollama.list().models]
        return names or ["qwen2.5-coder:7b", "llama3.2:3b"]
    except Exception:
        return ["qwen2.5-coder:7b", "llama3.2:3b", "codellama:7b", "mistral:7b"]

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
        try:
            import ollama
            model = settings.get("ollama_model") or _get_ollama_model(cfg)
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

    global_cfg    = _load_global_config()
    engine        = global_cfg.get("ai_engine", "ollama")
    ollama_model  = global_cfg.get("ollama_model", "qwen2.5-coder:7b")
    ollama_models = _get_ollama_models()

    settings = await cl.ChatSettings([
        Select(id="engine", label="AI Engine",
               values=["ollama", "api"], initial_value=engine),
        Select(id="ollama_model", label="Ollama Model",
               values=ollama_models,
               initial_value=ollama_model if ollama_model in ollama_models else ollama_models[0]),
    ]).send()
    cl.user_session.set("settings", settings)

    active_model = ollama_model if engine == "ollama" else "api"
    await cl.Message(content=f"""## dev-assist

🤖 `{engine}/{active_model}`

---
📎 Attach files · `clear` clear history · `history` view recent chat · `help` show commands
""").send()


@cl.on_settings_update
async def on_settings_update(settings: dict):
    cl.user_session.set("settings", settings)
    engine = settings.get("engine", "ollama")
    model  = settings.get("ollama_model", "qwen2.5-coder:7b")
    await cl.Message(content=f"✅ Model updated → `{engine}/{model}`").send()


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
- `help` — show this help
""").send()
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
