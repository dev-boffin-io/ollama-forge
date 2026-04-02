#!/usr/bin/env bash
# =========================================================
# Ollama Forge Installer
#
# Commands:
#   ./install.sh              → install GUI + Manager + CLI + dev-assist (user)
#   sudo ./install.sh         → system-wide install
#   ./install.sh build        → build all + install
#   ./install.sh da-install   → install dev-assist CLI only
#   ./install.sh da-remove    → remove dev-assist CLI only
#   ./install.sh remove       → uninstall everything
# =========================================================
set -euo pipefail
IFS=$'\n\t'

# ---------------------------------------------------------
# App info
# ---------------------------------------------------------
APP_NAME="Ollama-ai-gui"
APP_TITLE="Ollama AI"
APP_COMMENT="Manage & Chat With LLM Models"
CLI_NAME="ollama-main"
MGR_NAME="Ollama-ai-manager"
DA_NAME="dev-assist"

# ---------------------------------------------------------
# Paths
# ---------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILDER_DIR="$SCRIPT_DIR/builder"

GUI_BIN="$SCRIPT_DIR/$APP_NAME"
MGR_BIN="$SCRIPT_DIR/$MGR_NAME"
CLI_SOURCE="$SCRIPT_DIR/$CLI_NAME"
DA_SOURCE="$SCRIPT_DIR/$DA_NAME"
ICON="$SCRIPT_DIR/ollama-forge.png"

BUILD_GUI="$BUILDER_DIR/build-gui-bin.sh"
BUILD_MAIN="$BUILDER_DIR/build-main.sh"
BUILD_DA="$BUILDER_DIR/build-dev-assist.sh"

# ---------------------------------------------------------
# Install mode (user vs system)
# ---------------------------------------------------------
if [[ $EUID -eq 0 ]]; then
    MODE="system"
    DESKTOP_DIR="/usr/local/share/applications"
    BIN_DIR="/usr/local/bin"
else
    MODE="user"
    DESKTOP_DIR="$HOME/.local/share/applications"
    BIN_DIR="$HOME/.local/bin"
fi

CLI_TARGET="$BIN_DIR/$CLI_NAME"
DA_TARGET="$BIN_DIR/$DA_NAME"
DESKTOP_FILE="$DESKTOP_DIR/$APP_NAME.desktop"

# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------
info()  { echo "➜ $*"; }
ok()    { echo "✔ $*"; }
warn()  { echo "! $*"; }
error() { echo "✖ $*" >&2; exit 1; }

# ---------------------------------------------------------
# Build helpers
# ---------------------------------------------------------
_run_as_user() {
    local script="$1"
    if [[ -n "${SUDO_USER:-}" ]]; then
        sudo -u "$SUDO_USER" bash "$script"
    else
        bash "$script"
    fi
}

run_build_gui() {
    [[ -f "$BUILD_GUI"  ]] || error "Missing: $BUILD_GUI"
    [[ -f "$BUILD_MAIN" ]] || error "Missing: $BUILD_MAIN"
    chmod +x "$BUILD_GUI" "$BUILD_MAIN"
    info "Building GUI + Manager..."
    _run_as_user "$BUILD_GUI"
    info "Building CLI (ollama-main)..."
    _run_as_user "$BUILD_MAIN"
    ok "GUI + CLI build complete"
}

run_build_da() {
    [[ -f "$BUILD_DA" ]] || error "Missing: $BUILD_DA"
    chmod +x "$BUILD_DA"
    info "Building dev-assist..."
    _run_as_user "$BUILD_DA"
    ok "dev-assist build complete"
}

# ---------------------------------------------------------
# Validation
# ---------------------------------------------------------
check_gui_files() {
    [[ -f "$GUI_BIN" && -x "$GUI_BIN" ]] || error "Missing/non-executable GUI binary: $GUI_BIN
  Run: make build   or   ./install.sh build"
    [[ -f "$MGR_BIN" && -x "$MGR_BIN" ]] || error "Missing/non-executable Manager binary: $MGR_BIN
  Both binaries must be in the same directory."
    [[ -f "$ICON" ]] || error "Missing icon: $ICON"
}

check_da_file() {
    [[ -f "$DA_SOURCE" && -x "$DA_SOURCE" ]] || error "Missing/non-executable dev-assist binary: $DA_SOURCE
  Run: make da-build   or   bash builder/build-dev-assist.sh"
}

# ---------------------------------------------------------
# Desktop entry
# ---------------------------------------------------------
install_desktop() {
    info "Installing desktop entry ($MODE)"
    mkdir -p "$DESKTOP_DIR"
    cat > "$DESKTOP_FILE" << EOF
[Desktop Entry]
Version=1.0
Name=$APP_TITLE
Comment=$APP_COMMENT
Exec=$GUI_BIN
Icon=$ICON
Terminal=false
Type=Application
Categories=Development;IDE;
StartupNotify=true
EOF
    chmod 644 "$DESKTOP_FILE"
    command -v update-desktop-database &>/dev/null && \
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    ok "Desktop entry: $DESKTOP_FILE"
}

remove_desktop() {
    if [[ -f "$DESKTOP_FILE" ]]; then
        rm -f "$DESKTOP_FILE"
        command -v update-desktop-database &>/dev/null && \
            update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
        ok "Desktop entry removed"
    else
        warn "Desktop entry not found (already removed?)"
    fi
}

# ---------------------------------------------------------
# CLI symlinks
# ---------------------------------------------------------
install_cli() {
    [[ -f "$CLI_SOURCE" ]] || { warn "ollama-main binary not found — skipping CLI symlink"; return; }
    mkdir -p "$BIN_DIR"
    ln -sf "$CLI_SOURCE" "$CLI_TARGET"
    ok "ollama-main → $CLI_TARGET"
}

remove_cli() {
    if [[ -f "$CLI_TARGET" || -L "$CLI_TARGET" ]]; then
        rm -f "$CLI_TARGET"
        ok "ollama-main symlink removed"
    fi
}

install_da_cli() {
    check_da_file
    mkdir -p "$BIN_DIR"
    ln -sf "$DA_SOURCE" "$DA_TARGET"
    ok "dev-assist → $DA_TARGET"
}

remove_da_cli() {
    if [[ -f "$DA_TARGET" || -L "$DA_TARGET" ]]; then
        rm -f "$DA_TARGET"
        ok "dev-assist symlink removed"
    else
        warn "dev-assist symlink not found (already removed?)"
    fi
}

# ---------------------------------------------------------
# Full install / uninstall
# ---------------------------------------------------------
install_all() {
    check_gui_files
    info "Install mode: $MODE"

    install_desktop
    install_cli

    # dev-assist is optional — install if binary exists
    if [[ -f "$DA_SOURCE" && -x "$DA_SOURCE" ]]; then
        install_da_cli
    else
        warn "dev-assist binary not found — skipping (run: make da-build)"
    fi

    echo ""
    ok "Installation complete ($MODE)"
    echo ""
    echo "  GUI      : $GUI_BIN"
    echo "  Manager  : $MGR_BIN  (launched automatically by GUI)"
    echo "  CLI      : $CLI_TARGET"
    [[ -L "$DA_TARGET" ]] && echo "  AI CLI   : $DA_TARGET  (dev-assist --web for browser UI)"
    echo ""
    echo "  Both GUI binaries must stay in the same folder."
}

remove_all() {
    info "Removing installation ($MODE)"
    remove_desktop
    remove_cli
    remove_da_cli
    echo ""
    ok "Uninstall complete"
}

# ---------------------------------------------------------
# Entry point
# ---------------------------------------------------------
case "${1:-}" in

    build)
        run_build_gui
        run_build_da
        install_all
        ;;

    build-gui)
        run_build_gui
        check_gui_files
        install_desktop
        install_cli
        ok "GUI build + install complete"
        ;;

    build-da | build-dev-assist)
        run_build_da
        install_da_cli
        ok "dev-assist build + install complete"
        ;;

    da-install)
        install_da_cli
        echo ""
        ok "dev-assist installed → $DA_TARGET"
        echo "  Run CLI : dev-assist"
        echo "  Run Web : dev-assist --web"
        ;;

    da-remove)
        remove_da_cli
        ;;

    remove)
        remove_all
        ;;

    "")
        install_all
        ;;

    *)
        echo ""
        echo "Usage:"
        echo "  ./install.sh              → install all (binaries must exist)"
        echo "  ./install.sh build        → build all + install"
        echo "  ./install.sh build-gui    → build GUI/CLI + install"
        echo "  ./install.sh build-da     → build dev-assist + install"
        echo "  ./install.sh da-install   → install dev-assist CLI only"
        echo "  ./install.sh da-remove    → remove dev-assist CLI only"
        echo "  ./install.sh remove       → uninstall everything"
        echo "  sudo ./install.sh         → system-wide install"
        echo ""
        exit 1
        ;;
esac
