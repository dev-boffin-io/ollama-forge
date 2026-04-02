# =========================================================
# Ollama Forge — Makefile
# =========================================================
APP_GUI  = Ollama-ai-gui
APP_MGR  = Ollama-ai-manager
CLI_BIN  = ollama-main
DA_BIN   = da
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
	@echo "  make rebuild        clean then build everything"
	@echo "  make clean          remove all binaries"
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
	# FIX 1: -x instead of -f (detects missing + non-executable)
	@if [ ! -x "$(APP_GUI)" ] || [ ! -x "$(APP_MGR)" ] || [ ! -x "$(CLI_BIN)" ]; then \
		echo "➜ Binaries not found or not executable — building first"; \
		$(MAKE) build; \
	fi
	# FIX 2: explicit 'install' arg — future-proof against script default changes
	bash $(INSTALL_SCRIPT) install
# ---------------------------------------------------------
# Install GUI + dev-assist
# ---------------------------------------------------------
# FIX 3: single entrypoint via install.sh build — avoids double invocation
install-all:
	bash $(INSTALL_SCRIPT) build
# ---------------------------------------------------------
# Uninstall everything
# ---------------------------------------------------------
uninstall:
	bash $(INSTALL_SCRIPT) remove
# ---------------------------------------------------------
# Rebuild everything
# ---------------------------------------------------------
# FIX 5: include da-build so dev-assist isn't left behind
rebuild: clean build da-build
# ---------------------------------------------------------
# Clean all binaries
# ---------------------------------------------------------
# FIX 4: also remove DA_BIN so clean is truly complete
clean:
	@echo "➜ Cleaning all build artifacts"
	rm -f $(APP_GUI) $(APP_MGR) $(CLI_BIN) $(DA_BIN)
	@echo "✔ Clean complete"
