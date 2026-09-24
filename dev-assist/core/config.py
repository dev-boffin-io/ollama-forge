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
    from pydantic import BaseModel, Field, ValidationError, field_validator
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

    class RoutingConfig(BaseModel):
        enabled: bool = Field(default=True, description="Auto-route tasks to specialised agents")
        default_agent: str = Field(default="build", description="Fallback agent for unclassified tasks")
        compaction_chars: int = Field(
            default=60000, ge=1000,
            description="Compact the session history once it exceeds this many chars",
        )

    @staticmethod
    def _provider_defaults() -> dict[str, ProviderProfile]:
        from core import providers as _providers
        return {
            pid: ProviderProfile.model_validate(p)
            for pid, p in _providers.provider_defaults_raw().items()
        }

    class ProviderProfile(BaseModel):
        label: str = ""
        kind: str = "openai_compat"
        needs_key: bool = False
        env_var: str = ""
        base_url: str = ""
        api_version: str = ""
        azure: bool = False
        default_model: str = ""
        models: list[str] = []
        api_key: str = Field(default="", description="Optional; env vars preferred. Never required.")

        @field_validator("base_url")
        @classmethod
        def validate_url(cls, v: str) -> str:
            if v and not v.startswith(("http://", "https://")):
                raise ValueError(f"base_url must start with http:// or https://, got: {v}")
            return v

    class AppConfig(BaseModel):
        ai_engine: str = Field(default="ollama", pattern="^(ollama|api)$")
        active_provider: str = Field(default="ollama", description="Provider id from core.providers.PROVIDERS")
        providers: dict[str, ProviderProfile] = Field(default_factory=_provider_defaults)
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
        commands: dict[str, dict] = Field(
            default_factory=dict,
            description="Slash commands (/name): {template, description, subtask} "
                        "with $ARGUMENTS / $1..$N placeholders",
        )
        routing: RoutingConfig = Field(default_factory=RoutingConfig)
        agents: dict[str, dict] = Field(
            default_factory=dict,
            description="Agent routing: custom agents or overrides of built-ins "
                        "{name, description, system_prompt, read_only, uses_planning, hidden, mode}",
        )
        theme: str = Field(
            default="default",
            description="UI theme preset: default, ocean, gruvbox, monokai, nord",
        )
        permissions: dict[str, dict] = Field(
            default_factory=dict,
            description="Tool permissions: {tool: {group, verdict}} — verdict " 
                        "is allow | ask | deny; groups match by prefix (edit, "
                        "bash, webfetch, read, task)",
        )
        autocommit: str = Field(
            default="off",
            description="Auto-commit after agent runs: off | ask | auto",
        )
        mcp_servers: dict[str, dict] = Field(
            default_factory=dict,
            description="MCP servers: {name: {command, args, env}} — stdio "
                        "transports, spawned lazily by core.mcp",
        )
        lsp: dict[str, dict] = Field(
            default_factory=dict,
            description="LSP servers per language: {lang: {command, args, env}}",
        )

        def get_active_api_key(self) -> str:
            """Return API key from env var first, then config (never writes back)."""
            env_key = os.environ.get("DEV_ASSIST_API_KEY", "")
            if env_key:
                return env_key
            return self.api_engine.api_key

        def get_provider(self, provider: str | None = None) -> ProviderProfile:
            """Resolve a provider profile (defaults to the active one)."""
            from core import providers as _providers
            pid = provider or _providers.active_provider(self)
            prof = self.providers.get(pid)
            if prof is not None:
                return prof
            return ProviderProfile.model_validate(_providers.PROVIDERS[pid]) if pid in _providers.PROVIDERS else ProviderProfile()

        def get_current_model(self) -> str:
            from core import providers as _providers
            pid = _providers.active_provider(self)
            prof = self.get_provider(pid)
            model = prof.default_model
            if pid == "ollama" and not model:
                model = self.ollama_model
            return f"{pid}/{model}"


# ── Load / Save ──────────────────────────────────────────────────────────────

def load_config() -> AppConfig | dict[str, Any]:
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

    # Normalize: legacy single-engine configs map onto the provider catalog
    # (ai_engine=ollama -> ollama, ai_engine=api -> groq) and provider
    # defaults are filled so older settings.json files get provider rows.
    raw = _normalize_providers(raw)

    if not _PYDANTIC:
        return raw  # type: ignore[return-value]

    try:
        cfg = AppConfig(**raw)
        return cfg
    except ValidationError as exc:
        _warn(f"Config validation failed:\n{exc}\nUsing defaults for invalid fields.")
        # Field-by-field fallback: keep the fields that are valid,
        # fall back to defaults only for the ones that aren't.
        try:
            valid: dict[str, Any] = {}
            for field in AppConfig.model_fields:
                if field in raw:
                    try:
                        AppConfig.model_validate({field: raw[field]})
                        valid[field] = raw[field]
                    except ValidationError:
                        pass
            return AppConfig(**valid)
        except Exception:
            return AppConfig.model_construct()


def save_config(data: AppConfig | dict[str, Any]) -> None:
    """
    Persist config. Strips plaintext api_key if set via env var.
    """
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if _PYDANTIC and isinstance(data, AppConfig):
        raw = data.model_dump()
    else:
        raw = dict(data)  # type: ignore[arg-type]

    # Normalize since dict-mode saves go straight back to disk.
    raw = _normalize_providers(raw)

    # Safety: never write plaintext API key if env var is set
    if os.environ.get("DEV_ASSIST_API_KEY"):
        if "api_engine" in raw and isinstance(raw["api_engine"], dict):
            raw["api_engine"]["api_key"] = ""
    for prof in (raw.get("providers") or {}).values():
        if isinstance(prof, dict):
            prof["api_key"] = ""

    raw["_comment"] = "dev-assist config — set API keys via env vars (e.g. GROQ_API_KEY) instead of here"

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(raw, f, indent=2, ensure_ascii=False)


def _normalize_providers(raw: dict[str, Any]) -> dict[str, Any]:
    """Fill/repair the provider fields on a dict config, mapping legacy
    single-engine settings (ai_engine=ollama/api) onto the catalog."""
    from core import providers as _providers

    raw.setdefault("providers", _providers.provider_defaults_raw())
    if not isinstance(raw["providers"], dict):
        raw["providers"] = _providers.provider_defaults_raw()
    for pid, defaults in _providers.provider_defaults_raw().items():
        prof = raw["providers"].get(pid)
        if not isinstance(prof, dict):
            raw["providers"][pid] = dict(defaults)

    active = raw.get("active_provider", "")
    if active not in _providers.PROVIDERS:
        engine = raw.get("ai_engine", "ollama")
        raw["active_provider"] = "ollama" if engine == "ollama" else "groq"
    return raw


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
