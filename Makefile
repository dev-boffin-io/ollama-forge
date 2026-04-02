# =========================================================
# Ollama Forge — Makefile
# =========================================================

APP_GUI  = Ollama-ai-gui
APP_MGR  = Ollama-ai-manager
CLI_BIN  = ollama-main
DA_BIN   = dev-assist

BUILDER_DIR    = builder
BUILD_GUI      = $(BUILDER_DIR)/build-gui-bin.sh
BUILD_MAIN     = $(BUILDER_DIR)/build-main.sh
INSTALL_SCRIPT = install.sh

include dev-assist.mk

.PHONY: help build build-all install install-all uninstall rebuild clean

# ---------------------------------------------------------
# Help
# ---------------------------------------------------------
help:
	@echo ""
	@echo "  Ollama Forge — Build System"
	@echo ""
	@echo "  ── GUI + CLI ────────────────────────────────────────────"
	@echo "  make build          build GUI, Manager, ollama-main"
	@echo "  make install        install GUI (builds first if needed)"
	@echo "  make uninstall      remove desktop entry and symlinks"
	@echo "  make rebuild        clean then build GUI"
	@echo "  make clean          remove GUI binaries"
	@echo ""
	@echo "  ── dev-assist ───────────────────────────────────────────"
	@echo "  make da-build       build dev-assist binary"
	@echo "  make da-install     install dev-assist CLI symlink"
	@echo "  make da-uninstall   remove dev-assist CLI symlink"
	@echo "  make da-rebuild     clean + build dev-assist"
	@echo "  make da-clean       remove binary  (keeps .venv)"
	@echo "  make da-clean-all   remove binary + .venv"
	@echo "  make da-run         launch dev-assist CLI"
	@echo "  make da-web         launch dev-assist web UI"
	@echo ""
	@echo "  ── Combined ─────────────────────────────────────────────"
	@echo "  make build-all      build GUI + dev-assist"
	@echo "  make install-all    install GUI + dev-assist"
	@echo ""

# ---------------------------------------------------------
# Build GUI + Manager + ollama-main
# ---------------------------------------------------------
build:
	@echo "➜ Building GUI + Manager"
	bash $(BUILD_GUI)
	@echo "➜ Building ollama-main CLI"
	bash $(BUILD_MAIN)
	@echo "✔ Build complete"

# ---------------------------------------------------------
# Build everything
# ---------------------------------------------------------
build-all: build da-build

# ---------------------------------------------------------
# Install GUI  (builds first if binaries are missing)
# ---------------------------------------------------------
install:
	@if [ ! -f "$(APP_GUI)" ] || [ ! -f "$(APP_MGR)" ] || [ ! -f "$(CLI_BIN)" ]; then \
		echo "➜ Binaries not found — building first"; \
		$(MAKE) build; \
	fi
	bash $(INSTALL_SCRIPT)

# ---------------------------------------------------------
# Install GUI + dev-assist
# ---------------------------------------------------------
install-all:
	$(MAKE) install
	$(MAKE) da-install

# ---------------------------------------------------------
# Uninstall everything
# ---------------------------------------------------------
uninstall:
	bash $(INSTALL_SCRIPT) remove

# ---------------------------------------------------------
# Rebuild GUI
# ---------------------------------------------------------
rebuild: clean build

# ---------------------------------------------------------
# Clean GUI binaries
# ---------------------------------------------------------
clean:
	@echo "➜ Cleaning GUI build artifacts"
	rm -f $(APP_GUI) $(APP_MGR) $(CLI_BIN)
	@echo "✔ Clean complete"
