"""
ollama_manager/helpers.py
Pure Python helpers — no Qt dependency.
Handles: config persistence, binary detection, username parsing, formatting.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

# ── Persistent config ────────────────────────────────────────────────────────
_CONFIG_DIR    = os.path.join(os.path.expanduser("~"), ".ollama_gui")
_SETTINGS_FILE = os.path.join(_CONFIG_DIR, "settings.json")

OLLAMA_PATHS = [
    "/usr/local/bin/ollama",
    "/usr/local/lib/ollama",
    "/usr/share/ollama",
    "/etc/systemd/system/ollama.service",
    "/etc/systemd/system/ollama.service.d",
]


# ── Config I/O ───────────────────────────────────────────────────────────────

def load_ollama_bin() -> str:
    try:
        with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("ollama_bin", "")
    except Exception:
        return ""


def save_ollama_bin(path: str) -> None:
    os.makedirs(_CONFIG_DIR, exist_ok=True)
    try:
        data: dict = {}
        try:
            with open(_SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
        data["ollama_bin"] = path
        with open(_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


# ── Binary detection ──────────────────────────────────────────────────────────

def autodetect_ollama() -> str:
    import shutil
    found = shutil.which("ollama")
    if found:
        return found
    for p in (
        "/usr/local/bin/ollama",
        "/usr/bin/ollama",
        os.path.expanduser("~/bin/ollama"),
        "/data/data/com.termux/files/usr/bin/ollama",
    ):
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return ""


# ── Auth helpers ──────────────────────────────────────────────────────────────

def read_ollama_username() -> str | None:
    for path in [
        os.path.expanduser("~/.ollama/config"),
        os.path.expanduser("~/.config/ollama/config"),
    ]:
        if os.path.exists(path):
            try:
                d = json.loads(open(path, encoding="utf-8").read())
                u = d.get("username") or d.get("user") or d.get("name")
                if u:
                    return str(u)
            except Exception:
                pass
    return None


def parse_signin_username(line: str) -> str | None:
    for pat in [
        r"signed in as user\s+['\"]?([A-Za-z0-9_\-]+)['\"]?",
        r"already signed in as\s+['\"]?([A-Za-z0-9_\-]+)['\"]?",
        r"logged in as\s+['\"]?([A-Za-z0-9_\-]+)['\"]?",
        r"welcome[,\s]+['\"]?([A-Za-z0-9_\-]+)['\"]?",
    ]:
        m = re.search(pat, line, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


# ── Misc ──────────────────────────────────────────────────────────────────────

def safe_remove(path: str) -> None:
    try:
        os.remove(path)
    except Exception:
        pass


def fmt_size(b: int | float) -> str:
    if not b:
        return "?"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"
