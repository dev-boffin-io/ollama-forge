# =========================================================
# Ollama Forge Makefile
# =========================================================

APP_GUI=Ollama-ai-gui
CLI_BIN=ollama-main

BUILDER_DIR=builder

BUILD_GUI=$(BUILDER_DIR)/build-gui-bin.sh
BUILD_MAIN=$(BUILDER_DIR)/build-main.sh

INSTALL_SCRIPT=install.sh


.PHONY: help build install uninstall rebuild clean

# ---------------------------------------------------------
# Help
# ---------------------------------------------------------

help:
	@echo ""
	@echo "Ollama Forge Build System"
	@echo ""
	@echo "Commands:"
	@echo "  make build      → build binaries"
	@echo "  make install    → install application"
	@echo "  make uninstall  → remove installation"
	@echo "  make rebuild    → clean + build"
	@echo "  make clean      → remove built binaries"
	@echo ""

# ---------------------------------------------------------
# Build
# ---------------------------------------------------------

build:
	@echo "➜ Building GUI"
	bash $(BUILD_GUI)

	@echo "➜ Building CLI"
	bash $(BUILD_MAIN)

	@echo "✔ Build complete"

# ---------------------------------------------------------
# Install
# ---------------------------------------------------------

install:
	@echo "➜ Installing"
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
	rm -f $(CLI_BIN)

	@echo "✔ Clean complete"
