# =========================================================
# Ollama Forge Makefile
# =========================================================

APP_GUI     = Ollama-ai-gui
APP_MGR     = Ollama-ai-manager
CLI_BIN     = ollama-main

BUILDER_DIR = builder

BUILD_GUI   = $(BUILDER_DIR)/build-gui-bin.sh
BUILD_MAIN  = $(BUILDER_DIR)/build-main.sh

INSTALL_SCRIPT = install.sh


.PHONY: help build install uninstall rebuild clean

# ---------------------------------------------------------
# Help
# ---------------------------------------------------------

help:
	@echo ""
	@echo "Ollama Forge Build System"
	@echo ""
	@echo "Commands:"
	@echo "  make build      → build all binaries"
	@echo "  make install    → install application (build first if needed)"
	@echo "  make uninstall  → remove installation"
	@echo "  make rebuild    → clean + build"
	@echo "  make clean      → remove built binaries"
	@echo ""

# ---------------------------------------------------------
# Build
# ---------------------------------------------------------

build:
	@echo "➜ Building GUI + Manager"
	bash $(BUILD_GUI)

	@echo "➜ Building CLI"
	bash $(BUILD_MAIN)

	@echo "✔ Build complete"

# ---------------------------------------------------------
# Install (builds first if binaries are missing)
# ---------------------------------------------------------

install:
	@echo "➜ Installing"
	@if [ ! -f "$(APP_GUI)" ] || [ ! -f "$(APP_MGR)" ] || [ ! -f "$(CLI_BIN)" ]; then \
		echo "➜ Binaries not found — building first"; \
		$(MAKE) build; \
	fi
	bash $(INSTALL_SCRIPT)

# ---------------------------------------------------------
# Uninstall
# ---------------------------------------------------------

uninstall:
	@echo "➜ Removing installation"
	bash $(INSTALL_SCRIPT) remove

# ---------------------------------------------------------
# Rebuild
# ---------------------------------------------------------

rebuild: clean build

# ---------------------------------------------------------
# Clean
# ---------------------------------------------------------

clean:
	@echo "➜ Cleaning build artifacts"
	rm -f $(APP_GUI)
	rm -f $(APP_MGR)
	rm -f $(CLI_BIN)
	@echo "✔ Clean complete"
