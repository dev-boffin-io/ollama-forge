"""
Config — Pydantic-validated settings with environment variable support.

Priority (highest → lowest):
  1. Environment variables  (DEV_ASSIST_API_KEY, etc.)
  2. config/settings.json
  3. Pydantic defaults

Sensitive fields (api_key) are NEVER stored in settings.json in plaintext.
Instead set: export DEV_ASSIST_API_KEY="gsk_..."
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

try:
    from pydantic import BaseModel, Field, field_validator, ValidationError
    _PYDANTIC = True
except ImportError:
    _PYDANTIC = False

def _resolve_config_path() -> Path:
    """
    Config file location — resolved in this order:
    1. DEV_ASSIST_CONFIG_DIR env var  (set by runtime hook when running frozen)
    2. Repo-relative path             (normal dev / venv usage)
    """
    env_dir = os.environ.get("DEV_ASSIST_CONFIG_DIR")
    if env_dir:
        return Path(env_dir) / "settings.json"
    return Path(__file__).parent.parent / "config" / "settings.json"

CONFIG_PATH = _resolve_config_path()

# ── Pydantic models ──────────────────────────────────────────────────────────

if _PYDANTIC:
    class ApiEngineConfig(BaseModel):
        api_key: str = Field(default="", description="Set via DEV_ASSIST_API_KEY env var")
        api_url: str = "https://api.groq.com/openai/v1/chat/completions"
        api_model: str = "llama3-70b-8192"
        api_available_models: list[str] = [
            "llama3-70b-8192",
            "llama3-8b-8192",
            "mixtral-8x7b-32768",
            "gemma2-9b-it",
        ]

        @field_validator("api_url")
        @classmethod
        def validate_url(cls, v: str) -> str:
            if v and not v.startswith(("http://", "https://")):
                raise ValueError(f"api_url must start with http:// or https://, got: {v}")
            return v

    class TunnelConfig(BaseModel):
        default_port: str = "3000"
        auto_restart: bool = True
        restart_delay_seconds: int = Field(default=3, ge=1, le=60)
        max_lifetime_minutes: int = Field(default=120, ge=1, description="Warn after this many minutes")

        @field_validator("default_port")
        @classmethod
        def validate_port(cls, v: str) -> str:
            try:
                p = int(v)
                if not (1 <= p <= 65535):
                    raise ValueError
            except ValueError:
                raise ValueError(f"Invalid port: {v}")
            return v

    class AuditConfig(BaseModel):
        max_diff_lines: int = Field(default=3000, ge=100, le=50000)
        auto_run_on_push: bool = False
        sensitive_patterns: list[str] = [
            "*.pem", "*.key", "*.p12", "*.pfx",
            "id_rsa", "id_ed25519", ".env*",
            "*secret*", "*password*", "*credential*",
        ]

    class AppConfig(BaseModel):
        ai_engine: str = Field(default="ollama", pattern="^(ollama|api)$")
        ollama_model: str = "qwen2.5-coder:7b"
        ollama_available_models: list[str] = [
            "qwen2.5-coder:7b",
            "qwen2.5-coder:3b",
            "llama3.2:3b",
            "llama3.1:8b",
            "codellama:7b",
        ]
        api_engine: ApiEngineConfig = Field(default_factory=ApiEngineConfig)
        tunnel: TunnelConfig = Field(default_factory=TunnelConfig)
        audit: AuditConfig = Field(default_factory=AuditConfig)

        def get_active_api_key(self) -> str:
            """Return API key from env var first, then config (never writes back)."""
            env_key = os.environ.get("DEV_ASSIST_API_KEY", "")
            if env_key:
                return env_key
            return self.api_engine.api_key

        def get_current_model(self) -> str:
            if self.ai_engine == "ollama":
                return f"ollama/{self.ollama_model}"
            return f"api/{self.api_engine.api_model}"


# ── Load / Save ──────────────────────────────────────────────────────────────

def load_config() -> "AppConfig | dict[str, Any]":
    """
    Load and validate config. Returns AppConfig if pydantic is available,
    else a plain dict.
    """
    raw: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                raw = json.load(f)
        except json.JSONDecodeError as exc:
            _warn(f"settings.json is malformed: {exc}. Using defaults.")

    # Strip comment keys
    raw = {k: v for k, v in raw.items() if not k.startswith("_")}

    if not _PYDANTIC:
        return raw  # type: ignore[return-value]

    try:
        cfg = AppConfig(**raw)
        return cfg
    except ValidationError as exc:
        _warn(f"Config validation failed:\n{exc}\nUsing defaults for invalid fields.")
        # Try field-by-field to preserve valid values
        try:
            return AppConfig()
        except Exception:
            return AppConfig.model_construct()


def save_config(data: "AppConfig | dict[str, Any]") -> None:
    """
    Persist config. Strips plaintext api_key if set via env var.
    """
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if _PYDANTIC and isinstance(data, AppConfig):
        raw = data.model_dump()
    else:
        raw = dict(data)  # type: ignore[arg-type]

    # Safety: never write plaintext API key if env var is set
    if os.environ.get("DEV_ASSIST_API_KEY"):
        if "api_engine" in raw and isinstance(raw["api_engine"], dict):
            raw["api_engine"]["api_key"] = ""

    raw["_comment"] = "dev-assist config — set DEV_ASSIST_API_KEY env var instead of api_key here"

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)


def get_config_value(key: str, default: Any = None) -> Any:
    """Quick helper to get a single config value."""
    cfg = load_config()
    if _PYDANTIC and isinstance(cfg, AppConfig):
        return getattr(cfg, key, default)
    return cfg.get(key, default)  # type: ignore[union-attr]


def _warn(msg: str) -> None:
    try:
        from rich.console import Console
        Console(stderr=True).print(f"[yellow]⚠ config:[/yellow] {msg}")
    except ImportError:
        print(f"⚠ config: {msg}", flush=True)
