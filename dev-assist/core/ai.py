"""
AI Engine — provider-agnostic front-end over core.providers.

Improvements:
- Uses core.config for validated settings
- Any provider from core.providers.PROVIDERS (Ollama, OpenAI, Anthropic,
  Groq, OpenRouter, Mistral, Azure, custom OpenAI-compatible)
- API keys are never required up front — resolved lazily from env vars
  at request time (GROQ_API_KEY, OPENAI_API_KEY, ANTHROPIC_API_KEY, ...)
- Session-aware multi-turn chat for the CLI
- Captures output for session history integration
"""

from __future__ import annotations

import os
from typing import AsyncGenerator

from core.providers import (
    ProviderError,
    active_provider as _active_provider,
    make_provider,
    provider_key as _provider_key,
    provider_profile as _provider_profile,
)


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
    if isinstance(cfg, dict):
        from core import providers as _providers
        pid = _providers.active_provider(cfg)
        prof = _providers.provider_profile(cfg, pid)
        return f"{pid}/{prof.get('default_model', '')}"
    return "ollama/qwen2.5-coder:7b"


def _get_engine(cfg) -> str:
    """The active provider id (legacy 'ollama'/'api' map onto the catalog)."""
    return _active_provider(cfg)


def _get_ollama_model(cfg) -> str:
    """Legacy helper — ollama profile's default model."""
    return _provider_profile(cfg, "ollama").get("default_model", "qwen2.5-coder:7b")


def _get_api_key(cfg) -> str:
    """Legacy helper — the active provider's resolved key."""
    return _provider_key(cfg)


def _get_api_url(cfg) -> str:
    """Legacy helper — the active provider's base URL."""
    return _provider_profile(cfg).get("base_url", "") or ""


def _get_api_model(cfg) -> str:
    """Legacy helper — the active provider's default model."""
    return _provider_profile(cfg).get("default_model", "") or ""


def get_provider(cfg=None, provider: str | None = None, api_key: str = ""):
    """Build the active (or a named) provider adapter, resolving its key."""
    cfg = cfg if cfg is not None else _load_config()
    from core import providers as _providers
    pid = provider or _providers.active_provider(cfg)
    profile = _providers.provider_profile(cfg, pid)
    key = _providers.provider_key(cfg, pid, explicit=api_key)
    return make_provider(pid, profile, key)


# ── Sync CLI ───────────────────────────────────────────────────────────────

def _session_messages(prompt: str) -> list[dict]:
    """Messages for a prompt, injecting session history if present."""
    try:
        from core.session import get_session
        history = get_session().to_ollama_messages()
        if (history and history[-1].get("role") == "user"
                and history[-1].get("content") == prompt):
            history = history[:-1]
        if history:
            return history + [{"role": "user", "content": prompt}]
    except Exception:
        pass
    return [{"role": "user", "content": prompt}]


def ask_ai(prompt: str, capture_output: bool = False) -> str | None:
    """
    Ask AI and stream to stdout.
    If capture_output=True, also returns the full response string.
    """
    cfg = _load_config()
    try:
        provider = get_provider(cfg)
    except Exception as exc:
        print(f"⚠️  Provider error: {exc}")
        return None

    model = provider.resolve_model()
    print(f"🤖 [{provider.provider_id}/{model}] thinking...\n")
    collected: list[str] = []
    try:
        for token in provider.stream(_session_messages(prompt), model=model):
            print(token, end="", flush=True)
            if capture_output:
                collected.append(token)
        print("\n")
        return "".join(collected) if capture_output else None
    except ProviderError as exc:
        print(f"\n⚠️  {exc}")
    except Exception as exc:
        print(f"\n⚠️  {provider.label} error: {exc}")
        if provider.kind == "ollama":
            print("   Make sure Ollama is running: ollama serve")
    return None


# ── Async streaming (Web UI) ───────────────────────────────────────────────

async def ask_ai_streaming(prompt: str) -> AsyncGenerator[str, None]:
    cfg = _load_config()
    try:
        provider = get_provider(cfg)
    except Exception as exc:
        yield f"⚠️ Provider error: {exc}"
        return
    model = provider.resolve_model()
    try:
        for token in provider.stream(_session_messages(prompt), model=model):
            yield token
    except ProviderError as exc:
        yield f"⚠️ {exc}"
    except Exception as exc:
        yield f"⚠️ {provider.label} error: {exc}"