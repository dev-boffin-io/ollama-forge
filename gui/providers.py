#!/usr/bin/env python3
"""
providers.py — multi-provider catalog + REST clients for the GUI.

This mirrors dev-assist/core/providers.py *by design*: the two apps deploy
independently (the GUI is a standalone bundled binary), so each keeps its
own copy of the catalogue and adapter logic.  Keep the two in sync when
adding or changing a provider.

Supports: Ollama (local), OpenAI, Anthropic, Groq, OpenRouter, Mistral,
Azure OpenAI, and any custom OpenAI-compatible endpoint.

Keys are NEVER required to select a provider or list models — the client
falls back to static model lists and only needs a key when actually
streaming.  Keys are read from the provider's env var (GROQ_API_KEY, ...)
or passed explicitly from the GUI's key field.
"""
import json
import os
import time

import requests

from ollama_client import OllamaClient


# Environment-variable fallback for OpenAI-compatible endpoints (GUI's own
# copy — dev-assist keeps its own; the two never share configuration files).
_OPENAI_BASE_OVERRIDE_ENV = "DEV_ASSIST_OPENAI_BASE_URL"  # explicit CLI-style override
_OPENAI_BASE_STANDARD_ENV = "OPENAI_BASE_URL"            # standard fallback
_OPENAI_KEY_OVERRIDE_ENV  = "DEV_ASSIST_OPENAI_API_KEY"  # explicit CLI-style override
# Providers that honour the OpenAI_* envs: the literal "openai" provider and
# the arbitrary "custom" OpenAI-compatible endpoint. Others (groq, …) use
# their own catalog base_url / env_var.
_OPENAI_ENV_PROVIDERS = ("openai", "custom")


# Catalog — display order.  MUST stay in sync with dev-assist/core/providers.py.
PROVIDER_ORDER = [
    "ollama", "openai", "anthropic", "groq",
    "openrouter", "mistral", "azure", "custom",
]

PROVIDERS = {
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

# Name-keyword vision hints for remote providers without live capability data.
_OPENAI_VISION_KEYWORDS = (
    "vision", "vl", "llava", "gpt-4o", "gpt-4.1", "o1", "o3", "qwen2-vl",
    "claude", "gemini", "llama-4", "scout", "maverick",
)

_ANTHROPIC_VISION = ("claude",)  # all Claude models are multimodal


def env_key(provider_id: str, explicit: str = "") -> str:
    """Provider key: explicit field wins, else env (override, then standard
    provider env var)."""
    if explicit.strip():
        return explicit.strip()
    if provider_id in _OPENAI_ENV_PROVIDERS:
        override = os.environ.get(_OPENAI_KEY_OVERRIDE_ENV, "").strip()
        if override:
            return override
    env_var = PROVIDERS.get(provider_id, {}).get("env_var", "")
    if env_var and os.environ.get(env_var):
        return os.environ[env_var]
    return ""


def base_url_for(provider_id: str, explicit: str = "",
                 profile: dict | None = None) -> str:
    """Resolve a provider's base URL.

    Priority: explicit (GUI field, persisted) → env override (`DEV_ASSIST_`
    then standard `OPENAI_`, openai/custom only) → profile → catalog default.
    """
    if explicit.strip():
        return explicit.strip().rstrip("/")
    if provider_id in _OPENAI_ENV_PROVIDERS:
        override = os.environ.get(_OPENAI_BASE_OVERRIDE_ENV, "").strip()
        if override:
            return override.rstrip("/")
        standard = os.environ.get(_OPENAI_BASE_STANDARD_ENV, "").strip()
        if standard:
            return standard.rstrip("/")
    prof = profile or PROVIDERS.get(provider_id, {})
    return (prof.get("base_url") or "").rstrip("/")


# ── Base contract for every client returned by get_client() ─────────────────
# name / label / kind / needs_key attributes
# list_models()   -> list[dict]     {"name": str, "vision": bool, ...}
# validate_key()  -> (bool, str)    (ok, error message)
# chat_stream(model, messages, temperature=0.7) -> iterator of text tokens

class _RemoteClient:
    """Shared plumbing for HTTP providers (OpenAI-compatible + Anthropic)."""
    kind = "remote"
    vision_all = False

    def __init__(self, profile: dict, api_key: str = ""):
        self.profile = profile
        self.name = ""
        self.label = profile["label"]
        self.needs_key = profile["needs_key"]
        self.api_key = api_key
        self.base_url = (profile.get("base_url") or "").rstrip("/")

    @property
    def vision_keywords(self):
        return _OPENAI_VISION_KEYWORDS

    def _model_has_vision(self, model_id: str) -> bool:
        if self.vision_all:
            return True
        lo = model_id.lower()
        return any(k in lo for k in self.vision_keywords)

    def _static_models(self) -> list[dict]:
        return [{"name": m, "vision": self._model_has_vision(m)}
                for m in self.profile.get("models") or []]

    def _check_key(self):
        if self.needs_key and not self.api_key.strip():
            env_var = self.profile.get("env_var", "")
            hint = f"Set the {env_var} environment variable." if env_var else (
                "Paste your API key in the key field."
            )
            raise ValueError(f"No API key set for {self.label}. {hint}")


class OpenAICompatClient(_RemoteClient):
    kind = "openai_compat"

    def __init__(self, provider_id: str, profile: dict, api_key: str = "",
                 base_url: str = ""):
        super().__init__(profile, api_key)
        self.name = provider_id
        if base_url.strip():
            self.base_url = base_url.strip().rstrip("/")
        self._azure = bool(profile.get("azure"))

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self._azure:
            if self.api_key:
                h["api-key"] = self.api_key
        elif self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _models_url(self) -> str:
        base = self.base_url
        if self._azure:
            if base.endswith("/openai"):
                base = base[: -len("/openai")]
            return f"{base}/openai/models?api-version={self.profile.get('api_version', '2024-06-01')}"
        return f"{base}/models"

    def _chat_url(self, model: str) -> str:
        base = self.base_url
        if self._azure:
            if base.endswith("/openai"):
                base = base[: -len("/openai")]
            return (f"{base}/openai/deployments/{model}/chat/completions"
                    f"?api-version={self.profile.get('api_version', '2024-06-01')}")
        return f"{base}/chat/completions"

    def list_models(self) -> list[dict]:
        if self.needs_key and not self.api_key.strip():
            return self._static_models()
        try:
            r = requests.get(self._models_url(), headers=self._headers(), timeout=8)
            if r.status_code == 200:
                models = [{"name": m["id"], "vision": self._model_has_vision(m["id"])}
                          for m in r.json().get("data", []) if isinstance(m, dict)]
                if models:
                    return models
        except Exception:
            pass
        return self._static_models()

    def validate_key(self) -> tuple[bool, str]:
        if self.needs_key and not self.api_key.strip():
            return False, "API key is empty."
        try:
            r = requests.get(self._models_url(), headers=self._headers(), timeout=8)
            if r.status_code == 200:
                return True, ""
            if r.status_code == 401:
                return False, f"Invalid API key (401) for {self.label}."
            return False, f"{self.label} returned HTTP {r.status_code}."
        except requests.exceptions.ConnectionError:
            return False, f"Cannot reach {self.base_url}."
        except requests.exceptions.Timeout:
            return False, "Request timed out."
        except Exception as e:
            return False, str(e)

    def chat_stream(self, model: str, messages: list[dict],
                    temperature: float = 0.7):
        self._check_key()
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }
        try:
            with requests.post(
                self._chat_url(model), headers=self._headers(), json=payload,
                stream=True, timeout=120,
            ) as resp:
                if resp.status_code == 401:
                    raise ValueError(f"Invalid API key (401) for {self.label}.")
                if resp.status_code == 429:
                    raise RuntimeError(f"{self.label} rate limit reached. Try again shortly.")
                if resp.status_code == 400:
                    try:
                        err = resp.json()
                        raise ValueError(
                            f"{self.label} error: {err.get('error', {}).get('message', resp.text)}"
                        )
                    except Exception:
                        raise ValueError(f"{self.label} bad request: {resp.text[:200]}")
                resp.raise_for_status()

                for line in resp.iter_lines():
                    if not line:
                        continue
                    decoded = line.decode("utf-8")
                    if decoded.startswith("data: "):
                        decoded = decoded[6:]
                    if decoded.strip() == "[DONE]":
                        return
                    try:
                        data = json.loads(decoded)
                        delta = data["choices"][0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
        except requests.exceptions.ConnectionError:
            raise ConnectionError(f"Cannot reach {self.label} — check your internet.")


class AnthropicClient(_RemoteClient):
    kind = "anthropic"
    vision_all = True
    _VERSION = "2023-06-01"
    _MAX_TOKENS = 8192

    def __init__(self, provider_id: str, profile: dict, api_key: str = ""):
        super().__init__(profile, api_key)
        self.name = provider_id

    @property
    def vision_keywords(self):
        return _ANTHROPIC_VISION

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self._VERSION,
        }

    def _convert_messages(self, messages: list[dict]) -> tuple[str, list[dict]]:
        """Normalized (OpenAI-shaped) messages -> Anthropic messages."""
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
                blocks: list[dict] = []
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "image_url":
                            url = part.get("image_url") or {}
                            url = url.get("url") or ""
                            if "base64," in url:
                                media, _, data = url.partition(";base64,")
                                media = media.replace("data:", "")
                                blocks.append({"type": "image",
                                               "source": {"type": "base64",
                                                          "media_type": media or "image/jpeg",
                                                          "data": data}})
                        elif isinstance(part, dict) and part.get("type") == "text":
                            txt = str(part.get("text") or "")
                            if txt:
                                blocks.append({"type": "text", "text": txt})
                else:
                    blocks.append({"type": "text",
                                   "text": content if isinstance(content, str) else str(content)})
                images = m.get("images") or []
                for b64 in images:
                    blocks.append({"type": "image",
                                   "source": {"type": "base64",
                                              "media_type": "image/jpeg", "data": b64}})
                if blocks:
                    out.append({"role": "user", "content": blocks})
                continue
            if role == "assistant":
                text = content if isinstance(content, str) else ""
                blocks = [{"type": "text", "text": text}] if text else []
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    args = fn.get("arguments") or {}
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {}
                    blocks.append({"type": "tool_use", "id": tc.get("id") or "",
                                   "name": fn.get("name", ""),
                                   "input": args if isinstance(args, dict) else {}})
                if blocks:
                    out.append({"role": "assistant", "content": blocks})
                continue
            if role == "tool":
                out.append({"role": "user", "content": [{
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id") or "",
                    "content": content if isinstance(content, str) else str(content),
                }]})
                continue
        return "\n\n".join(p for p in system_parts if p), out

    def list_models(self) -> list[dict]:
        if not self.api_key.strip() or self.base_url != "https://api.anthropic.com":
            return self._static_models()
        try:
            r = requests.get(f"{self.base_url}/v1/models",
                             headers=self._headers(), timeout=8)
            if r.status_code == 200:
                models = [{"name": m["id"], "vision": True}
                          for m in r.json().get("data", []) if isinstance(m, dict)]
                if models:
                    return models
        except Exception:
            pass
        return self._static_models()

    def validate_key(self) -> tuple[bool, str]:
        if not self.api_key.strip():
            return False, "API key is empty."
        try:
            r = requests.get(f"{self.base_url}/v1/models",
                             headers=self._headers(), timeout=8)
            if r.status_code == 200:
                return True, ""
            if r.status_code == 401:
                return False, "Invalid Anthropic API key (401)."
            return False, f"Anthropic returned HTTP {r.status_code}."
        except requests.exceptions.ConnectionError:
            return False, "Cannot reach api.anthropic.com — check your internet."
        except requests.exceptions.Timeout:
            return False, "Request timed out."
        except Exception as e:
            return False, str(e)

    def chat_stream(self, model: str, messages: list[dict],
                    temperature: float = 0.7):
        self._check_key()
        system, msgs = self._convert_messages(messages)
        payload = {
            "model": model,
            "max_tokens": self._MAX_TOKENS,
            "system": system,
            "messages": msgs,
            "stream": True,
            "temperature": temperature,
        }
        try:
            with requests.post(
                f"{self.base_url}/v1/messages", headers=self._headers(),
                json=payload, stream=True, timeout=120,
            ) as resp:
                if resp.status_code == 401:
                    raise ValueError("Invalid Anthropic API key (401).")
                if resp.status_code == 429:
                    raise RuntimeError("Anthropic rate limit reached. Try again shortly.")
                if resp.status_code != 200:
                    raise ValueError(f"Anthropic error {resp.status_code}: {resp.text[:200]}")
                for line in resp.iter_lines():
                    if not line:
                        continue
                    decoded = line.decode("utf-8")
                    if decoded.startswith("event:"):
                        continue
                    if decoded.startswith("data: "):
                        decoded = decoded[6:]
                    try:
                        data = json.loads(decoded)
                    except json.JSONDecodeError:
                        continue
                    if data.get("type") == "content_block_delta":
                        delta = data.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            content = delta.get("text")
                            if content:
                                yield content
                    elif data.get("type") == "error":
                        raise ValueError(f"Anthropic error: {data.get('error')}")
        except requests.exceptions.ConnectionError:
            raise ConnectionError("Cannot reach Anthropic API — check your internet.")


# ── Ollama (reuses gui/ollama_client.py) ─────────────────────────────────────

class _OllamaAdapter:
    kind = "ollama"
    needs_key = False
    vision_all = False
    vision_keywords = (
        "llava", "vision", "bakllava", "moondream", "phi3-v", "minicpm-v",
    )

    def __init__(self, provider_id: str, profile: dict, api_key: str = "",
                 host: str = ""):
        self.name = provider_id
        self.label = profile["label"]
        self._client = OllamaClient(host=host)

    def list_models(self) -> list[dict]:
        return self._client.list_models()

    def validate_key(self) -> tuple[bool, str]:
        if self._client.is_running():
            return True, ""
        return False, "Ollama is not running."

    def chat_stream(self, model: str, messages: list[dict],
                    temperature: float = 0.7):
        yield from self._client.chat_stream(model, messages, temperature=temperature)


def get_client(provider_id: str, api_key: str = "", host: str = "",
               base_url: str = "") -> object:
    """Build the client for a provider id, resolving the key from env too."""
    profile = PROVIDERS.get(provider_id, PROVIDERS["custom"])
    key = env_key(provider_id, api_key)
    if provider_id == "ollama":
        return _OllamaAdapter(provider_id, profile, key, host=host)
    if profile.get("kind") == "anthropic":
        return AnthropicClient(provider_id, profile, key)
    url = base_url_for(provider_id, explicit=base_url, profile=profile)
    return OpenAICompatClient(provider_id, profile, key, base_url=url)