#!/usr/bin/env python3
"""
Ollama API client — streaming, vision, model listing.
No LangChain. No heavy deps.
"""
import json
import time
import requests


class OllamaClient:
    BASE = "http://localhost:11434"

    def __init__(self, timeout: int = 300, retries: int = 3):
        self.timeout = timeout
        self.retries = retries

    # ------------------------------------------------------------------ #
    #  Model list                                                          #
    # ------------------------------------------------------------------ #
    # Fallback keyword hints — only used when /api/show gives no capability info
    _EMBED_KEYWORDS = (
        "embed", "bge-", "e5-", "gte-", "snowflake-arctic-embed",
    )
    _VISION_KEYWORDS = (
        "llava", "vision", ":vl", "bakllava", "moondream", "phi3-v",
    )

    def _model_capabilities(self, name: str) -> dict:
        """
        Ask Ollama /api/show for actual capabilities.
        Returns {"embed": bool, "vision": bool}.
        Falls back to keyword matching if API call fails.
        """
        try:
            r = requests.post(
                f"{self.BASE}/api/show",
                json={"name": name},
                timeout=8,
            )
            if r.status_code == 200:
                info = r.json()
                model_info = info.get("model_info", {})
                has_projector = any("projector" in k for k in model_info)

                # Ollama >=0.3 exposes capabilities list
                capabilities = info.get("capabilities", [])
                if isinstance(capabilities, list) and capabilities:
                    is_embed  = "embedding" in capabilities
                    is_vision = "vision" in capabilities or has_projector
                    return {"embed": is_embed, "vision": is_vision}

                # Older Ollama — use modelfile + keyword
                modelfile = info.get("modelfile", "").lower()
                lo = name.lower()
                is_embed  = any(k in lo or k in modelfile
                                for k in self._EMBED_KEYWORDS)
                is_vision = (not is_embed) and (
                    any(k in lo for k in self._VISION_KEYWORDS)
                    or has_projector
                )
                return {"embed": is_embed, "vision": is_vision}
        except Exception:
            pass

        # Pure keyword fallback (Ollama unreachable or error)
        lo = name.lower()
        is_embed = any(k in lo for k in self._EMBED_KEYWORDS)
        return {
            "embed":  is_embed,
            "vision": (not is_embed) and any(k in lo for k in self._VISION_KEYWORDS),
        }

    def list_models(self) -> list[dict]:
        """
        Return all models with capabilities resolved via /api/show.
        Each dict: name, size, modified_at, vision (bool), embed (bool).
        """
        r = requests.get(f"{self.BASE}/api/tags", timeout=5)
        r.raise_for_status()
        models = []
        for m in r.json().get("models", []):
            name = m["name"]
            caps = self._model_capabilities(name)
            models.append({
                "name":        name,
                "size":        m.get("size", 0),
                "modified_at": m.get("modified_at", ""),
                "vision":      caps["vision"],
                "embed":       caps["embed"],
            })
        return models

    def list_chat_models(self) -> list[dict]:
        """Return only models suitable for chat (not embedding-only)."""
        return [m for m in self.list_models() if not m["embed"]]

    def list_embed_models(self) -> list[dict]:
        """Return only embedding models available in Ollama."""
        return [m for m in self.list_models() if m["embed"]]

    def is_running(self) -> bool:
        try:
            return requests.get(f"{self.BASE}/api/tags", timeout=3).status_code == 200
        except Exception:
            return False

    def show_model(self, name: str) -> dict:
        r = requests.post(f"{self.BASE}/api/show", json={"name": name}, timeout=10)
        if r.status_code == 200:
            return r.json()
        return {}

    # ------------------------------------------------------------------ #
    #  Streaming chat                                                      #
    # ------------------------------------------------------------------ #
    def chat_stream(self, model: str, messages: list[dict],
                    temperature: float = 0.7):
        """Yield text tokens. Raises on errors."""
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {"temperature": temperature},
        }
        for attempt in range(self.retries):
            try:
                with requests.post(
                    f"{self.BASE}/api/chat",
                    json=payload, stream=True, timeout=self.timeout
                ) as resp:
                    resp.raise_for_status()
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        data = json.loads(line.decode())
                        if "message" in data and "content" in data["message"]:
                            yield data["message"]["content"]
                        if data.get("done"):
                            return
                return   # success — no retry needed
            except requests.exceptions.HTTPError as e:
                if resp.status_code == 404:
                    raise ValueError(f"Model not found: {model}")
                raise RuntimeError(f"HTTP {resp.status_code}: {e}")
            except requests.exceptions.Timeout:
                if attempt < self.retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise TimeoutError("Request timed out after retries")
            except requests.exceptions.ConnectionError:
                raise ConnectionError("Ollama not responding")
