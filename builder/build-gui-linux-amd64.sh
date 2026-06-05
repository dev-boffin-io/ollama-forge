#!/usr/bin/env bash
# =========================================================================
#  build-gui-linux-amd64.sh
#  Builds Ollama-ai-gui + Ollama-ai-manager for Linux x86_64 (AMD64).
#  Output → bin/Ollama-GUI/
#
#  Full isolation: fresh venv, no --system-site-packages, PyQt6 from pip.
#  Usage: bash builder/build-gui-linux-amd64.sh
# =========================================================================
set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────────────
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
GUI_DIR="$PROJECT_ROOT/gui"
VENV_DIR="$SCRIPT_DIR/.venv-gui-amd64"
OUT_DIR="$PROJECT_ROOT/bin/Ollama-GUI"

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "  Ollama GUI — Linux AMD64 Build"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

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

[[ -f "$GUI_DIR/main.py" ]] || die "GUI source not found: $GUI_DIR/main.py"

# ── Fresh isolated venv ───────────────────────────────────────────────────
info "Creating isolated build environment..."
rm -rf "$VENV_DIR"
"$PY_BIN" -m venv "$VENV_DIR"          # no --system-site-packages = full isolation
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

pip install --quiet --upgrade pip setuptools wheel

# ── Install PyQt6 + runtime deps ──────────────────────────────────────────
info "Installing PyQt6 and runtime dependencies..."
pip install --quiet \
    "PyQt6>=6.6.0" \
    "PyQt6-Qt6>=6.6.0" \
    "PyQt6-sip>=13.6.0"

pip install --quiet \
    requests \
    packaging \
    numpy \
    sentence-transformers \
    faiss-cpu \
    pypdf \
    python-docx

# ── Install PyInstaller ───────────────────────────────────────────────────
info "Installing PyInstaller..."
pip install --quiet pyinstaller pyinstaller-hooks-contrib
pip install --quiet --force-reinstall setuptools
PYINSTALLER_BIN="$VENV_DIR/bin/pyinstaller"
[[ -x "$PYINSTALLER_BIN" ]] || die "PyInstaller not found at $PYINSTALLER_BIN"
ok "PyInstaller: $($PYINSTALLER_BIN --version)"

# ── Prepare output directory ──────────────────────────────────────────────
mkdir -p "$OUT_DIR"
rm -rf "$GUI_DIR/build" "$GUI_DIR/dist" "$GUI_DIR/__pycache__" "$GUI_DIR"/*.spec

# ── Shared PyInstaller flags ──────────────────────────────────────────────
HIDDEN=(
    --hidden-import numpy
    --hidden-import faiss
    --hidden-import sentence_transformers
    --hidden-import sentence_transformers.models
    --hidden-import sentence_transformers.losses
    --hidden-import pypdf
    --hidden-import pypdf._reader
    --hidden-import docx
    --hidden-import docx.oxml
    --hidden-import requests
    --hidden-import urllib.request
    --hidden-import pty
    --hidden-import tty
    --hidden-import termios
    --hidden-import select
    --hidden-import threading
    --hidden-import tempfile
    --hidden-import shutil
    --hidden-import ollama_manager
    --hidden-import ollama_manager.window
    --hidden-import ollama_manager.workers
    --hidden-import ollama_manager.helpers
)

EXCLUDED=(
    --exclude-module torch
    --exclude-module torchvision
    --exclude-module torchaudio
    --exclude-module triton
    --exclude-module sklearn
    --exclude-module scipy
    --exclude-module nltk
    --exclude-module nvidia
    --exclude-module tensorflow
    --exclude-module tensorboard
    --exclude-module pytest
    --exclude-module tkinter
    --exclude-module _tkinter
)

# ── Build Ollama-ai-gui ───────────────────────────────────────────────────
info "Building Ollama-ai-gui..."
cd "$GUI_DIR"
"$PYINSTALLER_BIN" \
    --onefile \
    --windowed \
    --name Ollama-ai-gui \
    --clean \
    "${HIDDEN[@]}" \
    "${EXCLUDED[@]}" \
    main.py

# ── Build Ollama-ai-manager ───────────────────────────────────────────────
info "Building Ollama-ai-manager..."
"$PYINSTALLER_BIN" \
    --onefile \
    --windowed \
    --name Ollama-ai-manager \
    --clean \
    "${HIDDEN[@]}" \
    "${EXCLUDED[@]}" \
    manager_entry.py

# ── Move binaries to bin/Ollama-GUI/ ─────────────────────────────────────
for name in Ollama-ai-gui Ollama-ai-manager; do
    [[ -f "$GUI_DIR/dist/$name" ]] || die "Build failed — $name not found"
    mv "$GUI_DIR/dist/$name" "$OUT_DIR/$name"
    chmod +x "$OUT_DIR/$name"
done

# ── Cleanup ───────────────────────────────────────────────────────────────
rm -rf "$GUI_DIR/build" "$GUI_DIR/dist" "$GUI_DIR/__pycache__" "$GUI_DIR"/*.spec
rm -rf "$VENV_DIR"
deactivate 2>/dev/null || true

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "  ✅ GUI AMD64 build complete"
echo ""
echo "  bin/Ollama-GUI/"
echo "    ├── Ollama-ai-gui       $(du -sh "$OUT_DIR/Ollama-ai-gui" | cut -f1)"
echo "    └── Ollama-ai-manager   $(du -sh "$OUT_DIR/Ollama-ai-manager" | cut -f1)"
echo ""
echo "  Both binaries must be in the same folder."
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
