"""
Tests for core/theme.py — theme presets, switching, and config round-trip.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from core.theme import THEMES, get_theme, reset_cache, set_theme, toolbar_style


@pytest.fixture
def fake_cfg(monkeypatch):
    """In-memory settings.json: {theme} round-trips through a dict."""
    saved: dict = {}

    def _fake_load():
        return dict(saved)

    def _fake_save(data):
        if hasattr(data, "model_dump"):
            saved.update(data.model_dump())
        else:
            saved.update(dict(data))

    from core import config as core_config
    monkeypatch.setattr(core_config, "load_config", _fake_load)
    monkeypatch.setattr(core_config, "save_config", _fake_save)
    reset_cache()
    yield saved
    reset_cache()


class TestThemes:
    def test_default_theme(self):
        assert THEMES["default"].name == "default"
        assert "default" in THEMES
        assert {"default", "ocean", "gruvbox", "monokai", "nord"} <= set(THEMES)

    def test_get_theme_default_when_unconfigured(self, fake_cfg):
        reset_cache()
        assert get_theme().name == "default"

    def test_set_theme_returns_true_and_persists(self, fake_cfg):
        assert set_theme("ocean") is True
        assert fake_cfg["theme"] == "ocean"
        assert get_theme().name == "ocean"

    def test_set_unknown_theme_rejected(self, fake_cfg, capsys):
        assert set_theme("neon-nightmare") is False
        assert "theme" not in fake_cfg
        assert get_theme().name == "default"

    def test_get_theme_reads_configured_name(self, fake_cfg):
        fake_cfg["theme"] = "nord"
        reset_cache()
        assert get_theme().name == "nord"
        assert get_theme().border == "bright_cyan"

    def test_theme_fields(self, fake_cfg):
        fake_cfg["theme"] = "gruvbox"
        reset_cache()
        t = get_theme()
        assert t.toolbar_bg == "#1d2021"
        assert t.activity_fg == "#fabd2f"
        assert t.border == "yellow"

    def test_reset_cache_forces_reload(self, fake_cfg):
        fake_cfg["theme"] = "monokai"
        reset_cache()
        assert get_theme().name == "monokai"
        fake_cfg["theme"] = "default"
        reset_cache()
        assert get_theme().name == "default"


class TestToolbarStyle:
    def test_builds_prompt_toolkit_style(self, fake_cfg):
        style = toolbar_style(THEMES["ocean"])
        attrs = style.get_attrs_for_style_str("class:toolbar")
        assert bool(attrs.color)
        assert bool(attrs.bgcolor)


class TestConfigAppThemeField:
    def test_appconfig_has_theme_default(self):
        from core.config import AppConfig
        cfg = AppConfig.model_construct() if hasattr(AppConfig, "model_construct") else None
        if cfg is not None:
            assert cfg.theme == "default"
