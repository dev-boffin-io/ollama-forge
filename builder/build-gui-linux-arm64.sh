#!/usr/bin/env bash
# =========================================================================
#  build-gui-linux-arm64.sh
#  Builds Ollama-ai-gui + Ollama-ai-manager for Linux aarch64 (ARM64).
#  Output → bin/Ollama-GUI/
#
#  PyQt6 has ARM64 pip wheels — full isolation, no system-site-packages.
#  Usage: bash builder/build-gui-linux-arm64.sh
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
[[ "$ARCH" == "aarch64" ]] || die "This script is for ARM64 (aarch64). Current: $ARCH"

# ── System deps preflight (libGL, libxcb — needed by Qt) ─────────────────
arm64_preflight() {
    info "ARM64 preflight — checking system libraries..."
    MISSING=""
    # Use ldconfig to check system libs — no Python dependency
    if ! ldconfig -p 2>/dev/null | grep -q "libGL.so.1"; then
        command -v dpkg &>/dev/null && dpkg -s libgl1-mesa-dev &>/dev/null || \
            MISSING="$MISSING libgl1-mesa-dev"
    fi
    if ! ldconfig -p 2>/dev/null | grep -q "libxcb-xinerama.so.0"; then
        command -v dpkg &>/dev/null && dpkg -s libxcb-xinerama0 &>/dev/null || \
            MISSING="$MISSING libxcb-xinerama0"
    fi
    if [[ -n "$MISSING" ]]; then
        echo ""
        warn "Missing system packages: $MISSING"
        warn "Run: sudo apt-get install -y $MISSING"
        echo ""
        die "Install system dependencies first."
    fi
    ok "System libraries OK"
}

# ── Paths ─────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
GUI_DIR="$PROJECT_ROOT/gui"
VENV_DIR="$SCRIPT_DIR/.venv-gui-arm64"
OUT_DIR="$PROJECT_ROOT/bin/Ollama-GUI"

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "  Ollama GUI — Linux ARM64 Build"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

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

arm64_preflight

[[ -f "$GUI_DIR/main.py" ]] || die "GUI source not found: $GUI_DIR/main.py"

# ── Fresh isolated venv ───────────────────────────────────────────────────
info "Creating isolated build environment..."
rm -rf "$VENV_DIR"
"$PY_BIN" -m venv "$VENV_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

pip install --quiet --upgrade pip setuptools wheel

# ── PyQt6 (ARM64 wheel available on PyPI since PyQt6 6.4) ────────────────
info "Installing PyQt6..."
pip install --quiet \
    "PyQt6>=6.6.0" \
    "PyQt6-Qt6>=6.6.0" \
    "PyQt6-sip>=13.6.0"

info "Installing runtime dependencies..."
pip install --quiet \
    requests \
    packaging \
    numpy \
    sentence-transformers \
    faiss-cpu \
    pypdf \
    python-docx

info "Installing PyInstaller..."
pip install --quiet pyinstaller pyinstaller-hooks-contrib
pip install --quiet --force-reinstall setuptools
PYINSTALLER_BIN="$VENV_DIR/bin/pyinstaller"
[[ -x "$PYINSTALLER_BIN" ]] || die "PyInstaller not found."
ok "PyInstaller: $($PYINSTALLER_BIN --version)"

mkdir -p "$OUT_DIR"
rm -rf "$GUI_DIR/build" "$GUI_DIR/dist" "$GUI_DIR/__pycache__" "$GUI_DIR"/*.spec

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

info "Building Ollama-ai-gui (ARM64)..."
cd "$GUI_DIR"
"$PYINSTALLER_BIN" \
    --onefile \
    --windowed \
    --name Ollama-ai-gui-arm64 \
    --clean \
    "${HIDDEN[@]}" \
    "${EXCLUDED[@]}" \
    main.py

info "Building Ollama-ai-manager (ARM64)..."
"$PYINSTALLER_BIN" \
    --onefile \
    --windowed \
    --name Ollama-ai-manager-arm64 \
    --clean \
    "${HIDDEN[@]}" \
    "${EXCLUDED[@]}" \
    manager_entry.py

for name in Ollama-ai-gui-arm64 Ollama-ai-manager-arm64; do
    [[ -f "$GUI_DIR/dist/$name" ]] || die "Build failed — $name not found"
    mv "$GUI_DIR/dist/$name" "$OUT_DIR/$name"
    chmod +x "$OUT_DIR/$name"
done

rm -rf "$GUI_DIR/build" "$GUI_DIR/dist" "$GUI_DIR/__pycache__" "$GUI_DIR"/*.spec
rm -rf "$VENV_DIR"
deactivate 2>/dev/null || true

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "  ✅ GUI ARM64 build complete"
echo ""
echo "  bin/Ollama-GUI/"
echo "    ├── Ollama-ai-gui-arm64       $(du -sh "$OUT_DIR/Ollama-ai-gui-arm64" | cut -f1)"
echo "    └── Ollama-ai-manager-arm64   $(du -sh "$OUT_DIR/Ollama-ai-manager-arm64" | cut -f1)"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
