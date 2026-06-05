#!/usr/bin/env bash
# =========================================================================
#  build-da-linux-amd64.sh
#  Builds  da  +  ollama-main  for Linux x86_64 (AMD64).
#  Output → bin/dev-assist/
#
#  Full isolation: fresh venv, no --system-site-packages.
#  Usage: bash builder/build-da-linux-amd64.sh
# =========================================================================
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'
BLUE='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
info() { echo -e "${BLUE}[→]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
die()  { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

# ── Architecture guard ────────────────────────────────────────────────────
ARCH=$(uname -m)
[[ "$ARCH" == "x86_64" ]] || die "This script is for AMD64 (x86_64). Current: $ARCH"

# ── Paths ─────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DA_DIR="$PROJECT_ROOT/dev-assist"
VENV_DIR="$SCRIPT_DIR/.venv-da-amd64"
OUT_DIR="$PROJECT_ROOT/bin/dev-assist"

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "  dev-assist — Linux AMD64 Build  (da + ollama-main)"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ── Validate sources ──────────────────────────────────────────────────────
[[ -f "$DA_DIR/main.py" ]] \
    || die "dev-assist entry not found: $DA_DIR/main.py"
[[ -f "$DA_DIR/ollama-main/main.py" ]] \
    || die "ollama-main entry not found: $DA_DIR/ollama-main/main.py"

# ── Detect Python ─────────────────────────────────────────────────────────
# ── Detect Python ─────────────────────────────────────────────────────────────
# Prefer system Python (e.g. /usr/bin) over Termux — PyQt6/manylinux wheels
# require glibc-based Python, not Termux's Android-linked interpreter.
detect_python() {
    local cand _p
    # 1. System paths first (works correctly in proot-Debian + Termux env)
    for cand in python3.12 python3.11 python3.10 python3; do
        for _sys_dir in /usr/bin /usr/local/bin; do
            _p="$_sys_dir/$cand"
            [[ -x "$_p" ]] && echo "$_p" && return 0
        done
    done
    # 2. PATH-based detection — skip Termux Python (no manylinux wheel support)
    for cand in python3.12 python3.11 python3.10 python3 python; do
        _p="$(command -v "$cand" 2>/dev/null)" || continue
        [[ "$_p" == /data/data/com.termux/* ]] && continue
        echo "$_p" && return 0
    done
    return 1
}

PY_BIN="$(detect_python)"
[[ -n "$PY_BIN" ]] || die "No suitable Python interpreter found."
info "Python: $PY_BIN ($($PY_BIN --version 2>&1))"

# ── Fresh isolated venv ───────────────────────────────────────────────────
info "Creating isolated build environment..."
rm -rf "$VENV_DIR"
"$PY_BIN" -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

pip install --quiet --upgrade pip setuptools wheel

# ── dev-assist deps ───────────────────────────────────────────────────────
info "Installing dev-assist dependencies..."
if [[ -f "$DA_DIR/requirements.txt" ]]; then
    pip install --quiet -r "$DA_DIR/requirements.txt"
else
    # Fallback minimal set
    pip install --quiet \
        chainlit \
        crewai \
        requests \
        packaging \
        openai \
        anthropic \
        rich \
        typer \
        click \
        httpx
fi

# ── PyInstaller ───────────────────────────────────────────────────────────
info "Installing PyInstaller..."
pip install --quiet pyinstaller pyinstaller-hooks-contrib
pip install --quiet --force-reinstall setuptools
PYINSTALLER_BIN="$VENV_DIR/bin/pyinstaller"
[[ -x "$PYINSTALLER_BIN" ]] || die "PyInstaller not found."
ok "PyInstaller: $($PYINSTALLER_BIN --version)"

mkdir -p "$OUT_DIR"

# ── Shared flags ──────────────────────────────────────────────────────────
DA_HIDDEN=(
    --hidden-import chainlit
    --hidden-import chainlit.cli
    --hidden-import crewai
    --hidden-import crewai.agent
    --hidden-import crewai.task
    --hidden-import crewai.crew
    --hidden-import openai
    --hidden-import anthropic
    --hidden-import requests
    --hidden-import httpx
    --hidden-import rich
    --hidden-import rich.console
    --hidden-import typer
    --hidden-import click
    --hidden-import asyncio
    --hidden-import importlib.metadata
    --hidden-import pkg_resources
)

DA_EXCLUDED=(
    --exclude-module PyQt5
    --exclude-module PyQt6
    --exclude-module PySide2
    --exclude-module PySide6
    --exclude-module tkinter
    --exclude-module _tkinter
    --exclude-module pytest
    --exclude-module torch
    --exclude-module tensorflow
    --exclude-module tensorboard
    --exclude-module sklearn
    --exclude-module scipy
    --exclude-module matplotlib
)

OLLAMA_HIDDEN=(
    --hidden-import requests
    --hidden-import packaging
    --hidden-import packaging.version
    --hidden-import subprocess
    --hidden-import argparse
    --hidden-import tempfile
    --hidden-import shutil
    --hidden-import json
    --hidden-import re
)

OLLAMA_EXCLUDED=(
    --exclude-module PyQt5
    --exclude-module PyQt6
    --exclude-module tkinter
    --exclude-module pytest
    --exclude-module torch
    --exclude-module tensorflow
)

# ── Build da ──────────────────────────────────────────────────────────────
info "Building da (dev-assist CLI)..."
rm -rf "$DA_DIR/build" "$DA_DIR/dist" "$DA_DIR/__pycache__" "$DA_DIR"/*.spec

cd "$DA_DIR"
"$PYINSTALLER_BIN" \
    --onefile \
    --name da \
    --clean \
    "${DA_HIDDEN[@]}" \
    "${DA_EXCLUDED[@]}" \
    main.py

[[ -f "$DA_DIR/dist/da" ]] || die "Build failed — da binary not found"
mv "$DA_DIR/dist/da" "$OUT_DIR/da"
chmod +x "$OUT_DIR/da"
ok "da → $OUT_DIR/da  ($(du -sh "$OUT_DIR/da" | cut -f1))"

# ── Build ollama-main ─────────────────────────────────────────────────────
info "Building ollama-main..."
rm -rf "$DA_DIR/build" "$DA_DIR/dist" "$DA_DIR/__pycache__" "$DA_DIR"/*.spec

"$PYINSTALLER_BIN" \
    --onefile \
    --name ollama-main \
    --clean \
    "${OLLAMA_HIDDEN[@]}" \
    "${OLLAMA_EXCLUDED[@]}" \
    ollama-main/main.py

[[ -f "$DA_DIR/dist/ollama-main" ]] || die "Build failed — ollama-main binary not found"
mv "$DA_DIR/dist/ollama-main" "$OUT_DIR/ollama-main"
chmod +x "$OUT_DIR/ollama-main"
ok "ollama-main → $OUT_DIR/ollama-main  ($(du -sh "$OUT_DIR/ollama-main" | cut -f1))"

# ── Cleanup ───────────────────────────────────────────────────────────────
rm -rf "$DA_DIR/build" "$DA_DIR/dist" "$DA_DIR/__pycache__" "$DA_DIR"/*.spec
rm -rf "$VENV_DIR"
deactivate 2>/dev/null || true

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "  ✅ dev-assist AMD64 build complete"
echo ""
echo "  bin/dev-assist/"
echo "    ├── da            $(du -sh "$OUT_DIR/da" | cut -f1)"
echo "    └── ollama-main   $(du -sh "$OUT_DIR/ollama-main" | cut -f1)"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
