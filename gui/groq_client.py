#!/usr/bin/env python3
"""
Groq API client — streaming chat using Groq's OpenAI-compatible endpoint.
No extra dependencies beyond 'requests'.
"""
import json
import time
import requests

GROQ_CHAT_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "llama-3.1-8b-instant",
    "llama3-70b-8192",
    "llama3-8b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
    "deepseek-r1-distill-llama-70b",
    # Vision-capable models
    "meta-llama/llama-4-scout-17b-16e-instruct",
    "meta-llama/llama-4-maverick-17b-128e-instruct",
    "llama-3.2-11b-vision-preview",
    "llama-3.2-90b-vision-preview",
]

# Keywords used to detect vision capability from model name
_GROQ_VISION_KEYWORDS = (
    "vision", "llava", "llama-4", "llama4", "scout", "maverick"
)

GROQ_BASE = "https://api.groq.com/openai/v1"


class GroqClient:
    def __init__(self, api_key: str = "", timeout: int = 120):
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _model_has_vision(model_id: str) -> bool:
        lo = model_id.lower()
        return any(k in lo for k in _GROQ_VISION_KEYWORDS)

    def list_models(self) -> list[dict]:
        """
        Return available Groq chat models with vision flag.
        Tries live /models endpoint first; falls back to static list.
        Each dict: {"name": str, "vision": bool}
        """
        def _wrap(model_id: str) -> dict:
            return {"name": model_id, "vision": self._model_has_vision(model_id)}

        if not self.api_key:
            return [_wrap(m) for m in GROQ_CHAT_MODELS]
        try:
            r = requests.get(
                f"{GROQ_BASE}/models",
                headers=self._headers(),
                timeout=8,
            )
            if r.status_code == 200:
                data = r.json().get("data", [])
                chat = [
                    _wrap(m["id"])
                    for m in data
                    if "whisper" not in m["id"] and "tts" not in m["id"]
                ]
                return chat if chat else [_wrap(m) for m in GROQ_CHAT_MODELS]
        except Exception:
            pass
        return [_wrap(m) for m in GROQ_CHAT_MODELS]

    def validate_key(self) -> tuple[bool, str]:
        """Returns (ok, error_msg). Tries a minimal models request."""
        if not self.api_key.strip():
            return False, "API key is empty."
        try:
            r = requests.get(
                f"{GROQ_BASE}/models",
                headers=self._headers(),
                timeout=8,
            )
            if r.status_code == 200:
                return True, ""
            if r.status_code == 401:
                return False, "Invalid API key (401 Unauthorized)."
            return False, f"Groq returned HTTP {r.status_code}."
        except requests.exceptions.ConnectionError:
            return False, "Cannot reach api.groq.com — check your internet."
        except requests.exceptions.Timeout:
            return False, "Request timed out."
        except Exception as e:
            return False, str(e)

    def chat_stream(self, model: str, messages: list[dict],
                    temperature: float = 0.7):
        """Yield text tokens from Groq streaming endpoint. Raises on errors."""
        if not self.api_key:
            raise ValueError("Groq API key not set.")

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }
        try:
            with requests.post(
                f"{GROQ_BASE}/chat/completions",
                headers=self._headers(),
                json=payload,
                stream=True,
                timeout=self.timeout,
            ) as resp:
                if resp.status_code == 401:
                    raise ValueError("Invalid Groq API key (401).")
                if resp.status_code == 429:
                    raise RuntimeError("Groq rate limit reached. Try again shortly.")
                if resp.status_code == 400:
                    try:
                        err = resp.json()
                        raise ValueError(f"Groq error: {err.get('error', {}).get('message', resp.text)}")
                    except Exception:
                        raise ValueError(f"Groq bad request: {resp.text[:200]}")
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
            raise ConnectionError("Cannot reach Groq API — check your internet.")
        except requests.exceptions.Timeout:
            raise TimeoutError("Groq request timed out.")
