# =============================================================================
# ollama-forge — Makefile
# =============================================================================
#
# Targets:
#   all               Build all three binaries
#   build-main        Build ollama-main CLI binary
#   build-gui         Build Ollama-ai-gui + Ollama-ai-manager binaries
#   build-dev-assist  Build da (dev-assist) binary
#   install           Install CLI symlinks + GUI desktop entry (sudo for /usr/local/bin)
#   uninstall         Remove installed symlinks, desktop entry, and icon
#   install-deps-gui  Install system Qt/Python deps (requires sudo)
#   test              Run dev-assist pytest suite
#   lint              Ruff + black check
#   format            Black + isort auto-fix
#   clean             Remove all build artefacts and venvs
#   help              Show this message
#
# =============================================================================

SHELL  := /bin/bash
.DEFAULT_GOAL := help

# Colours
GREEN  := \033[0;32m
YELLOW := \033[1;33m
BLUE   := \033[0;34m
NC     := \033[0m

# Paths
BUILDER       := builder
DEV_ASSIST    := dev-assist
GUI_DIR       := gui

# Output binaries (as declared in .gitignore)
BIN_MAIN      := ollama-main
BIN_GUI       := Ollama-ai-gui
BIN_MGR       := Ollama-ai-manager
BIN_DA        := da

# Install paths
# CLI symlinks go to /usr/local/bin (sudo required).
# Override: make install INSTALL_BIN=~/.local/bin
INSTALL_BIN   := /usr/local/bin

PROJECT_DIR   := $(shell pwd)
ICON_SRC      := $(PROJECT_DIR)/ollama-forge.png
ICON_DIR      := $(HOME)/.local/share/icons/hicolor/1024x1024/apps
ICON_DEST     := $(ICON_DIR)/ollama-forge.png
DESKTOP_DIR   := $(HOME)/.local/share/applications
DESKTOP_FILE  := $(DESKTOP_DIR)/ollama-forge.desktop

# ─── Top-level targets ────────────────────────────────────────────────────────

.PHONY: all
all: build-main build-gui build-dev-assist  ## Build all three binaries
	@echo -e "$(GREEN)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(NC)"
	@echo -e "  ✅  All binaries built"
	@echo -e "$(GREEN)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(NC)"

# ─── Individual builds ────────────────────────────────────────────────────────

.PHONY: build-main
build-main:  ## Build ollama-main CLI binary → ./ollama-main
	@echo -e "$(BLUE)[→]$(NC) Building ollama-main..."
	@bash $(BUILDER)/build-main.sh

.PHONY: build-gui
build-gui:  ## Build PyQt5 GUI binaries → ./Ollama-ai-gui + ./Ollama-ai-manager
	@echo -e "$(BLUE)[→]$(NC) Building Ollama GUI..."
	@bash $(BUILDER)/build-gui-bin.sh

.PHONY: build-dev-assist
build-dev-assist:  ## Build dev-assist binary → ./da
	@echo -e "$(BLUE)[→]$(NC) Building dev-assist..."
	@bash $(BUILDER)/build-dev-assist.sh

# ─── Install / Uninstall ──────────────────────────────────────────────────────

.PHONY: install
install:  ## Install CLI symlinks + GUI desktop entry (sudo for /usr/local/bin)
	@echo -e "$(BLUE)[→]$(NC) Installing ollama-forge..."

	@# ── Verify binaries exist before doing anything ──────────────────────────
	@for bin in $(BIN_MAIN) $(BIN_GUI) $(BIN_DA); do \
		if [ ! -f "$(PROJECT_DIR)/$$bin" ]; then \
			echo -e "$(YELLOW)[!]$(NC) $$bin not found — run 'make build-main build-gui build-dev-assist' first"; \
			exit 1; \
		fi; \
	done

	@# ── CLI symlinks (/usr/local/bin — sudo required) ────────────────────────
	@echo -e "$(BLUE)[→]$(NC) Creating symlinks in $(INSTALL_BIN)..."
	@sudo ln -sf "$(PROJECT_DIR)/$(BIN_MAIN)" "$(INSTALL_BIN)/$(BIN_MAIN)"
	@echo -e "  $(GREEN)✔$(NC)  $(INSTALL_BIN)/$(BIN_MAIN)  →  $(PROJECT_DIR)/$(BIN_MAIN)"
	@sudo ln -sf "$(PROJECT_DIR)/$(BIN_DA)"   "$(INSTALL_BIN)/$(BIN_DA)"
	@echo -e "  $(GREEN)✔$(NC)  $(INSTALL_BIN)/$(BIN_DA)  →  $(PROJECT_DIR)/$(BIN_DA)"

	@# ── Icon ─────────────────────────────────────────────────────────────────
	@if [ ! -f "$(ICON_SRC)" ]; then \
		echo -e "$(YELLOW)[!]$(NC) Icon not found — skipping: $(ICON_SRC)"; \
	else \
		mkdir -p "$(ICON_DIR)"; \
		cp -f "$(ICON_SRC)" "$(ICON_DEST)"; \
		echo -e "  $(GREEN)✔$(NC)  Icon installed → $(ICON_DEST)"; \
		gtk-update-icon-cache -f -t "$(HOME)/.local/share/icons/hicolor" 2>/dev/null || true; \
	fi

	@# ── Desktop entry ────────────────────────────────────────────────────────
	@mkdir -p "$(DESKTOP_DIR)"
	@printf '%s\n' \
		'[Desktop Entry]' \
		'Version=1.0' \
		'Type=Application' \
		'Name=Ollama Forge' \
		'Comment=Local AI chat and model manager powered by Ollama' \
		'Exec=$(PROJECT_DIR)/$(BIN_GUI)' \
		'Icon=ollama-forge' \
		'Terminal=false' \
		'Categories=Utility;Science;ArtificialIntelligence;' \
		'Keywords=ollama;ai;llm;chat;local;' \
		'StartupWMClass=Ollama-ai-gui' \
		> "$(DESKTOP_FILE)"
	@chmod +x "$(DESKTOP_FILE)"
	@echo -e "  $(GREEN)✔$(NC)  Desktop entry → $(DESKTOP_FILE)"

	@# ── Refresh desktop database ─────────────────────────────────────────────
	@update-desktop-database "$(DESKTOP_DIR)" 2>/dev/null || true

	@echo ""
	@echo -e "$(GREEN)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(NC)"
	@echo -e "  ✅  Installation complete"
	@echo ""
	@echo -e "  CLI commands now available:"
	@echo -e "    $(INSTALL_BIN)/$(BIN_MAIN)   — Ollama lifecycle manager"
	@echo -e "    $(INSTALL_BIN)/$(BIN_DA)        — AI DevOps assistant"
	@echo ""
	@echo -e "  GUI launcher added to your application menu."
	@echo -e "$(GREEN)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(NC)"

.PHONY: uninstall
uninstall:  ## Remove CLI symlinks, desktop entry, and icon
	@echo -e "$(YELLOW)[!]$(NC) Uninstalling ollama-forge..."

	@# ── Remove CLI symlinks ───────────────────────────────────────────────────
	@if [ -L "$(INSTALL_BIN)/$(BIN_MAIN)" ]; then \
		sudo rm -f "$(INSTALL_BIN)/$(BIN_MAIN)"; \
		echo -e "  $(GREEN)✔$(NC)  Removed $(INSTALL_BIN)/$(BIN_MAIN)"; \
	else \
		echo -e "  $(YELLOW)–$(NC)  $(INSTALL_BIN)/$(BIN_MAIN) not found, skipping"; \
	fi
	@if [ -L "$(INSTALL_BIN)/$(BIN_DA)" ]; then \
		sudo rm -f "$(INSTALL_BIN)/$(BIN_DA)"; \
		echo -e "  $(GREEN)✔$(NC)  Removed $(INSTALL_BIN)/$(BIN_DA)"; \
	else \
		echo -e "  $(YELLOW)–$(NC)  $(INSTALL_BIN)/$(BIN_DA) not found, skipping"; \
	fi

	@# ── Remove desktop entry ─────────────────────────────────────────────────
	@if [ -f "$(DESKTOP_FILE)" ]; then \
		rm -f "$(DESKTOP_FILE)"; \
		echo -e "  $(GREEN)✔$(NC)  Removed $(DESKTOP_FILE)"; \
		update-desktop-database "$(DESKTOP_DIR)" 2>/dev/null || true; \
	else \
		echo -e "  $(YELLOW)–$(NC)  Desktop entry not found, skipping"; \
	fi

	@# ── Remove icon ──────────────────────────────────────────────────────────
	@if [ -f "$(ICON_DEST)" ]; then \
		rm -f "$(ICON_DEST)"; \
		echo -e "  $(GREEN)✔$(NC)  Removed $(ICON_DEST)"; \
		gtk-update-icon-cache -f -t "$(HOME)/.local/share/icons/hicolor" 2>/dev/null || true; \
	else \
		echo -e "  $(YELLOW)–$(NC)  Icon not found, skipping"; \
	fi

	@echo -e "$(GREEN)[✓]$(NC) Uninstall complete"

# ─── System dependencies ──────────────────────────────────────────────────────

.PHONY: install-deps-gui
install-deps-gui:  ## Install system Qt/Python deps for GUI (ARM64/Debian, requires sudo)
	@echo -e "$(YELLOW)[!]$(NC) Installing system GUI dependencies (sudo required)..."
	@sudo bash $(BUILDER)/install-deps-gui.sh

# ─── Development ──────────────────────────────────────────────────────────────

.PHONY: test
test:  ## Run dev-assist pytest suite
	@echo -e "$(BLUE)[→]$(NC) Running tests..."
	@cd $(DEV_ASSIST) && python -m pytest tests/ -v --tb=short

.PHONY: lint
lint:  ## Lint with ruff + black --check
	@echo -e "$(BLUE)[→]$(NC) Linting..."
	@cd $(DEV_ASSIST) && ruff check . && black --check .

.PHONY: format
format:  ## Auto-format with black + isort
	@echo -e "$(BLUE)[→]$(NC) Formatting..."
	@cd $(DEV_ASSIST) && black . && isort .

# ─── Utility ──────────────────────────────────────────────────────────────────

.PHONY: clean
clean:  ## Remove all build artefacts, venvs, and output binaries
	@echo -e "$(YELLOW)[!]$(NC) Cleaning build artefacts..."

	# Remove PyInstaller build/dist directories
	@rm -rf \
		$(DEV_ASSIST)/build  \
		$(DEV_ASSIST)/dist   \
		$(GUI_DIR)/build     \
		$(GUI_DIR)/dist      \
		build/ dist/

	# Remove isolated build venvs
	@rm -rf \
		$(BUILDER)/.venv-build-main  \
		$(BUILDER)/.venv-build       \
		$(DEV_ASSIST)/.venv

	# Remove output binaries
	@rm -f $(BIN_MAIN) $(BIN_GUI) $(BIN_MGR) $(BIN_DA)

	# Remove Python caches and PyInstaller spec files
	@find . \
		-not -path './.git/*' \
		\( -name "__pycache__" -o -name "*.pyc" -o -name "*.pyo" -o -name "*.spec" \) \
		-exec rm -rf {} + 2>/dev/null || true

	# Remove pytest / coverage artefacts
	@rm -rf .pytest_cache htmlcov .coverage .coverage.*

	@echo -e "$(GREEN)[✓]$(NC) Clean complete"

.PHONY: help
help:  ## Show available targets
	@echo ""
	@echo "  ollama-forge — Makefile targets"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*##"}; {printf "  $(BLUE)%-22s$(NC) %s\n", $$1, $$2}'
	@echo ""
