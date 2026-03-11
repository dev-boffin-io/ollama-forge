#!/usr/bin/env bash
# =============================================================
#  Ollama_ai — GUI Build Script (no root required)
#  Usage: ./builder/build-gui-bin.sh
#
#  ARM64/PRoot: run sudo ./builder/install-deps-gui.sh first
# =============================================================
set -euo pipefail

# ─── Resolve Project Root ────────────────────────────────────────────────
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( dirname "$SCRIPT_DIR" )"
GUI_DIR="$PROJECT_ROOT/gui"
TARGET_BIN="$PROJECT_ROOT/Ollama-ai-gui"
TARGET_MGR="$PROJECT_ROOT/Ollama-ai-manager"
VENV_DIR="$SCRIPT_DIR/.venv-build"
PYINSTALLER_BIN=""
DEPS_SCRIPT="$SCRIPT_DIR/install-deps-gui.sh"

echo "Building Ollama_ai GUI..."

# ─── Must NOT run as root ─────────────────────────────────────────────────
if [ "$(id -u)" -eq 0 ]; then
    echo "ERROR: Do not run this script as root."
    echo "       For system dependencies run: sudo $DEPS_SCRIPT"
    exit 1
fi

# ─── Detect Python binary ─────────────────────────────────────────────────
PY_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY_BIN="$(command -v "$candidate")"
        break
    fi
done || true

if [ -z "$PY_BIN" ]; then
    echo "ERROR: No Python interpreter found."
    exit 1
fi

echo "Python : $PY_BIN ($($PY_BIN --version 2>&1))"

# ─── Check source file ────────────────────────────────────────────────────
if [ ! -f "$GUI_DIR/main.py" ]; then
    echo "ERROR: GUI source not found: $GUI_DIR/main.py"
    exit 1
fi

# ─── Detect architecture ──────────────────────────────────────────────────
IS_ARM64=0
[ "$(uname -m)" = "aarch64" ] && IS_ARM64=1

# ─── ARM64: verify system deps (no install, no sudo) ─────────────────────
arm64_preflight() {
    echo ""
    echo "ARM64 detected — verifying system PyQt5..."

    MISSING=""

    if ! "$PY_BIN" -c "from PyQt5 import QtCore" 2>/dev/null; then
        MISSING="${MISSING} python3-pyqt5"
    fi

    # libGL — ctypes probe, then dpkg/rpm fallback
    HAS_GL=0
    if "$PY_BIN" -c "import ctypes; ctypes.CDLL('libGL.so.1')" 2>/dev/null; then
        HAS_GL=1
    elif command -v dpkg >/dev/null 2>&1; then
        for pkg in libgl1-mesa-dev libgl1; do
            dpkg -s "$pkg" >/dev/null 2>&1 && HAS_GL=1 && break
        done
    elif command -v rpm >/dev/null 2>&1; then
        rpm -q mesa-libGL >/dev/null 2>&1 && HAS_GL=1
    fi
    [ "$HAS_GL" -eq 0 ] && MISSING="${MISSING} libgl1-mesa-dev"

    # libxcb-xinerama
    HAS_XCB=0
    if "$PY_BIN" -c "import ctypes; ctypes.CDLL('libxcb-xinerama.so.0')" 2>/dev/null; then
        HAS_XCB=1
    elif command -v dpkg >/dev/null 2>&1 && dpkg -s libxcb-xinerama0 >/dev/null 2>&1; then
        HAS_XCB=1
    elif command -v rpm >/dev/null 2>&1 && rpm -q libxcb >/dev/null 2>&1; then
        HAS_XCB=1
    fi
    [ "$HAS_XCB" -eq 0 ] && MISSING="${MISSING} libxcb-xinerama0"

    if [ -n "$MISSING" ]; then
        echo ""
        echo "ERROR: Missing system packages: $MISSING"
        echo "  Run the dependency installer first:"
        echo "    sudo $DEPS_SCRIPT"
        echo ""
        exit 1
    fi

    VER="$("$PY_BIN" -c 'from PyQt5 import QtCore; print(QtCore.PYQT_VERSION_STR)')"
    echo "System PyQt5 OK: $VER"
}

# ─── Install PyInstaller into venv ────────────────────────────────────────
install_pyinstaller() {
    local py_ver
    py_ver="$("$PY_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    echo "Python $py_ver — installing latest compatible PyInstaller..."

    pip install --quiet --ignore-installed \
        pyinstaller \
        pyinstaller-hooks-contrib

    local installed
    installed="$(pip show pyinstaller 2>/dev/null | grep -i '^Version:' | awk '{print $2}')" || true
    echo "PyInstaller installed: ${installed:-unknown}"

    PYINSTALLER_BIN="$VENV_DIR/bin/pyinstaller"
    if [ ! -x "$PYINSTALLER_BIN" ]; then
        echo "ERROR: $PYINSTALLER_BIN not found after install."
        ls -la "$VENV_DIR/bin/" || true
        exit 1
    fi
    echo "PyInstaller binary: $PYINSTALLER_BIN"
}

# ─── Common Python dependencies (both architectures) ─────────────────────
install_common_deps() {
    pip install --quiet \
        requests \
        numpy \
        sentence-transformers \
        faiss-cpu \
        pypdf \
        python-docx
    echo "Common dependencies installed."
}

# ─── Isolated Virtual Environment ─────────────────────────────────────────
echo ""
echo "Setting up isolated build environment..."

rm -rf "$VENV_DIR"

if [ "$IS_ARM64" -eq 1 ]; then
    arm64_preflight

    # --system-site-packages makes system PyQt5 visible inside venv
    "$PY_BIN" -m venv --system-site-packages "$VENV_DIR"
    source "$VENV_DIR/bin/activate"

    pip install --quiet --upgrade pip setuptools wheel
    install_pyinstaller
    install_common_deps
    # PyQt5 NOT installed via pip on ARM64 — using system package

else
    # x86_64 — fully isolated, pip wheels available
    "$PY_BIN" -m venv "$VENV_DIR"
    source "$VENV_DIR/bin/activate"

    pip install --quiet --upgrade pip setuptools wheel
    install_pyinstaller
    install_common_deps
    pip install --quiet --force-reinstall "PyQt5==5.15.10"
fi

echo "Build environment ready."

# ─── Clean Previous Builds ────────────────────────────────────────────────
rm -rf "$GUI_DIR/build" \
       "$GUI_DIR/dist" \
       "$GUI_DIR/__pycache__" \
       "$GUI_DIR"/*.spec \
       "$TARGET_BIN" \
       "$TARGET_MGR"

# ─── Hidden imports shared by both binaries ───────────────────────────────
#
# PyInstaller only traces static imports. Lazy imports (inside functions)
# and dynamic plugin loading are invisible to it — we must list them here.
#
#   faiss          — lazy import in rag_engine.py
#   numpy          — used by faiss and sentence_transformers
#   sentence_transformers — lazy import in rag_engine.py
#   pypdf          — lazy import in rag_engine.py (_extract_text)
#   docx           — lazy import in rag_engine.py (_extract_text)
#   requests       — runtime HTTP calls (ollama_client.py, ollama_manager.py)
#   urllib.request — used by ollama_manager.py (install.sh download)
#   pty, select, tty, termios — used by ollama_manager.py (sudo pty auth)
#   threading, tempfile, shutil — standard lib, explicit for safety
#
HIDDEN="
    --hidden-import faiss
    --hidden-import numpy
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
"

# ─── Build GUI (main.py) ──────────────────────────────────────────────────
cd "$GUI_DIR"

echo ""
echo "Building main GUI..."
# shellcheck disable=SC2086
"$PYINSTALLER_BIN" \
    --onefile \
    --windowed \
    --name Ollama-ai-gui \
    --clean \
    $HIDDEN \
    main.py

# ─── Build Manager (ollama_manager.py) ────────────────────────────────────
echo ""
echo "Building model manager..."
# shellcheck disable=SC2086
"$PYINSTALLER_BIN" \
    --onefile \
    --windowed \
    --name Ollama-ai-manager \
    --clean \
    $HIDDEN \
    ollama_manager.py

# ─── Move Binaries To Project Root ────────────────────────────────────────
DIST_GUI="$GUI_DIR/dist/Ollama-ai-gui"
DIST_MGR="$GUI_DIR/dist/Ollama-ai-manager"

if [ ! -f "$DIST_GUI" ]; then
    echo "Build failed. GUI binary not found at: $DIST_GUI"
    deactivate
    exit 1
fi
if [ ! -f "$DIST_MGR" ]; then
    echo "Build failed. Manager binary not found at: $DIST_MGR"
    deactivate
    exit 1
fi

mv "$DIST_GUI" "$TARGET_BIN"
mv "$DIST_MGR" "$TARGET_MGR"
chmod +x "$TARGET_BIN" "$TARGET_MGR"

# ─── Cleanup ──────────────────────────────────────────────────────────────
rm -rf "$GUI_DIR/build" \
       "$GUI_DIR/dist" \
       "$GUI_DIR/__pycache__" \
       "$GUI_DIR"/*.spec \
       "$VENV_DIR"

deactivate 2>/dev/null || true

echo ""
echo "Build complete!"
echo ""
echo "Expected Files:"
echo "  $PROJECT_ROOT/"
echo "      ├── Ollama-ai-gui      (main GUI binary)"
echo "      └── Ollama-ai-manager  (model manager binary)"
echo ""
echo "Both binaries must be in the same folder to work correctly."
