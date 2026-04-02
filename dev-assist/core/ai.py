"""
AI Engine — Ollama (local) with optional API fallback.

Improvements:
- Uses core.config for validated settings
- API key loaded from env var (DEV_ASSIST_API_KEY)
- Session-aware multi-turn for Ollama chat
- Captures output for session history integration
"""

from __future__ import annotations

import os
from typing import AsyncGenerator


# ── Config helpers ─────────────────────────────────────────────────────────

def _load_config():
    try:
        from core.config import load_config
        return load_config()
    except Exception:
        import json
        p = os.path.join(os.path.dirname(__file__), "..", "config", "settings.json")
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            return {}


def save_config(config) -> None:
    try:
        from core.config import save_config as _save
        _save(config)
    except Exception:
        import json
        p = os.path.join(os.path.dirname(__file__), "..", "config", "settings.json")
        with open(p, "w") as f:
            json.dump(config if isinstance(config, dict) else {}, f, indent=2)


def get_current_model() -> str:
    cfg = _load_config()
    if hasattr(cfg, "get_current_model"):
        return cfg.get_current_model()
    engine = cfg.get("ai_engine", "ollama")
    if engine == "ollama":
        return f"ollama/{cfg.get('ollama_model', 'qwen2.5-coder:7b')}"
    return f"api/{cfg.get('api_engine', {}).get('api_model', 'llama3-70b-8192')}"


def _get_engine(cfg) -> str:
    if hasattr(cfg, "ai_engine"):
        return cfg.ai_engine
    return cfg.get("ai_engine", "ollama")


def _get_ollama_model(cfg) -> str:
    if hasattr(cfg, "ollama_model"):
        return cfg.ollama_model
    return cfg.get("ollama_model", "qwen2.5-coder:7b")


def _get_api_key(cfg) -> str:
    # Env var takes priority
    env_key = os.environ.get("DEV_ASSIST_API_KEY", "")
    if env_key:
        return env_key
    if hasattr(cfg, "get_active_api_key"):
        return cfg.get_active_api_key()
    return cfg.get("api_engine", {}).get("api_key", "")


def _get_api_url(cfg) -> str:
    if hasattr(cfg, "api_engine"):
        return cfg.api_engine.api_url
    return cfg.get("api_engine", {}).get(
        "api_url", "https://api.groq.com/openai/v1/chat/completions"
    )


def _get_api_model(cfg) -> str:
    if hasattr(cfg, "api_engine"):
        return cfg.api_engine.api_model
    return cfg.get("api_engine", {}).get("api_model", "llama3-70b-8192")


# ── Sync CLI ───────────────────────────────────────────────────────────────

def ask_ai(prompt: str, capture_output: bool = False) -> str | None:
    """
    Ask AI and stream to stdout.
    If capture_output=True, also returns the full response string.
    """
    cfg = _load_config()
    engine = _get_engine(cfg)

    if engine == "ollama":
        return _ask_ollama(prompt, cfg, capture_output=capture_output)
    elif engine == "api":
        return _ask_api(prompt, cfg, capture_output=capture_output)
    else:
        print(f"⚠️  Unknown AI engine: {engine}")
        return None


def _ask_ollama(prompt: str, cfg, *, capture_output: bool = False) -> str | None:
    try:
        import ollama
        model = _get_ollama_model(cfg)

        # Try multi-turn chat if session history exists
        try:
            from core.session import get_session
            history = get_session().to_ollama_messages()
            if history:
                # Use chat API for multi-turn
                messages = history + [{"role": "user", "content": prompt}]
                print(f"🤖 [{model}] thinking...\n")
                stream = ollama.chat(model=model, messages=messages, stream=True)
                collected = []
                for chunk in stream:
                    token = chunk.get("message", {}).get("content", "")
                    print(token, end="", flush=True)
                    if capture_output:
                        collected.append(token)
                print("\n")
                return "".join(collected) if capture_output else None
        except Exception:
            pass

        # Single-turn fallback
        print(f"🤖 [{model}] thinking...\n")
        stream = ollama.generate(model=model, prompt=prompt, stream=True)
        collected = []
        for chunk in stream:
            token = chunk.get("response", "")
            print(token, end="", flush=True)
            if capture_output:
                collected.append(token)
        print("\n")
        return "".join(collected) if capture_output else None

    except ImportError:
        print("⚠️  ollama not installed. Run: pip install ollama")
        print("   Then: ollama pull qwen2.5-coder:7b && ollama serve")
    except Exception as exc:
        print(f"⚠️  Ollama error: {exc}")
        print("   Make sure Ollama is running: ollama serve")
    return None


def _ask_api(prompt: str, cfg, *, capture_output: bool = False) -> str | None:
    try:
        import urllib.request
        import json as _json

        api_key = _get_api_key(cfg)
        api_url = _get_api_url(cfg)
        model   = _get_api_model(cfg)

        if not api_key:
            print("⚠️  API key not set.")
            print("   Set env var: export DEV_ASSIST_API_KEY='your-key'")
            print("   Or: config/settings.json → api_engine.api_key")
            return None

        payload = _json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }).encode()

        req = urllib.request.Request(
            api_url, data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read())
            reply = data["choices"][0]["message"]["content"]
            print(f"\n🤖 [{model}]\n{reply}\n")
            return reply if capture_output else None

    except Exception as exc:
        print(f"⚠️  API error: {exc}")
    return None


# ── Async streaming (Web UI) ───────────────────────────────────────────────

async def ask_ai_streaming(prompt: str) -> AsyncGenerator[str, None]:
    cfg = _load_config()
    engine = _get_engine(cfg)

    if engine == "ollama":
        async for token in _stream_ollama(prompt, cfg):
            yield token
    elif engine == "api":
        async for token in _stream_api(prompt, cfg):
            yield token
    else:
        yield f"⚠️ Unknown engine: {engine}"


async def _stream_ollama(prompt: str, cfg) -> AsyncGenerator[str, None]:
    try:
        import ollama
        import asyncio
        model = _get_ollama_model(cfg)

        def _gen():
            return list(ollama.generate(model=model, prompt=prompt, stream=True))

        loop = asyncio.get_running_loop()
        chunks = await loop.run_in_executor(None, _gen)
        for chunk in chunks:
            token = chunk.get("response", "")
            if token:
                yield token

    except ImportError:
        yield "⚠️ ollama not installed. Run: `pip install ollama`"
    except Exception as exc:
        yield f"⚠️ Ollama error: {exc}\nMake sure Ollama is running: `ollama serve`"


async def _stream_api(prompt: str, cfg) -> AsyncGenerator[str, None]:
    try:
        import urllib.request
        import json as _json

        api_key = _get_api_key(cfg)
        api_url = _get_api_url(cfg)
        model   = _get_api_model(cfg)

        if not api_key:
            yield "⚠️ Set `DEV_ASSIST_API_KEY` env var or `api_key` in settings.json"
            return

        payload = _json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }).encode()

        req = urllib.request.Request(
            api_url, data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )

        def _call():
            with urllib.request.urlopen(req, timeout=30) as resp:
                return _json.loads(resp.read())

        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, _call)
        yield data["choices"][0]["message"]["content"]

    except Exception as exc:
        yield f"⚠️ API error: {exc}"
