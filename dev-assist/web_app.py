"""
dev-assist — FastAPI Web Interface (no auth, single onefile-friendly process)

Replaces the old Chainlit web_chat.py. Runs entirely in-process (no
subprocess, no external CLI needed) which makes PyInstaller onefile
packaging trivial — everything ships inside the frozen binary.

Features (parity with the old Chainlit UI):
  - Direct access — no login or registration required
  - Per-session chat history (in-memory, keyed by a browser cookie)
  - Per-session engine/model settings
  - File upload -> AI reads and answers
  - Ollama start/stop and status via buttons + commands
  - clear / history / help / ollama on|off|status commands
  - Streaming responses via Server-Sent Events (SSE)
"""

from __future__ import annotations

import asyncio
import json
import os
import queue
import sys
import threading
import uuid
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

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

MAX_CONTEXT_MESSAGES = 20
SESSION_COOKIE = "dev_assist_sid"

WEBUI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui")

app = FastAPI(title="dev-assist")

# ---------------------------------------------------------------------------
# Per-session in-memory state
# ---------------------------------------------------------------------------
_sessions: dict[str, dict] = {}
_sessions_lock = threading.Lock()


def _get_session(sid: str) -> dict:
    with _sessions_lock:
        s = _sessions.get(sid)
        if s is None:
            s = {"history": [], "settings": {}}
            _sessions[sid] = s
        return s


def _get_history(sid: str, limit: int = 100) -> list[dict]:
    return _get_session(sid)["history"][-limit:]


def _save_msg(sid: str, role: str, content: str) -> None:
    _get_session(sid)["history"].append({"role": role, "content": content})


def _clear_history(sid: str) -> None:
    _get_session(sid)["history"] = []


def _get_settings(sid: str) -> dict:
    return _get_session(sid)["settings"]


def _set_settings(sid: str, **kwargs) -> None:
    _get_session(sid)["settings"].update(kwargs)


def _sid_from_request(request: Request) -> str:
    return request.cookies.get(SESSION_COOKIE) or str(uuid.uuid4())


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
    """Return installed model names, or [] if ollama is stopped/not installed."""
    if _ollama_status() != "running":
        return []
    try:
        import ollama
        return [m.model for m in ollama.list().models]
    except Exception:
        try:
            import subprocess
            result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                lines = result.stdout.strip().splitlines()
                return [ln.split()[0] for ln in lines[1:] if ln.split()]
        except Exception:
            pass
        return []


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


# ---------------------------------------------------------------------------
# AI streaming — yields plain text tokens
# ---------------------------------------------------------------------------

async def _stream_response(sid: str, user_text: str, file_context: str = "", images: list[str] | None = None):
    from core.ai import _load_config as _ai_cfg, _get_engine, _get_ollama_model

    cfg = _ai_cfg()
    engine = _get_engine(cfg)

    settings = _get_settings(sid)
    if settings.get("engine"):
        engine = settings["engine"]

    history = _get_history(sid, limit=MAX_CONTEXT_MESSAGES)
    msgs = [{"role": m["role"], "content": m["content"]} for m in history]
    full_q = f"{user_text}\n\n--- Attached file ---\n{file_context}" if file_context else user_text
    last_msg = {"role": "user", "content": full_q}
    if images:
        # Ollama's vision API takes base64 strings directly under "images".
        last_msg["images"] = images
    msgs.append(last_msg)

    if engine == "ollama":
        if _ollama_status() != "running":
            yield "⚠️ Ollama is stopped. Start it with `ollama on` or the ▶ Start button, then try again."
            return
        try:
            import ollama
            model = settings.get("ollama_model") or _get_ollama_model(cfg)
            if not model or model.startswith("⚠"):
                yield "⚠️ No valid model selected. Start ollama and pick a model from settings."
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
                    if images:
                        token_queue.put(
                            f"\n\n⚠️ Ollama error: {exc}\n"
                            f"(Note: the selected model may not support image/vision input.)"
                        )
                    else:
                        token_queue.put(f"\n\n⚠️ Ollama error: {exc}")
                finally:
                    token_queue.put(None)

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
            model = settings.get("api_model") or _get_api_model(cfg)

            api_msgs = msgs[:-1]
            if images:
                # OpenAI-compatible vision format: content becomes a list of
                # text + image_url parts instead of a plain string.
                content_parts = [{"type": "text", "text": full_q}]
                for b64 in images:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    })
                api_msgs.append({"role": "user", "content": content_parts})
            else:
                api_msgs.append({"role": "user", "content": full_q})

            payload = _j.dumps({"model": model, "messages": api_msgs, "stream": False}).encode()
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
# API routes
# ---------------------------------------------------------------------------

@app.get("/api/init")
async def api_init(request: Request):
    """Called on page load: returns config, model list, status, and history."""
    sid = _sid_from_request(request)
    global_cfg = _load_global_config()
    engine = _get_settings(sid).get("engine") or global_cfg.get("ai_engine", "ollama")
    saved_model = _get_settings(sid).get("ollama_model") or global_cfg.get("ollama_model", "")
    models = _get_ollama_models()

    if models:
        initial_model = saved_model if saved_model in models else models[0]
    else:
        initial_model = ""

    _set_settings(sid, engine=engine, ollama_model=initial_model)

    resp = JSONResponse({
        "engine": engine,
        "ollama_model": initial_model,
        "models": models,
        "ollama_status": _ollama_status(),
        "history": _get_history(sid, limit=50),
    })
    resp.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
    return resp


@app.get("/api/status")
async def api_status():
    return {"status": _ollama_status()}


@app.post("/api/ollama/start")
async def api_ollama_start(request: Request):
    sid = _sid_from_request(request)
    result = await _ollama_start()
    models = _get_ollama_models()
    if models:
        cur = _get_settings(sid).get("ollama_model")
        if cur not in models:
            _set_settings(sid, ollama_model=models[0])
    resp = JSONResponse({"message": result, "status": _ollama_status(), "models": models})
    resp.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
    return resp


@app.post("/api/ollama/stop")
async def api_ollama_stop(request: Request):
    sid = _sid_from_request(request)
    result = await _ollama_stop()
    resp = JSONResponse({"message": result, "status": _ollama_status()})
    resp.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
    return resp


@app.post("/api/settings")
async def api_settings(request: Request):
    sid = _sid_from_request(request)
    body = await request.json()
    engine = body.get("engine")
    model = body.get("ollama_model")
    if engine:
        _set_settings(sid, engine=engine)
    if model:
        _set_settings(sid, ollama_model=model)
    resp = JSONResponse({"ok": True, "settings": _get_settings(sid)})
    resp.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
    return resp


@app.post("/api/clear")
async def api_clear(request: Request):
    sid = _sid_from_request(request)
    _clear_history(sid)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
    return resp


@app.post("/api/chat")
async def api_chat(
    request: Request,
    message: str = Form(""),
    file: Optional[UploadFile] = File(None),
):
    sid = _sid_from_request(request)
    raw = (message or "").strip()
    cmd = raw.lower()

    async def _text_event(gen_text: str):
        yield f"data: {json.dumps({'token': gen_text})}\n\n"
        yield f"data: {json.dumps({'done': True})}\n\n"

    def _sse(headers_sid: str):
        h = {"Cache-Control": "no-cache"}
        return h

    # ── Built-in commands ──────────────────────────────────────────────
    if cmd in ("clear", "/clear", "clear history"):
        _clear_history(sid)
        r = StreamingResponse(_text_event("🗑️ Chat history cleared."), media_type="text/event-stream")
        r.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
        return r

    if cmd in ("history", "/history"):
        hist = _get_history(sid, limit=20)
        if not hist:
            text = "No history yet."
        else:
            lines = []
            for m in hist:
                role = "You" if m["role"] == "user" else "AI"
                short = m["content"][:120].replace("\n", " ")
                lines.append(f"**{role}**: {short}{'…' if len(m['content']) > 120 else ''}")
            text = "### 📜 Recent history\n\n" + "\n\n".join(lines)
        r = StreamingResponse(_text_event(text), media_type="text/event-stream")
        r.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
        return r

    if cmd in ("help", "/help"):
        text = (
            "### Commands\n"
            "- `clear` — clear chat history\n"
            "- `history` — show recent messages\n"
            "- `ollama on` — start ollama serve\n"
            "- `ollama off` — stop ollama serve\n"
            "- `ollama status` — show ollama status\n"
            "- `help` — show this help\n"
        )
        r = StreamingResponse(_text_event(text), media_type="text/event-stream")
        r.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
        return r

    if cmd in ("ollama on", "ollama start"):
        result = await _ollama_start()
        r = StreamingResponse(_text_event(result), media_type="text/event-stream")
        r.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
        return r

    if cmd in ("ollama off", "ollama stop"):
        result = await _ollama_stop()
        r = StreamingResponse(_text_event(result), media_type="text/event-stream")
        r.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
        return r

    if cmd in ("ollama status", "ollama"):
        r = StreamingResponse(_text_event(_ollama_status()), media_type="text/event-stream")
        r.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
        return r

    # ── File upload ──────────────────────────────────────────────────
    file_context = ""
    images: list[str] = []
    if file is not None:
        try:
            content_bytes = await file.read()
            from core.attachment_handler import process_attachment_bytes
            result = process_attachment_bytes(file.filename, content_bytes)
            if result.has_images():
                images = result.images
                file_context = ""  # image goes via `images`, not text injection
            elif result.has_text():
                fname, content = result.text_blocks[0]
                file_context = f"[File: {fname}]\n{content[:8000]}"
            else:
                file_context = result.summary
        except Exception as e:
            file_context = f"[Could not read {file.filename}: {e}]"

    saved = raw
    if file is not None:
        saved += f"\n[attached: {file.filename}]"
    _save_msg(sid, "user", saved)

    async def _gen():
        full_reply = []
        async for token in _stream_response(sid, raw, file_context, images):
            full_reply.append(token)
            yield f"data: {json.dumps({'token': token})}\n\n"
        _save_msg(sid, "assistant", "".join(full_reply))
        yield f"data: {json.dumps({'done': True})}\n\n"

    r = StreamingResponse(_gen(), media_type="text/event-stream", headers=_sse(sid))
    r.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
    return r


# ---------------------------------------------------------------------------
# Static frontend (plain HTML/JS/CSS) — serve last so /api/* takes priority
# ---------------------------------------------------------------------------
if os.path.isdir(WEBUI_DIR):
    app.mount("/", StaticFiles(directory=WEBUI_DIR, html=True), name="webui")
else:
    @app.get("/")
    async def _no_ui():
        return JSONResponse({"error": f"webui directory not found: {WEBUI_DIR}"}, status_code=500)
