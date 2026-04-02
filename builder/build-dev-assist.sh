#!/usr/bin/env bash
# =========================================================
# build-dev-assist.sh
#
# Builds the dev-assist CLI binary via PyInstaller.
# Creates a fresh venv every build.
# Outputs final binary outside the project directory.
#
# Usage:
#   bash builder/build-dev-assist.sh
#   bash builder/build-dev-assist.sh --clean
# =========================================================

set -euo pipefail
IFS=$'\n\t'

# ── Colors ───────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
info() { echo -e "${BLUE}[→]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
die()  { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

# ── Paths ────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

DEV_ASSIST_DIR="$PROJECT_ROOT/dev-assist"
BUILD_SCRIPT="$DEV_ASSIST_DIR/build.sh"

BINARY_NAME="dev-assist"
DIST_BINARY="$DEV_ASSIST_DIR/dist/$BINARY_NAME"

# Final output config
FINAL_NAME="da"
FINAL_BINARY="$PROJECT_ROOT/$FINAL_NAME"

# ── Validate ─────────────────────────────────────────────
[[ -d "$DEV_ASSIST_DIR" ]] || die "dev-assist/ not found → $DEV_ASSIST_DIR"
[[ -f "$BUILD_SCRIPT"   ]] || die "build.sh not found → $BUILD_SCRIPT"

# ── Clean mode ───────────────────────────────────────────
if [[ "${1:-}" == "--clean" ]]; then
    warn "Cleaning: .venv, build/, dist/"
    rm -rf "$DEV_ASSIST_DIR/.venv" \
           "$DEV_ASSIST_DIR/build" \
           "$DEV_ASSIST_DIR/dist"
    ok "Clean complete"
fi

# ── Reset venv (always fresh build) ──────────────────────
if [[ -d "$DEV_ASSIST_DIR/.venv" ]]; then
    info "Removing stale .venv..."
    rm -rf "$DEV_ASSIST_DIR/.venv"
    ok ".venv removed"
fi

# ── Build ────────────────────────────────────────────────
info "Building binary..."
(
    cd "$DEV_ASSIST_DIR"
    bash build.sh
)

# ── Verify build ─────────────────────────────────────────
[[ -f "$DIST_BINARY" ]] || die "Build failed → $DIST_BINARY not found"

# ── Prepare destination ──────────────────────────────────

if [[ -f "$FINAL_BINARY" ]]; then
    warn "Existing binary found → removing $FINAL_BINARY"
    rm -f "$FINAL_BINARY" || die "Failed to remove old binary"
fi

# ── Move (rename + relocate in one step) ─────────────────
info "Installing binary → $FINAL_BINARY"
mv -f "$DIST_BINARY" "$FINAL_BINARY"
chmod +x "$FINAL_BINARY"

ok "Binary ready: $FINAL_BINARY  ($(du -sh "$FINAL_BINARY" | cut -f1))"

# ── Cleanup build artifacts ──────────────────────────────
info "Cleaning build artifacts..."
rm -rf "$DEV_ASSIST_DIR/build" "$DEV_ASSIST_DIR/dist"
ok "Cleanup complete (.venv preserved)"

# ── Summary ──────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "  ✅ dev-assist build complete"
echo ""
echo "  Binary : $FINAL_BINARY"
echo "  Size   : $(du -sh "$FINAL_BINARY" | cut -f1)"
echo ""
echo "  Run CLI : $FINAL_BINARY"
echo "  Run Web : $FINAL_BINARY --web"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
