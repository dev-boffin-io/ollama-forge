# =============================================================================
# ollama-forge — Makefile  (PyQt6 · restructured)
# =============================================================================
#
# Targets:
#   all               Build all binaries for the current arch
#   build-gui         Build Ollama-ai-gui + Ollama-ai-manager
#   build-da          Build da + ollama-main  (both in bin/dev-assist/)
#   build-main        Alias for build-da  (backward compat)
#   build-dev-assist  Alias for build-da  (backward compat)
#   install           Install symlinks + desktop entry
#   uninstall         Remove installed symlinks, desktop entry, icon
#   test              Run dev-assist pytest suite
#   lint              ruff + black --check
#   format            black + isort auto-fix
#   clean             Remove all build artefacts and venvs
#   help              Show this message
#
# =============================================================================

SHELL          := /bin/bash
.DEFAULT_GOAL  := help

# ── Colors ────────────────────────────────────────────────────────────────────
GREEN  := \033[0;32m
YELLOW := \033[1;33m
BLUE   := \033[0;34m
RED    := \033[0;31m
NC     := \033[0m

# ── Source dirs ───────────────────────────────────────────────────────────────
BUILDER    := builder
DEV_ASSIST := dev-assist
GUI_DIR    := gui

# ── Architecture detection ────────────────────────────────────────────────────
ARCH := $(shell uname -m)

ifeq ($(ARCH),aarch64)
  ARCH_LABEL   := arm64
  GUI_SCRIPT   := $(BUILDER)/build-gui-linux-arm64.sh
  DA_SCRIPT    := $(BUILDER)/build-da-linux-arm64.sh
  _GUI_SUFFIX  := -arm64
  _DA_SUFFIX   := -arm64
else
  ARCH_LABEL   := amd64
  GUI_SCRIPT   := $(BUILDER)/build-gui-linux-amd64.sh
  DA_SCRIPT    := $(BUILDER)/build-da-linux-amd64.sh
  _GUI_SUFFIX  :=
  _DA_SUFFIX   :=
endif

# ── Output binary paths ───────────────────────────────────────────────────────
BIN_GUI  := bin/Ollama-GUI/Ollama-ai-gui$(_GUI_SUFFIX)
BIN_MGR  := bin/Ollama-GUI/Ollama-ai-manager$(_GUI_SUFFIX)
BIN_DA   := bin/dev-assist/da$(_DA_SUFFIX)
BIN_MAIN := bin/dev-assist/ollama-main$(_DA_SUFFIX)

# ── Install paths ─────────────────────────────────────────────────────────────
# CLI symlinks → /usr/local/bin  (sudo required)
# Override:  make install INSTALL_BIN=~/.local/bin
INSTALL_BIN  := /usr/local/bin
PROJECT_DIR  := $(shell pwd)
ICON_SRC     := $(PROJECT_DIR)/ollama-forge.png
ICON_DIR     := $(HOME)/.local/share/icons/hicolor/512x512/apps
ICON_DEST    := $(ICON_DIR)/ollama-forge.png
DESKTOP_DIR  := $(HOME)/.local/share/applications
DESKTOP_FILE := $(DESKTOP_DIR)/ollama-forge.desktop

# =============================================================================
# Build targets
# =============================================================================

.PHONY: all
all: build-gui build-da  ## Build all binaries for current arch ($(ARCH_LABEL))
	@echo -e "$(GREEN)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(NC)"
	@echo -e "  ✅  All binaries built  [$(ARCH_LABEL)]"
	@echo ""
	@echo -e "  bin/Ollama-GUI/"
	@[ -f "$(BIN_GUI)"  ] && echo -e "    ├── $(notdir $(BIN_GUI))   $$(du -sh $(BIN_GUI)  | cut -f1)" || true
	@[ -f "$(BIN_MGR)"  ] && echo -e "    └── $(notdir $(BIN_MGR))   $$(du -sh $(BIN_MGR)  | cut -f1)" || true
	@echo -e "  bin/dev-assist/"
	@[ -f "$(BIN_DA)"   ] && echo -e "    ├── $(notdir $(BIN_DA))            $$(du -sh $(BIN_DA)   | cut -f1)" || true
	@[ -f "$(BIN_MAIN)" ] && echo -e "    └── $(notdir $(BIN_MAIN))   $$(du -sh $(BIN_MAIN) | cut -f1)" || true
	@echo -e "$(GREEN)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(NC)"

# ── GUI ───────────────────────────────────────────────────────────────────────
.PHONY: build-gui
build-gui:  ## Build Ollama-ai-gui + Ollama-ai-manager  →  bin/Ollama-GUI/
	@echo -e "$(BLUE)[→]$(NC) Building Ollama GUI  [$(ARCH_LABEL)]..."
	@bash $(GUI_SCRIPT)

# ── dev-assist (da + ollama-main) ─────────────────────────────────────────────
.PHONY: build-da
build-da:  ## Build da + ollama-main  →  bin/dev-assist/
	@echo -e "$(BLUE)[→]$(NC) Building dev-assist  [$(ARCH_LABEL)]..."
	@bash $(DA_SCRIPT)

# Backward-compatible aliases
.PHONY: build-main build-dev-assist
build-main: build-da       ## Alias for build-da  (builds da + ollama-main)

build-dev-assist: build-da ## Alias for build-da  (builds da + ollama-main)


# =============================================================================
# Install / Uninstall
# =============================================================================

.PHONY: install
install:  ## Install CLI symlinks + GUI desktop entry
	@echo -e "$(BLUE)[→]$(NC) Installing ollama-forge..."

	@# ── Verify binaries exist ──────────────────────────────────────────────
	@for bin in "$(BIN_MAIN)" "$(BIN_GUI)" "$(BIN_DA)"; do \
		if [ ! -f "$$bin" ]; then \
			echo -e "$(RED)[✗]$(NC) Binary not found: $$bin"; \
			echo -e "     Run  make all  first."; \
			exit 1; \
		fi; \
	done

	@# ── CLI symlinks (/usr/local/bin — sudo required) ─────────────────────
	@echo -e "$(BLUE)[→]$(NC) Creating symlinks in $(INSTALL_BIN)..."
	@sudo ln -sf "$(PROJECT_DIR)/$(BIN_MAIN)" "$(INSTALL_BIN)/ollama-main"
	@echo -e "  $(GREEN)✔$(NC)  $(INSTALL_BIN)/ollama-main  →  $(BIN_MAIN)"
	@sudo ln -sf "$(PROJECT_DIR)/$(BIN_DA)"   "$(INSTALL_BIN)/da"
	@echo -e "  $(GREEN)✔$(NC)  $(INSTALL_BIN)/da  →  $(BIN_DA)"

	@# ── Icon ──────────────────────────────────────────────────────────────
	@if [ -f "$(ICON_SRC)" ]; then \
		mkdir -p "$(ICON_DIR)"; \
		cp -f "$(ICON_SRC)" "$(ICON_DEST)"; \
		echo -e "  $(GREEN)✔$(NC)  Icon → $(ICON_DEST)"; \
		gtk-update-icon-cache -f -t "$(HOME)/.local/share/icons/hicolor" 2>/dev/null || true; \
	else \
		echo -e "$(YELLOW)[!]$(NC) Icon not found — skipping: $(ICON_SRC)"; \
	fi

	@# ── Desktop entry ─────────────────────────────────────────────────────
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
	@update-desktop-database "$(DESKTOP_DIR)" 2>/dev/null || true

	@echo ""
	@echo -e "$(GREEN)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(NC)"
	@echo -e "  ✅  Installation complete"
	@echo ""
	@echo -e "  CLI commands:"
	@echo -e "    $(INSTALL_BIN)/ollama-main   — Ollama lifecycle manager"
	@echo -e "    $(INSTALL_BIN)/da            — AI DevOps assistant"
	@echo -e "$(GREEN)━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━$(NC)"

.PHONY: uninstall
uninstall:  ## Remove CLI symlinks, desktop entry, and icon
	@echo -e "$(YELLOW)[!]$(NC) Uninstalling ollama-forge..."

	@for link in ollama-main da; do \
		if [ -L "$(INSTALL_BIN)/$$link" ]; then \
			sudo rm -f "$(INSTALL_BIN)/$$link"; \
			echo -e "  $(GREEN)✔$(NC)  Removed $(INSTALL_BIN)/$$link"; \
		else \
			echo -e "  $(YELLOW)–$(NC)  $(INSTALL_BIN)/$$link not found, skipping"; \
		fi; \
	done

	@if [ -f "$(DESKTOP_FILE)" ]; then \
		rm -f "$(DESKTOP_FILE)"; \
		echo -e "  $(GREEN)✔$(NC)  Removed $(DESKTOP_FILE)"; \
		update-desktop-database "$(DESKTOP_DIR)" 2>/dev/null || true; \
	else \
		echo -e "  $(YELLOW)–$(NC)  Desktop entry not found, skipping"; \
	fi

	@if [ -f "$(ICON_DEST)" ]; then \
		rm -f "$(ICON_DEST)"; \
		echo -e "  $(GREEN)✔$(NC)  Removed $(ICON_DEST)"; \
		gtk-update-icon-cache -f -t "$(HOME)/.local/share/icons/hicolor" 2>/dev/null || true; \
	else \
		echo -e "  $(YELLOW)–$(NC)  Icon not found, skipping"; \
	fi

	@echo -e "$(GREEN)[✓]$(NC) Uninstall complete"

# =============================================================================
# Development helpers
# =============================================================================

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

# =============================================================================
# Clean
# =============================================================================

.PHONY: clean
clean:  ## Remove all build artefacts, venvs, and output binaries
	@echo -e "$(YELLOW)[!]$(NC) Cleaning build artefacts..."

	@# PyInstaller leftovers
	@rm -rf \
		$(DEV_ASSIST)/build  $(DEV_ASSIST)/dist \
		$(GUI_DIR)/build     $(GUI_DIR)/dist \
		build/ dist/

	@# Isolated build venvs (new naming)
	@rm -rf \
		$(BUILDER)/.venv-gui-amd64 \
		$(BUILDER)/.venv-gui-arm64 \
		$(BUILDER)/.venv-da-amd64  \
		$(BUILDER)/.venv-da-arm64  \
		$(BUILDER)/.venv-gui-win   \
		$(BUILDER)/.venv-da-win    \
		$(BUILDER)/.venv-build-main \
		$(BUILDER)/.venv-build      \
		$(DEV_ASSIST)/.venv

	@# Output binaries
	@rm -f \
		bin/Ollama-GUI/Ollama-ai-gui        bin/Ollama-GUI/Ollama-ai-gui-arm64 \
		bin/Ollama-GUI/Ollama-ai-manager    bin/Ollama-GUI/Ollama-ai-manager-arm64 \
		bin/dev-assist/da                   bin/dev-assist/da-arm64 \
		bin/dev-assist/ollama-main          bin/dev-assist/ollama-main-arm64

	@# Spec files + Python caches
	@find . \
		-not -path './.git/*' \
		\( -name "__pycache__" -o -name "*.pyc" -o -name "*.pyo" -o -name "*.spec" \) \
		-exec rm -rf {} + 2>/dev/null || true

	@# pytest / coverage
	@rm -rf .pytest_cache htmlcov .coverage .coverage.*

	@echo -e "$(GREEN)[✓]$(NC) Clean complete"

# =============================================================================
# Help
# =============================================================================

.PHONY: help
help:  ## Show available targets
	@echo ""
	@echo "  ollama-forge — available targets  [arch: $(ARCH_LABEL)]"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*##"}; {printf "  $(BLUE)%-22s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "  Output layout after build:"
	@echo "    bin/Ollama-GUI/   Ollama-ai-gui$(if $(_GUI_SUFFIX),-arm64,)   Ollama-ai-manager$(if $(_GUI_SUFFIX),-arm64,)"
	@echo "    bin/dev-assist/   da$(if $(_DA_SUFFIX),-arm64,)              ollama-main$(if $(_DA_SUFFIX),-arm64,)"
	@echo ""
