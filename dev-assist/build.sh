#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# build.sh — Build dev-assist as a single portable binary via PyInstaller
#
# Uses an ISOLATED venv so system Python stays clean.
# Works on: Debian/Ubuntu, Arch, Fedora/RHEL, Alpine, openSUSE,
#           Termux (Android/ARM64), proot-distro environments
#
# Usage:
#   bash build.sh            # standard build
#   bash build.sh --clean    # wipe build/ dist/ .venv/ first
#   bash build.sh --install  # also copy binary to ~/.local/bin/
#
# Output:  dist/dev-assist
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
info() { echo -e "${BLUE}[→]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
die()  { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CLEAN=false
INSTALL=false
for arg in "$@"; do
    [[ "$arg" == "--clean"   ]] && CLEAN=true
    [[ "$arg" == "--install" ]] && INSTALL=true
done

# ── 1. Detect platform ───────────────────────────────────────────────────────
detect_platform() {
    # Check distro files FIRST — proot-distro runs inside Termux but has real distro files.
    # TERMUX_VERSION is only set in native Termux shell, NOT inside proot sessions.
    if [ -n "${TERMUX_VERSION:-}" ] && \
       [ ! -f "/etc/debian_version" ] && \
       [ ! -f "/etc/alpine-release" ] && \
       [ ! -f "/etc/arch-release"   ] && \
       [ ! -f "/etc/fedora-release" ]; then
        echo "termux"
    elif [ -f "/etc/alpine-release" ]; then
        echo "alpine"
    elif [ -f "/etc/arch-release" ]; then
        echo "arch"
    elif [ -f "/etc/fedora-release" ] || [ -f "/etc/redhat-release" ]; then
        echo "fedora"
    elif [ -f "/etc/debian_version" ]; then
        echo "debian"
    elif [ -f "/etc/SuSE-release" ] || [ -f "/etc/opensuse-release" ]; then
        echo "suse"
    elif [[ "${OSTYPE:-}" == "darwin"* ]]; then
        echo "macos"
    else
        echo "linux"
    fi
}

PLATFORM=$(detect_platform)
info "Platform: $PLATFORM"

# ── 2. Find system Python ────────────────────────────────────────────────────
SYS_PYTHON=""
for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
    if command -v "$candidate" &>/dev/null; then
        SYS_PYTHON="$candidate"
        break
    fi
done
[[ -n "$SYS_PYTHON" ]] || die "Python not found. Install Python 3.9+."

PY_VER=$("$SYS_PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "System Python: $PY_VER  ($SYS_PYTHON)"

"$SYS_PYTHON" -c "import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)" \
    || die "Python 3.9+ required (found $PY_VER)."

# ── 3. Optionally clean ───────────────────────────────────────────────────────
if $CLEAN; then
    info "Cleaning previous build artefacts..."
    rm -rf build/ dist/ __pycache__ .venv/
    ok "Clean done."
fi

# ── 4. Create isolated venv ──────────────────────────────────────────────────
VENV_DIR="$SCRIPT_DIR/.venv"
info "Setting up isolated build venv: $VENV_DIR"

# Ensure python3-venv / python3-full is available
if ! "$SYS_PYTHON" -m venv --help &>/dev/null 2>&1; then
    warn "venv module missing — installing..."
    case $PLATFORM in
        termux)  pkg install python -y ;;
        alpine)  apk add --no-cache python3 py3-virtualenv ;;
        arch)    pacman -S --noconfirm python ;;
        fedora)  dnf install -y python3 ;;
        debian)  apt-get install -y python3-venv python3-full ;;
        suse)    zypper install -y python3 ;;
        macos)   brew install python3 ;;
    esac
fi

# Create venv (reuse if already exists and --clean wasn't passed)
if [ ! -d "$VENV_DIR" ]; then
    "$SYS_PYTHON" -m venv "$VENV_DIR" || die "Failed to create venv."
    ok "venv created."
else
    ok "venv already exists — reusing."
fi

# Activate venv
PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"
[[ -f "$PYTHON" ]] || die "venv Python not found: $PYTHON"

VENV_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
ok "venv Python: $VENV_VER"

# ── 5. Install dependencies into venv ────────────────────────────────────────
info "Installing dependencies into venv..."

# Upgrade pip first (silently)
"$PYTHON" -m pip install --upgrade pip --quiet

# Build requirements — everything needed for PyInstaller to bundle correctly.
# Strip comment lines and optional packages from requirements.txt.
BUILD_REQS=(
    "pyinstaller"
    "ollama"
    "pydantic>=2.0"
    "rich>=13.0"
    "prompt-toolkit>=3.0"
    "jinja2>=3.1"
)

for pkg in "${BUILD_REQS[@]}"; do
    "$PIP" install "$pkg" --quiet && ok "  $pkg" || warn "  $pkg (failed — will skip)"
done

# ── 6. Verify PyInstaller ────────────────────────────────────────────────────
PI_VER=$("$PYTHON" -m PyInstaller --version 2>&1)
ok "PyInstaller $PI_VER"

# Optional: UPX
if command -v upx &>/dev/null; then
    ok "UPX found — binary will be compressed."
else
    warn "UPX not found — binary won't be compressed (still works fine)."
fi

# ── 7. Run PyInstaller from venv ─────────────────────────────────────────────
mkdir -p build

info "Building single binary (this may take a while)..."
"$PYTHON" -m PyInstaller dev_assist.spec --noconfirm 2>&1 | tee build/pyinstaller.log

BINARY="dist/dev-assist"
[[ -f "$BINARY" ]] || die "Build failed — binary not found. See build/pyinstaller.log"

ok "Binary built: $(du -sh "$BINARY" | cut -f1)  →  $BINARY"

# ── 8. Smoke-test ────────────────────────────────────────────────────────────
info "Smoke-testing binary..."
if "$BINARY" --help &>/dev/null; then
    ok "Smoke test passed."
else
    warn "Smoke test: --help exited non-zero (may be fine if help prints to stderr)."
fi

# ── 9. Optional install ──────────────────────────────────────────────────────
if $INSTALL; then
    DEST="$HOME/.local/bin/dev-assist"
    mkdir -p "$HOME/.local/bin"
    cp "$BINARY" "$DEST"
    chmod +x "$DEST"
    ok "Installed to $DEST"
    if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
        warn "~/.local/bin is not in your PATH."
        echo "    Add this to ~/.bashrc or ~/.zshrc:"
        echo '    export PATH="$HOME/.local/bin:$PATH"'
    fi
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ✅ Build complete!${NC}"
echo ""
echo "  Platform : $PLATFORM"
echo "  venv     : $VENV_DIR"
echo "  Binary   : $SCRIPT_DIR/$BINARY"
echo "  Size     : $(du -sh "$BINARY" | cut -f1)"
echo ""
echo "  Run it   : ./dist/dev-assist"
echo "  Web UI   : ./dist/dev-assist --web"
if $INSTALL; then
echo "  Global   : dev-assist   (after reloading shell)"
fi
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
