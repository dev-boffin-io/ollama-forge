#!/usr/bin/env bash
# =============================================================
#  Ollama_ai — GUI System Dependency Installer
#  Run once as root/sudo before building.
#  Usage: sudo ./builder/install-deps-gui.sh
# =============================================================
set -euo pipefail

# ─── Color helpers ───────────────────────────────────────────
if [ -t 1 ]; then
    RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
    BLUE='\033[0;34m'; BOLD='\033[1m'; RESET='\033[0m'
else
    RED=''; GREEN=''; YELLOW=''; BLUE=''; BOLD=''; RESET=''
fi
info()    { printf "${BLUE}ℹ${RESET}  %s\n" "$1"; }
success() { printf "${GREEN}✅${RESET} %s\n" "$1"; }
warn()    { printf "${YELLOW}⚠${RESET}  %s\n" "$1"; }
die()     { printf "${RED}❌${RESET} %s\n" "$1" >&2; exit 1; }
header()  { printf "\n${BOLD}%s${RESET}\n" "$1"; }

# ─── Root check ──────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    die "This script must be run as root. Try: sudo $0"
fi

# ─── Detect package manager ──────────────────────────────────
detect_pkg_manager() {
    if   command -v apt-get >/dev/null 2>&1; then echo "apt"
    elif command -v dnf     >/dev/null 2>&1; then echo "dnf"
    elif command -v yum     >/dev/null 2>&1; then echo "yum"
    elif command -v pacman  >/dev/null 2>&1; then echo "pacman"
    elif command -v zypper  >/dev/null 2>&1; then echo "zypper"
    elif command -v apk     >/dev/null 2>&1; then echo "apk"
    else echo "unknown"
    fi
}

# ─── Install ─────────────────────────────────────────────────
PKG_MGR="$(detect_pkg_manager)"
header "Ollama_ai — GUI Dependency Installer ($PKG_MGR)"

case "$PKG_MGR" in
    apt)
        info "Using apt (Debian / Ubuntu / Mint / PRoot)"
        apt-get update -qq
        apt-get install -y \
            python3-pyqt5 \
            libgl1-mesa-dev \
            libxcb-xinerama0
        # pyqt5-dev-tools is optional — not needed for runtime or build
        apt-get install -y pyqt5-dev-tools 2>/dev/null \
            || warn "pyqt5-dev-tools not available — skipping (not required)"
        ;;
    dnf|yum)
        info "Using $PKG_MGR (Fedora / RHEL / AlmaLinux)"
        "$PKG_MGR" install -y \
            python3-qt5 \
            mesa-libGL-devel \
            libxcb
        # python3-qt5-devel optional
        "$PKG_MGR" install -y python3-qt5-devel 2>/dev/null \
            || warn "python3-qt5-devel not available — skipping (not required)"
        ;;
    pacman)
        info "Using pacman (Arch / Manjaro)"
        pacman -Sy --noconfirm \
            python-pyqt5 \
            mesa \
            libxcb
        ;;
    zypper)
        info "Using zypper (openSUSE)"
        zypper --non-interactive install \
            python3-qt5 \
            libGL-devel \
            libxcb-devel
        ;;
    apk)
        info "Using apk (Alpine)"
        apk add --no-cache \
            py3-pyqt5 \
            mesa-gl \
            libxcb
        ;;
    *)
        die "Unsupported package manager. Install manually: python3-pyqt5  libGL  libxcb-xinerama"
        ;;
esac

# ─── Verify ──────────────────────────────────────────────────
header "Verifying..."

PY_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PY_BIN="$(command -v "$candidate")"
        break
    fi
done

if [ -z "${PY_BIN:-}" ]; then
    warn "No Python interpreter found — cannot verify PyQt5"
else
    if "$PY_BIN" -c "from PyQt5 import QtCore; print('PyQt5', QtCore.PYQT_VERSION_STR)" 2>/dev/null; then
        success "PyQt5 OK"
    else
        warn "PyQt5 import failed — check installation above"
        exit 1
    fi
fi

header "Done!"
success "GUI dependencies installed."
printf "\n${BOLD}Next step:${RESET} ${BLUE}./builder/build-gui-bin.sh${RESET}\n\n"
