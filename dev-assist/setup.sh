#!/bin/bash
# dev-assist Setup Script
# Supports: Termux, Debian/Ubuntu, Arch, Fedora/RHEL, Alpine, openSUSE, macOS
# Uses an isolated Python venv — system Python stays clean.
# Run: bash setup.sh

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
info() { echo -e "${BLUE}[→]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
die()  { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

echo -e "${BLUE}"
echo "  ██████╗ ███████╗██╗   ██╗      █████╗ ███████╗███████╗██╗███████╗████████╗"
echo "  Setup Script"
echo -e "${NC}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Detect platform ───────────────────────────────────────────────
detect_platform() {
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
ok "Platform detected: $PLATFORM"

# ── Find system Python ────────────────────────────────────────────
SYS_PYTHON=""
for candidate in python3 python3.13 python3.12 python3.11 python3.10 python3.9 python; do
    if command -v "$candidate" &>/dev/null; then
        SYS_PYTHON="$candidate"
        break
    fi
done

if [ -z "$SYS_PYTHON" ]; then
    warn "Python3 not found — installing..."
    case $PLATFORM in
        termux)  pkg install python -y ;;
        alpine)  apk add --no-cache python3 py3-pip ;;
        arch)    pacman -S --noconfirm python python-pip ;;
        fedora)  dnf install -y python3 python3-pip ;;
        debian)  apt-get install -y python3 python3-venv python3-full ;;
        suse)    zypper install -y python3 python3-pip ;;
        macos)   brew install python3 ;;
        *)       die "Please install Python 3.9+ manually" ;;
    esac
    SYS_PYTHON=$(command -v python3 || command -v python)
fi

ok "System Python: $($SYS_PYTHON --version)"

# ── Ensure venv support ───────────────────────────────────────────
if ! "$SYS_PYTHON" -m venv --help &>/dev/null 2>&1; then
    warn "venv module missing — installing..."
    case $PLATFORM in
        debian) apt-get install -y python3-venv python3-full ;;
        fedora) dnf install -y python3 ;;
        alpine) apk add --no-cache py3-virtualenv ;;
        arch)   pacman -S --noconfirm python ;;
        *)      warn "Please ensure python3-venv is installed." ;;
    esac
fi

# ── Create isolated venv ──────────────────────────────────────────
VENV_DIR="$SCRIPT_DIR/.venv"
info "Creating isolated Python venv: $VENV_DIR"
"$SYS_PYTHON" -m venv "$VENV_DIR" || die "Failed to create venv."
ok "venv created."

PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"

# Upgrade pip in venv
"$PYTHON" -m pip install --upgrade pip --quiet

# ── Install Ollama CLI ────────────────────────────────────────────
install_ollama() {
    info "Installing Ollama..."
    case $PLATFORM in
        termux)
            echo "  Termux: Ollama ARM64 native support is limited."
            echo "  Recommended: use API mode — set 'ai_engine': 'api' in config/settings.json"
            ;;
        alpine)
            ARCH=$(uname -m)
            case $ARCH in x86_64) OL_ARCH="amd64" ;; aarch64) OL_ARCH="arm64" ;; *) OL_ARCH="amd64" ;; esac
            curl -fsSL "https://ollama.com/download/ollama-linux-${OL_ARCH}" \
                -o /usr/local/bin/ollama && chmod +x /usr/local/bin/ollama
            ;;
        arch)
            if command -v yay &>/dev/null; then yay -S --noconfirm ollama
            else curl -fsSL https://ollama.com/install.sh | sh; fi
            ;;
        debian|fedora|suse|linux)
            curl -fsSL https://ollama.com/install.sh | sh ;;
        macos)
            brew install ollama || curl -fsSL https://ollama.com/install.sh | sh ;;
    esac
}

read -p "Install Ollama (local AI server)? [y/N]: " INSTALL_OLLAMA
if [[ "$INSTALL_OLLAMA" =~ ^[Yy]$ ]]; then
    install_ollama
fi

# ── Install Python dependencies into venv ────────────────────────
info "Installing Python dependencies into venv..."

PKGS=(
    "ollama"
    "pydantic>=2.0"
    "bcrypt>=4.0"
    "rich>=13.0"
    "prompt-toolkit>=3.0"
    "jinja2>=3.1"
    "chainlit>=1.0"
)

for pkg in "${PKGS[@]}"; do
    "$PIP" install "$pkg" --quiet && ok "  $pkg" || warn "  $pkg (failed — skipping)"
done

# ── Make executable ───────────────────────────────────────────────
chmod +x main.py

# ── Create launcher script ────────────────────────────────────────
# Launcher uses the venv Python so all packages are available
LAUNCHER="$SCRIPT_DIR/dev-assist.sh"
cat > "$LAUNCHER" << LAUNCHER_EOF
#!/usr/bin/env bash
# dev-assist launcher — uses isolated venv
SCRIPT_DIR="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
exec "\$SCRIPT_DIR/.venv/bin/python" "\$SCRIPT_DIR/main.py" "\$@"
LAUNCHER_EOF
chmod +x "$LAUNCHER"
ok "Launcher created: $LAUNCHER"

# ── Alias setup ───────────────────────────────────────────────────
echo ""
read -p "Add 'dev-assist' alias to shell? [y/N]: " ADD_ALIAS
if [[ "$ADD_ALIAS" =~ ^[Yy]$ ]]; then
    SHELL_RC="$HOME/.bashrc"
    [[ "${SHELL:-}" == *"zsh"*  ]] && SHELL_RC="$HOME/.zshrc"
    [[ "${SHELL:-}" == *"fish"* ]] && SHELL_RC="$HOME/.config/fish/config.fish"
    [[ "$PLATFORM"  == "termux" ]] && SHELL_RC="$HOME/.bashrc"

    ALIAS_CMD="alias dev-assist='bash $SCRIPT_DIR/dev-assist.sh'"
    echo "" >> "$SHELL_RC"
    echo "# dev-assist" >> "$SHELL_RC"
    echo "$ALIAS_CMD" >> "$SHELL_RC"
    ok "Alias added to $SHELL_RC"
    echo "  Run: source $SHELL_RC  — then type: dev-assist"
fi

# ── Ollama model pull ─────────────────────────────────────────────
if command -v ollama &>/dev/null; then
    echo ""
    read -p "Pull AI model (qwen2.5-coder:7b, ~4GB)? [y/N]: " PULL_MODEL
    if [[ "$PULL_MODEL" =~ ^[Yy]$ ]]; then
        info "Pulling model (this may take a while)..."
        ollama pull qwen2.5-coder:7b
        ok "Model ready!"
    fi
fi

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ Setup complete!"
echo ""
echo "  Platform : $PLATFORM"
echo "  venv     : $VENV_DIR"
echo ""
echo "  Run (source):  bash dev-assist.sh"
echo "  Run (binary):  bash build.sh && ./dist/dev-assist"
echo "  Or (alias):    dev-assist   (after reloading shell)"
echo -e "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
