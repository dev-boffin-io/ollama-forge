"""
Providers — multi-model/AI-provider catalog and transport adapters.

This mirrors (by design) the catalogue used by the Ollama GUI
(gui/providers.py).  Both apps keep their own copy: the GUI ships as a
standalone bundled binary, dev-assist as a CLI package, so a shared module
would couple two independent deploys.  The two copies must stay in sync —
a provider added here should be added there, and vice-versa.

Every provider speaks one of three transport dialects:

  - "ollama"            local Ollama server (python `ollama` client)
  - "openai_compat"     OpenAI-style /chat/completions REST (OpenAI, Groq,
                        OpenRouter, Mistral, Azure OpenAI, and arbitrary
                        OpenAI-compatible local servers)
  - "anthropic"         Anthropic Messages API (own header + response shape)

All adapters expose the same surface:

  list_models()  -> list[str]        (live list, static fallback list)
  validate()     -> (bool, str)      (reachable? error message)
  chat(msg, model=..., tools=...) -> normalized dict
  stream(msg, model=...)          -> iterator of text tokens

A "normalized" chat result has the OpenAI message shape:

  {"role": "assistant", "content": str, "tool_calls": [{"id", "type",
    "function": {"name", "arguments"}}]}

API keys are NEVER required to configure, switch, or list models — they
are resolved lazily from environment variables at request time, so a
provider only needs a key when it is actually asked to generate.
"""

from __future__ import annotations

import json
import os
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Iterator

# PyInstaller-safe import guard: `ollama` may be absent; it is imported
# lazily inside the Ollama adapter only.
try:  # pragma: no cover - only used at runtime, not in the test venv
    import ollama as _ollama  # noqa: F401
except Exception:  # pragma: no cover
    _ollama = None

REQUEST_TIMEOUT = 120     # seconds for a full (non-stream) completion
STREAM_TIMEOUT = 600      # seconds for a streamed completion
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MAX_TOKENS = 8192

# Environment-variable support for OpenAI-compatible endpoints (apps' own env:
# nothing shared with the GUI — the GUI keeps its own copy of this catalog).
_OPENAI_BASE_OVERRIDE_ENV = "DEV_ASSIST_OPENAI_BASE_URL"  # CLI-specific, highest
_OPENAI_BASE_STANDARD_ENV = "OPENAI_BASE_URL"            # standard fallback
_OPENAI_KEY_OVERRIDE_ENV  = "DEV_ASSIST_OPENAI_API_KEY"  # CLI-specific, highest
_OPENAI_KEY_STANDARD_ENV  = "OPENAI_API_KEY"             # standard fallback
# Providers these apply to: the literal "openai" one plus the arbitrary
# "custom" OpenAI-compatible endpoint. Other providers (groq, openrouter, …)
# keep their own catalog base_url / env_var.
_OPENAI_ENV_PROVIDERS = ("openai", "custom")

# Catalog — display order.
PROVIDER_ORDER = [
    "ollama", "openai", "anthropic", "groq",
    "openrouter", "mistral", "azure", "custom",
]

# Catalog — the single source of defaults for both config persistence and
# runtime profiles.  Non-secret fields only: keys always come from env vars.
PROVIDERS: dict[str, dict] = {
    "ollama": {
        "label": "Ollama (Local)",
        "kind": "ollama",
        "needs_key": False,
        "env_var": "",
        "base_url": "http://localhost:11434",
        "default_model": "qwen2.5-coder:7b",
        "models": [
            "qwen2.5-coder:7b", "qwen2.5-coder:3b",
            "llama3.1:8b", "llama3.2:3b", "codellama:7b",
        ],
    },
    "openai": {
        "label": "OpenAI",
        "kind": "openai_compat",
        "needs_key": True,
        "env_var": "OPENAI_API_KEY",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "o3-mini"],
    },
    "anthropic": {
        "label": "Anthropic",
        "kind": "anthropic",
        "needs_key": True,
        "env_var": "ANTHROPIC_API_KEY",
        "base_url": "https://api.anthropic.com",
        "default_model": "claude-sonnet-4-5",
        "models": [
            "claude-opus-4-5", "claude-sonnet-4-5", "claude-haiku-4-5",
            "claude-3-7-sonnet-20250219", "claude-3-5-haiku-20241022",
        ],
    },
    "groq": {
        "label": "Groq",
        "kind": "openai_compat",
        "needs_key": True,
        "env_var": "GROQ_API_KEY",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
        "models": [
            "llama-3.3-70b-versatile", "llama-3.1-70b-versatile",
            "llama-3.1-8b-instant", "mixtral-8x7b-32768", "gemma2-9b-it",
        ],
    },
    "openrouter": {
        "label": "OpenRouter",
        "kind": "openai_compat",
        "needs_key": True,
        "env_var": "OPENROUTER_API_KEY",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "anthropic/claude-sonnet-4-5",
        "models": [
            "anthropic/claude-sonnet-4-5", "anthropic/claude-opus-4-5",
            "openai/gpt-4o", "openai/gpt-4o-mini",
            "google/gemini-2.0-flash-001", "meta-llama/llama-3.3-70b-instruct",
        ],
    },
    "mistral": {
        "label": "Mistral",
        "kind": "openai_compat",
        "needs_key": True,
        "env_var": "MISTRAL_API_KEY",
        "base_url": "https://api.mistral.ai/v1",
        "default_model": "mistral-large-latest",
        "models": [
            "mistral-large-latest", "mistral-small-latest",
            "codestral-latest", "open-mistral-nemo",
        ],
    },
    "azure": {
        "label": "Azure OpenAI",
        "kind": "openai_compat",
        "needs_key": True,
        "env_var": "AZURE_OPENAI_API_KEY",
        "base_url": "https://YOUR-RESOURCE.openai.azure.com",
        "api_version": "2024-06-01",
        "azure": True,
        "default_model": "gpt-4o",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-35-turbo", "gpt-4"],
    },
    "custom": {
        "label": "Custom (OpenAI-compatible)",
        "kind": "openai_compat",
        "needs_key": False,
        "env_var": "OPENAI_API_KEY",
        "base_url": "http://localhost:8000/v1",
        "default_model": "local-model",
        "models": [],
    },
}

# Fields that may be persisted to settings (never includes api_key).
_PROFILE_KEYS = (
    "label", "kind", "needs_key", "env_var", "base_url",
    "api_version", "azure", "default_model", "models",
)


def provider_defaults_raw() -> dict[str, dict]:
    """Catalog in a JSON-serialisable form for storing in settings.json."""
    return {
        pid: {k: p[k] for k in _PROFILE_KEYS if k in p}
        for pid, p in PROVIDERS.items()
    }


def is_provider(name: str) -> bool:
    return name in PROVIDERS


class ProviderError(Exception):
    """User-facing provider failure (missing key, unreachable host, ...)."""


# ── Config-shaped helpers (accept pydantic AppConfig OR plain dict) ─────────

def active_provider(cfg) -> str:
    """Resolve the configured provider id, mapping legacy engines."""
    pid = ""
    if hasattr(cfg, "active_provider"):
        pid = cfg.active_provider
    elif isinstance(cfg, dict):
        pid = cfg.get("active_provider", "")
    if pid in PROVIDERS:
        return pid
    engine = (
        cfg.ai_engine if hasattr(cfg, "ai_engine")
        else cfg.get("ai_engine", "ollama") if isinstance(cfg, dict) else "ollama"
    )
    return "ollama" if engine == "ollama" else "groq"


def provider_profile(cfg, provider: str | None = None) -> dict:
    """Per-provider profile as a plain dict, merged over catalog defaults."""
    pid = provider or active_provider(cfg)
    base = dict(PROVIDERS.get(pid, PROVIDERS["custom"]))
    raw: dict = {}
    if hasattr(cfg, "providers"):
        prof = cfg.providers.get(pid)
        if prof is not None:
            raw = prof.model_dump() if hasattr(prof, "model_dump") else dict(prof)
    elif isinstance(cfg, dict):
        raw = cfg.get("providers", {}).get(pid) or {}
    merged = dict(base)
    merged.update({k: v for k, v in raw.items() if v is not None})
    return merged


def provider_key(cfg, provider: str | None = None, explicit: str = "") -> str:
    """
    Resolve the API key for a provider.

    Priority: explicit → CLI override env for openai/custom →
    provider env var → stored key → legacy DEV_ASSIST_API_KEY fallback →
    legacy groq api_engine.api_key.
    """
    if explicit:
        return explicit
    pid = provider or active_provider(cfg)
    prof = provider_profile(cfg, pid)
    if pid in _OPENAI_ENV_PROVIDERS:
        override = os.environ.get(_OPENAI_KEY_OVERRIDE_ENV, "").strip()
        if override:
            return override
    env_var = prof.get("env_var", "")
    if env_var and os.environ.get(env_var):
        return os.environ[env_var]
    stored = prof.get("api_key") or ""
    if stored:
        return stored
    if os.environ.get("DEV_ASSIST_API_KEY"):
        return os.environ["DEV_ASSIST_API_KEY"]
    if pid == "groq":
        if hasattr(cfg, "api_engine"):
            return cfg.api_engine.api_key
        if isinstance(cfg, dict):
            return cfg.get("api_engine", {}).get("api_key", "")
    return ""


def _openai_env_base_url(pid: str, profile: dict) -> str:
    """Base URL from env for OpenAI-compatible endpoints (openai/custom only).

    CLI-specific override beats the standard OPENAI_BASE_URL. Empty when the
    provider isn't env configurable, so settings/catalog values win there."""
    if pid not in _OPENAI_ENV_PROVIDERS:
        return ""
    override = os.environ.get(_OPENAI_BASE_OVERRIDE_ENV, "").strip()
    if override:
        return override
    return os.environ.get(_OPENAI_BASE_STANDARD_ENV, "").strip()


def provider_base_url(cfg, provider: str | None = None) -> str:
    """
    Resolve the base URL for a provider (env override → profile/settings →
    catalog default). Mirrors provider_key() so env can preempt settings.
    """
    pid = provider or active_provider(cfg)
    prof = provider_profile(cfg, pid)
    env_base = _openai_env_base_url(pid, prof)
    return (env_base or prof.get("base_url", "") or "").rstrip("/")


# ── Base adapter ─────────────────────────────────────────────────────────────

class BaseProvider(ABC):
    kind: str = "openai_compat"

    def __init__(self, provider_id: str, profile: dict, api_key: str = ""):
        self.provider_id = provider_id
        self.profile = dict(profile)
        merged_defaults = dict(PROVIDERS.get(provider_id, {}))
        merged_defaults.update({k: v for k, v in profile.items() if v is not None})
        self.profile = merged_defaults
        self.label = self.profile.get("label", provider_id)
        self.default_model = self.profile.get("default_model", "") or ""
        self.base_url = (
            _openai_env_base_url(provider_id, self.profile)
            or (self.profile.get("base_url", "") or "")
        ).rstrip("/")
        self.api_key = api_key or self._env_key() or self.profile.get("api_key", "") or ""

    def _env_key(self) -> str:
        if self.provider_id in _OPENAI_ENV_PROVIDERS:
            override = os.environ.get(_OPENAI_KEY_OVERRIDE_ENV, "").strip()
            if override:
                return override
        env_var = self.profile.get("env_var", "")
        if env_var and os.environ.get(env_var):
            return os.environ[env_var]
        return ""

    @property
    def needs_key(self) -> bool:
        return bool(self.profile.get("needs_key"))

    def resolve_model(self, model: str | None = None) -> str:
        return model or self.default_model

    def _check_key(self) -> None:
        if self.needs_key and not self.api_key:
            env_var = self.profile.get("env_var", "")
            hint = f"Set the {env_var} environment variable." if env_var else "Set an API key."
            raise ProviderError(
                f"No API key set for {self.label}. {hint} "
                "The key is read from the environment at request time."
            )

    @abstractmethod
    def list_models(self) -> list[str]: ...

    @abstractmethod
    def validate(self) -> tuple[bool, str]: ...

    @abstractmethod
    def chat(self, messages: list[dict], *, model: str | None = None,
             tools: list[dict] | None = None) -> dict: ...

    @abstractmethod
    def stream(self, messages: list[dict], *, model: str | None = None) -> Iterator[str]: ...


# ── HTTP plumbing (urllib, no third-party deps) ─────────────────────────────

def _http_json(url: str, payload: dict | None = None,
               headers: dict | None = None, timeout: int = REQUEST_TIMEOUT) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    if payload is not None:
        req.method = "POST"
        req.data = json.dumps(payload).encode()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _sse_events(url: str, payload: dict, headers: dict) -> Iterator[str]:
    """Yield text tokens from an SSE response, handling both OpenAI and
    Anthropic frame shapes so the producers stay tiny."""
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=STREAM_TIMEOUT) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if not data or data == "[DONE]":
                return
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                return
            token = _sse_token(event)
            if token:
                yield token


def _sse_token(event: dict) -> str:
    """Pull one token out of an SSE JSON event (OpenAI or Anthropic shape)."""
    # OpenAI / chat.completion.chunk
    if "choices" in event:
        delta = (event.get("choices") or [{}])[0].get("delta") or {}
        return delta.get("content") or ""
    # Anthropic content_block_delta (text_delta)
    if event.get("type") == "content_block_delta":
        d = event.get("delta") or {}
        if d.get("type") == "text_delta":
            return d.get("text") or ""
    if event.get("type") == "error":
        err = event.get("error")
        raise ProviderError(str(err) if err else "Anthropic stream error")
    return ""


# ── Ollama ──────────────────────────────────────────────────────────────────

class OllamaProvider(BaseProvider):
    kind = "ollama"

    def _make_client(self):
        try:
            import ollama
        except ImportError as exc:
            raise ProviderError("ollama not installed. Run: pip install ollama") from exc
        host = self.base_url or "http://localhost:11434"
        return ollama.Client(host=host)

    def list_models(self) -> list[str]:
        try:
            resp = self._make_client().list()
            models = []
            for m in (resp.models if not isinstance(resp, dict) else resp.get("models", [])):
                if isinstance(m, dict):
                    models.append(m.get("model") or m.get("name") or "")
                else:
                    models.append(getattr(m, "model", "") or getattr(m, "name", ""))
            models = sorted({m for m in models if m})
            if models:
                return models
        except Exception:
            pass
        return list(self.profile.get("models", []))

    def validate(self) -> tuple[bool, str]:
        try:
            self._make_client().list()
            return True, "Ollama is reachable"
        except Exception as exc:
            return False, f"Ollama not reachable: {exc}"

    def chat(self, messages, *, model=None, tools=None) -> dict:
        client = self._make_client()
        kwargs: dict = {"model": self.resolve_model(model), "messages": messages}
        if tools:
            kwargs["tools"] = tools
        resp = client.chat(**kwargs)
        msg = resp.get("message") if isinstance(resp, dict) else getattr(resp, "message", None)
        if isinstance(msg, dict):
            return msg
        return {"role": "assistant", "content": getattr(msg, "content", "") or "",
                "tool_calls": getattr(msg, "tool_calls", None) or []}

    def stream(self, messages, *, model=None) -> Iterator[str]:
        client = self._make_client()
        for chunk in client.chat(model=self.resolve_model(model), messages=messages, stream=True):
            if isinstance(chunk, dict):
                token = (chunk.get("message") or {}).get("content") or ""
            else:
                token = getattr(getattr(chunk, "message", None), "content", "") or ""
            if token:
                yield token


# ── OpenAI-compatible ───────────────────────────────────────────────────────

class OpenAICompatProvider(BaseProvider):
    kind = "openai_compat"

    @property
    def _is_azure(self) -> bool:
        return bool(self.profile.get("azure"))

    def _api_version(self) -> str:
        return self.profile.get("api_version") or "2024-06-01"

    def _chat_url(self, model: str) -> str:
        base = self.base_url
        if self._is_azure:
            if base.endswith("/openai"):
                base = base[: -len("/openai")]
            return (f"{base}/openai/deployments/{model}/chat/completions"
                    f"?api-version={self._api_version()}")
        return f"{base}/chat/completions"

    def _models_url(self) -> str:
        base = self.base_url
        if self._is_azure:
            if base.endswith("/openai"):
                base = base[: -len("/openai")]
            return f"{base}/openai/models?api-version={self._api_version()}"
        return f"{base}/models"

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self._is_azure:
            if self.api_key:
                headers["api-key"] = self.api_key
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def list_models(self) -> list[str]:
        if self.needs_key and not self.api_key:
            return list(self.profile.get("models", []))
        try:
            data = _http_json(self._models_url(), headers=self._headers(), timeout=30)
            names = [i.get("id") or "" for i in data.get("data", []) if isinstance(i, dict)]
            names = sorted({n for n in names if n})
            if names:
                return names
        except Exception:
            pass
        return list(self.profile.get("models", []))

    def validate(self) -> tuple[bool, str]:
        if self.needs_key and not self.api_key:
            return False, f"No API key set for {self.label}."
        try:
            live = self.list_models()
            return (True, "connected") if live else (False, "No models returned")
        except Exception as exc:
            return False, str(exc)

    def chat(self, messages, *, model=None, tools=None) -> dict:
        self._check_key()
        payload: dict = {"model": self.resolve_model(model),
                         "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
        data = _http_json(self._chat_url(payload["model"]), payload,
                          self._headers(), timeout=REQUEST_TIMEOUT)
        try:
            return data["choices"][0]["message"]
        except (KeyError, IndexError):
            raise ProviderError(f"{self.label} returned an unexpected response: {data}")

    def stream(self, messages, *, model=None) -> Iterator[str]:
        self._check_key()
        model = self.resolve_model(model)
        payload: dict = {"model": model, "messages": messages, "stream": True}
        yield from _sse_events(self._chat_url(model), payload, self._headers())


# ── Anthropic (Messages API) ────────────────────────────────────────────────

def _anthropic_convert_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Convert normalized (OpenAI-shaped) messages to Anthropic messages.

    Returns (system_text, anthropic_messages)."""
    system_parts: list[str] = []
    out: list[dict] = []
    for m in messages:
        role = m.get("role", "")
        content = m.get("content")
        if role == "system":
            if content:
                system_parts.append(content if isinstance(content, str) else str(content))
            continue
        if role == "user":
            blocks = _anthropic_user_blocks(m, content)
            if blocks:
                out.append({"role": "user", "content": blocks})
            continue
        if role == "assistant":
            blocks: list[dict] = []
            text = content if isinstance(content, str) else ""
            if text:
                blocks.append({"type": "text", "text": text})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id") or f"toolu_{len(blocks)}",
                    "name": fn.get("name", ""),
                    "input": args if isinstance(args, dict) else {},
                })
            if blocks:
                out.append({"role": "assistant", "content": blocks})
            continue
        if role == "tool":
            blocks = [{
                "type": "tool_result",
                "tool_use_id": m.get("tool_call_id") or "",
                "content": content if isinstance(content, str) else str(content),
            }]
            out.append({"role": "user", "content": blocks})
            continue
    return "\n\n".join(p for p in system_parts if p), out


def _anthropic_user_blocks(m: dict, content) -> list[dict]:
    """User message content → Anthropic blocks (text + images)."""
    blocks: list[dict] = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                block = _anthropic_image_block(part.get("image_url") or {})
                if block:
                    blocks.append(block)
            elif isinstance(part, dict) and part.get("type") == "text":
                txt = str(part.get("text") or "")
                if txt:
                    blocks.append({"type": "text", "text": txt})
        if blocks:
            return blocks
    images = m.get("images") or []
    if images:
        text = content if isinstance(content, str) else ""
        blocks = [{"type": "text", "text": text}] if text else []
        for b64 in images:
            block = _anthropic_image_block({"url": f"data:image/jpeg;base64,{b64}"})
            if block:
                blocks.append(block)
        return blocks
    return [{"type": "text", "text": content if isinstance(content, str) else str(content)}]


def _anthropic_image_block(image_url: dict) -> dict | None:
    url = (image_url or {}).get("url") or ""
    sep = url.find(";base64,")
    if sep == -1:
        return None
    media = url[5:url.find(";")]
    data = url[sep + len(";base64,"):]
    return {"type": "image", "source": {"type": "base64",
                                        "media_type": media or "image/jpeg", "data": data}}


def _anthropic_tools(tools: list[dict]) -> list[dict]:
    out = []
    for tool in tools:
        fn = tool.get("function") or tool if "function" not in tool else tool["function"]
        if not fn.get("name"):
            continue
        out.append({
            "name": fn["name"],
            "description": fn.get("description") or "",
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def _anthropic_response(data: dict) -> dict:
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for block in data.get("content") or []:
        if block.get("type") == "text":
            text_parts.append(block.get("text") or "")
        elif block.get("type") == "tool_use":
            tool_calls.append({
                "id": block.get("id") or "",
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input") or {}),
                },
            })
    stop = data.get("stop_reason")
    if stop == "tool_use":
        finish = "tool_calls"
    elif stop == "max_tokens":
        finish = "length"
    else:
        finish = "stop"
    return {"role": "assistant", "content": "".join(text_parts),
            "tool_calls": tool_calls, "finish_reason": finish}


class AnthropicProvider(BaseProvider):
    kind = "anthropic"

    def _url(self) -> str:
        return f"{self.base_url}/v1/messages"

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
        }

    def _body(self, messages, *, model=None, tools=None, stream: bool = False) -> dict:
        system, msgs = _anthropic_convert_messages(messages)
        body: dict = {
            "model": self.resolve_model(model),
            "max_tokens": ANTHROPIC_MAX_TOKENS,
            "system": system,
            "messages": msgs,
            "stream": stream,
        }
        if tools:
            body["tools"] = _anthropic_tools(tools)
        return body

    def list_models(self) -> list[str]:
        if not self.api_key or self.base_url != "https://api.anthropic.com":
            return list(self.profile.get("models", []))
        try:
            data = _http_json(f"{self.base_url}/v1/models", headers=self._headers(), timeout=30)
            names = [m.get("id") or "" for m in data.get("data", []) if isinstance(m, dict)]
            names = sorted({n for n in names if n})
            if names:
                return names
        except Exception:
            pass
        return list(self.profile.get("models", []))

    def validate(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, f"No API key set for {self.label}."
        try:
            data = _http_json(f"{self.base_url}/v1/models", headers=self._headers(), timeout=30)
            return (True, "connected") if data else (False, "empty response")
        except Exception as exc:
            return False, str(exc)

    def chat(self, messages, *, model=None, tools=None) -> dict:
        self._check_key()
        data = _http_json(self._url(), self._body(messages, model=model, tools=tools),
                          self._headers(), timeout=REQUEST_TIMEOUT)
        return _anthropic_response(data)

    def stream(self, messages, *, model=None) -> Iterator[str]:
        self._check_key()
        yield from _sse_events(self._url(), self._body(messages, model=model, stream=True),
                               self._headers())


# ── Factory ─────────────────────────────────────────────────────────────────

_ADAPTERS = {
    "ollama": OllamaProvider,
    "openai_compat": OpenAICompatProvider,
    "anthropic": AnthropicProvider,
}


def make_provider(provider_id: str, profile: dict | None = None,
                  api_key: str = "") -> BaseProvider:
    """Build the right adapter for a provider id + profile dict."""
    if not profile:
        profile = dict(PROVIDERS.get(provider_id, PROVIDERS["custom"]))
    kind = profile.get("kind") or PROVIDERS.get(provider_id, {}).get("kind", "openai_compat")
    cls = _ADAPTERS.get(kind, OpenAICompatProvider)
    return cls(provider_id, profile, api_key)