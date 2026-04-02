#!/usr/bin/env bash
# =============================================================
# ollama-main Build Script
# Usage: ./builder/build-main.sh
# =============================================================

set -euo pipefail

# ─────────────────────────────────────────────────────────────
# Resolve paths
# ─────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

SRC_FILE="$PROJECT_ROOT/ollama-main.py"
DIST_BIN="$PROJECT_ROOT/dist/ollama-main"
TARGET_BIN="$PROJECT_ROOT/ollama-main"

VENV_DIR="$SCRIPT_DIR/.venv-build-main"
PYINSTALLER_BIN=""

echo "Building ollama-main installer..."
echo ""

# ─────────────────────────────────────────────────────────────
# Detect Python
# ─────────────────────────────────────────────────────────────

PY_BIN=""
for p in python3 python; do
    if command -v "$p" >/dev/null 2>&1; then
        PY_BIN="$(command -v "$p")"
        break
    fi
done

if [ -z "$PY_BIN" ]; then
    echo "ERROR: Python not found."
    exit 1
fi

echo "Python : $PY_BIN ($($PY_BIN --version 2>&1))"

# ─────────────────────────────────────────────────────────────
# Check source
# ─────────────────────────────────────────────────────────────

if [ ! -f "$SRC_FILE" ]; then
    echo "ERROR: Source file not found: $SRC_FILE"
    exit 1
fi

# ─────────────────────────────────────────────────────────────
# Architecture detection
# ─────────────────────────────────────────────────────────────

ARCH="$(uname -m)"
echo "Architecture : $ARCH"

# ─────────────────────────────────────────────────────────────
# Install deps + PyInstaller
# ─────────────────────────────────────────────────────────────

install_pyinstaller() {
    echo "Installing dependencies..."

    pip install --quiet --upgrade pip

    # requests + packaging — used by ollama-main.py at runtime
    pip install --quiet \
        requests \
        packaging

    pip install --quiet \
        --ignore-installed \
        pyinstaller \
        pyinstaller-hooks-contrib

    # Force-reinstall setuptools AFTER pyinstaller so its dist-info lands in
    # the venv's own site-packages. On ARM64 (--system-site-packages), the
    # system setuptools is visible to Python but NOT to PyInstaller's isolated
    # child process which only scans the venv → version=None → TypeError.
    pip install --quiet --force-reinstall setuptools

    PYINSTALLER_BIN="$VENV_DIR/bin/pyinstaller"

    if [ ! -x "$PYINSTALLER_BIN" ]; then
        echo "ERROR: PyInstaller not installed correctly."
        exit 1
    fi

    echo "PyInstaller : $("$PYINSTALLER_BIN" --version)"
}

# ─────────────────────────────────────────────────────────────
# Setup virtual environment
# ─────────────────────────────────────────────────────────────

echo ""
echo "Setting up build environment..."

rm -rf "$VENV_DIR"

if [ "$ARCH" = "aarch64" ]; then
    "$PY_BIN" -m venv --system-site-packages "$VENV_DIR"
else
    "$PY_BIN" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

install_pyinstaller

echo "Environment ready."
echo ""

# ─────────────────────────────────────────────────────────────
# Clean old builds
# ─────────────────────────────────────────────────────────────

rm -rf \
    "$PROJECT_ROOT/build" \
    "$PROJECT_ROOT/dist" \
    "$PROJECT_ROOT/__pycache__" \
    "$PROJECT_ROOT"/*.spec \
    "$TARGET_BIN"

# ─────────────────────────────────────────────────────────────
# Build binary
# ─────────────────────────────────────────────────────────────

cd "$PROJECT_ROOT"

"$PYINSTALLER_BIN" \
    --onefile \
    --name ollama-main \
    --clean \
    --hidden-import requests \
    --hidden-import packaging \
    --hidden-import packaging.version \
    ollama-main.py

# ─────────────────────────────────────────────────────────────
# Validate build
# ─────────────────────────────────────────────────────────────

if [ ! -f "$DIST_BIN" ]; then
    echo "ERROR: Build failed. Binary not found."
    deactivate
    exit 1
fi

mv "$DIST_BIN" "$TARGET_BIN"
chmod +x "$TARGET_BIN"

# ─────────────────────────────────────────────────────────────
# Cleanup
# ─────────────────────────────────────────────────────────────

rm -rf \
    "$PROJECT_ROOT/build" \
    "$PROJECT_ROOT/dist" \
    "$PROJECT_ROOT/__pycache__" \
    "$PROJECT_ROOT"/*.spec \
    "$VENV_DIR"

deactivate 2>/dev/null || true

# ─────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────

echo ""
echo "Build complete!"
echo "Binary : $TARGET_BIN"
echo ""
echo "Expected layout:"
echo "  $PROJECT_ROOT/"
echo "  └── ollama-main"
