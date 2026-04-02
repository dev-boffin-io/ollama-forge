#!/usr/bin/env bash
# =========================================================
# build-dev-assist.sh
#
# Builds the dev-assist CLI binary via PyInstaller.
# Keeps dev-assist/.venv intact between builds.
# Moves the finished binary to the project root.
#
# Usage:
#   bash builder/build-dev-assist.sh
#   bash builder/build-dev-assist.sh --clean   # wipe .venv too
# =========================================================
set -euo pipefail
IFS=$'\n\t'

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
DEST_BINARY="$PROJECT_ROOT/$BINARY_NAME"

# ── Validate ─────────────────────────────────────────────
[[ -d "$DEV_ASSIST_DIR" ]] || die "dev-assist/ directory not found at: $DEV_ASSIST_DIR"
[[ -f "$BUILD_SCRIPT"   ]] || die "build.sh not found at: $BUILD_SCRIPT"

# ── Clean mode ───────────────────────────────────────────
if [[ "${1:-}" == "--clean" ]]; then
    warn "Clean mode: removing dev-assist/.venv, build/, dist/"
    rm -rf "$DEV_ASSIST_DIR/.venv" \
           "$DEV_ASSIST_DIR/build" \
           "$DEV_ASSIST_DIR/dist"
    ok "Cleaned."
fi

# ── Build ────────────────────────────────────────────────
info "Building dev-assist binary..."
info "Source: $DEV_ASSIST_DIR"

cd "$DEV_ASSIST_DIR"
bash build.sh
cd "$PROJECT_ROOT"

# ── Verify ───────────────────────────────────────────────
[[ -f "$DIST_BINARY" ]] || die "Build failed — binary not found at $DIST_BINARY"

# ── Move binary to project root ──────────────────────────
info "Moving binary to project root..."
mv -f "$DIST_BINARY" "$DEST_BINARY"
chmod +x "$DEST_BINARY"
ok "Binary ready: $DEST_BINARY  ($(du -sh "$DEST_BINARY" | cut -f1))"

# ── Clean build artifacts (keep .venv) ───────────────────
info "Cleaning build artifacts (keeping .venv)..."
rm -rf "$DEV_ASSIST_DIR/build"
rm -rf "$DEV_ASSIST_DIR/dist"
ok "Build dirs removed. .venv preserved for faster rebuilds."

# ── Summary ──────────────────────────────────────────────
echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo "  ✅ dev-assist build complete"
echo ""
echo "  Binary : $DEST_BINARY"
echo "  Size   : $(du -sh "$DEST_BINARY" | cut -f1)"
echo ""
echo "  Run CLI : ./dev-assist"
echo "  Run Web : ./dev-assist --web"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
