#!/usr/bin/env bash
# =========================================================
# Ollama Forge Installer
#
# Commands:
#   ./install.sh build        → build + install
#   ./install.sh              → user install
#   sudo ./install.sh         → system install
#   ./install.sh remove       → user uninstall
#   sudo ./install.sh remove  → system uninstall
# =========================================================

set -Eeuo pipefail
IFS=$'\n\t'

# ---------------------------------------------------------
# App Info
# ---------------------------------------------------------

APP_NAME="Ollama-ai-gui"
APP_TITLE="Ollama AI"
APP_COMMENT="Manage & Chat With LLM Models"
CLI_NAME="ollama-main"

# ---------------------------------------------------------
# Paths
# ---------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILDER_DIR="$SCRIPT_DIR/builder"

GUI_BIN="$SCRIPT_DIR/$APP_NAME"
CLI_SOURCE="$SCRIPT_DIR/$CLI_NAME"
ICON="$SCRIPT_DIR/ollama-forge.png"

BUILD_GUI="$BUILDER_DIR/build-gui-bin.sh"
BUILD_MAIN="$BUILDER_DIR/build-main.sh"

# ---------------------------------------------------------
# Install Mode
# ---------------------------------------------------------

if [[ $EUID -eq 0 ]]; then
    MODE="system"
    DESKTOP_DIR="/usr/local/share/applications"
    CLI_TARGET="/usr/local/bin/$CLI_NAME"
else
    MODE="user"
    DESKTOP_DIR="$HOME/.local/share/applications"
    CLI_TARGET="$HOME/.local/bin/$CLI_NAME"
fi

DESKTOP_FILE="$DESKTOP_DIR/$APP_NAME.desktop"

# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

info()  { echo "➜ $*"; }
error() { echo "✖ $*" >&2; exit 1; }

# ---------------------------------------------------------
# Build System
# ---------------------------------------------------------

run_build() {

    info "Starting build process"

    [[ -f "$BUILD_GUI" ]]  || error "Missing build script: $BUILD_GUI"
    [[ -f "$BUILD_MAIN" ]] || error "Missing build script: $BUILD_MAIN"

    chmod +x "$BUILD_GUI" "$BUILD_MAIN"

    if [[ -n "${SUDO_USER:-}" ]]; then
        info "Running build as user: $SUDO_USER"
        sudo -u "$SUDO_USER" bash "$BUILD_GUI"
        sudo -u "$SUDO_USER" bash "$BUILD_MAIN"
    else
        bash "$BUILD_GUI"
        bash "$BUILD_MAIN"
    fi

    info "✔ Build complete"
}

# ---------------------------------------------------------
# Validation
# ---------------------------------------------------------

check_files() {

    [[ -f "$GUI_BIN" ]] || error "Missing GUI binary: $GUI_BIN"
    [[ -x "$GUI_BIN" ]] || error "GUI binary not executable"
    [[ -f "$ICON" ]]    || error "Missing icon: $ICON"
}

# ---------------------------------------------------------
# Desktop Entry
# ---------------------------------------------------------

install_desktop() {

    info "Installing desktop entry"

    mkdir -p "$DESKTOP_DIR"

    cat > "$DESKTOP_FILE" <<EOF
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

    if command -v update-desktop-database >/dev/null; then
        update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    fi

    info "Desktop entry installed"
}

remove_desktop() {

    if [[ -f "$DESKTOP_FILE" ]]; then
        rm -f "$DESKTOP_FILE"

        command -v update-desktop-database >/dev/null && \
            update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

        info "Desktop entry removed"
    else
        info "Desktop entry not found"
    fi
}

# ---------------------------------------------------------
# CLI
# ---------------------------------------------------------

install_cli() {

    mkdir -p "$(dirname "$CLI_TARGET")"

    rm -f "$CLI_TARGET"
    ln -s "$CLI_SOURCE" "$CLI_TARGET"

    info "CLI installed → $CLI_TARGET"
}

remove_cli() {

    if [[ -f "$CLI_TARGET" || -L "$CLI_TARGET" ]]; then
        rm -f "$CLI_TARGET"
        info "CLI removed"
    fi
}

# ---------------------------------------------------------
# Install
# ---------------------------------------------------------

install_all() {

    check_files

    info "Install mode: $MODE"

    install_desktop
    install_cli

    info "✔ Installation complete"
}

# ---------------------------------------------------------
# Uninstall
# ---------------------------------------------------------

remove_all() {

    info "Removing installation"

    remove_desktop
    remove_cli

    info "✔ Uninstall complete"
}

# ---------------------------------------------------------
# CLI Interface
# ---------------------------------------------------------

case "${1:-}" in

    build)
        run_build
        install_all
        ;;

    remove)
        remove_all
        ;;

    "")
        install_all
        ;;

    *)
        echo
        echo "Usage:"
        echo "  ./install.sh build"
        echo "  ./install.sh"
        echo "  ./install.sh remove"
        echo
        exit 1
        ;;

esac
