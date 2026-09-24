"""
Themes — user-selectable UI colour presets (opencode's `/themes` parity).

A theme drives the prompt_toolkit bottom-toolbar style, the syntax theme
used for diff previews, and the accent border colour of agent panels.
The active theme name is stored in settings.json (`theme` key) and switched
from the REPL with `/themes` (or `/theme <name>`).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

# Short cache so theme lookups on hot paths (toolbar render, diff previews)
# don't re-read settings.json every time.
_cache: tuple[float, str] | None = None
_CACHE_TTL = 2.0


@dataclass(frozen=True)
class Theme:
    name: str
    toolbar_bg: str = "#333333"
    toolbar_fg: str = "#ffffff"
    activity_bg: str = "#333333"
    activity_fg: str = "#ffcc00"
    border: str = "cyan"
    syntax: str = "ansi_dark"


THEMES: dict[str, Theme] = {
    "default": Theme(name="default"),
    "ocean": Theme(
        name="ocean",
        toolbar_bg="#0f2537", toolbar_fg="#e6f1ff",
        activity_bg="#0f2537", activity_fg="#7fd1ff",
        border="blue", syntax="material",
    ),
    "gruvbox": Theme(
        name="gruvbox",
        toolbar_bg="#1d2021", toolbar_fg="#ebdbb2",
        activity_bg="#1d2021", activity_fg="#fabd2f",
        border="yellow", syntax="gruvbox-dark",
    ),
    "monokai": Theme(
        name="monokai",
        toolbar_bg="#272822", toolbar_fg="#f8f8f2",
        activity_bg="#272822", activity_fg="#a6e22e",
        border="magenta", syntax="monokai",
    ),
    "nord": Theme(
        name="nord",
        toolbar_bg="#2e3440", toolbar_fg="#d8dee9",
        activity_bg="#2e3440", activity_fg="#88c0d0",
        border="bright_cyan", syntax="nord-darker",
    ),
}


def _configured_name() -> str:
    global _cache
    now = time.time()
    if _cache and (now - _cache[0]) < _CACHE_TTL:
        return _cache[1]
    name = "default"
    try:
        from core import config as _config
        cfg = _config.load_config()
        if hasattr(cfg, "theme"):
            name = str(cfg.theme or "default")
        elif isinstance(cfg, dict):
            name = str(cfg.get("theme") or "default")
    except Exception:
        pass
    _cache = (now, name)
    return name


def get_theme() -> Theme:
    """Resolve the active theme (falls back to `default`)."""
    return THEMES.get(_configured_name(), THEMES["default"])


def set_theme(name: str) -> bool:
    """Persist the active theme. Returns True when the theme exists."""
    if name not in THEMES:
        return False
    try:
        from core import config as _config
        cfg = _config.load_config()
        if hasattr(cfg, "theme"):
            _config.save_config(cfg.model_copy(update={"theme": name}))
        else:
            data = dict(cfg)  # type: ignore[arg-type]
            data["theme"] = name
            _config.save_config(data)
    except Exception:
        pass
    global _cache
    _cache = (time.time(), name)
    return True


def toolbar_style(theme: Theme):
    """prompt_toolkit Style for the bottom toolbar of a theme."""
    from prompt_toolkit.styles import Style
    return Style.from_dict({
        "toolbar": f"bg:{theme.toolbar_bg} {theme.toolbar_fg}",
        "toolbar.activity": f"bg:{theme.activity_bg} {theme.activity_fg}",
    })


def reset_cache() -> None:
    global _cache
    _cache = None
