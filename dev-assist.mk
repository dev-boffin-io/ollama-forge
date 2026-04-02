# =========================================================
# dev-assist.mk — Include this in the project Makefile
#
# In Makefile add:
#   include dev-assist.mk
# =========================================================

DA_BINARY    = dev-assist
DA_BUILD     = builder/build-dev-assist.sh
DA_SUBDIR    = dev-assist
DA_CLI_NAME  = dev-assist

# ── Phony targets ────────────────────────────────────────
.PHONY: da-build da-clean da-rebuild da-install da-uninstall da-run da-web

# ---------------------------------------------------------
# Build
# ---------------------------------------------------------
da-build:
	@echo "➜ Building dev-assist"
	bash $(DA_BUILD)
	@echo "✔ dev-assist binary ready: ./$(DA_BINARY)"

# ---------------------------------------------------------
# Clean  (keeps .venv)
# ---------------------------------------------------------
da-clean:
	@echo "➜ Cleaning dev-assist build artifacts"
	rm -f $(DA_BINARY)
	rm -rf $(DA_SUBDIR)/build $(DA_SUBDIR)/dist
	@echo "✔ dev-assist cleaned (.venv preserved)"

# ---------------------------------------------------------
# Clean everything including .venv
# ---------------------------------------------------------
da-clean-all:
	@echo "➜ Full clean — removing dev-assist build artifacts and .venv"
	rm -f $(DA_BINARY)
	rm -rf $(DA_SUBDIR)/build $(DA_SUBDIR)/dist $(DA_SUBDIR)/.venv
	@echo "✔ dev-assist fully cleaned"

# ---------------------------------------------------------
# Rebuild
# ---------------------------------------------------------
da-rebuild: da-clean da-build

# ---------------------------------------------------------
# Install  (symlink to ~/.local/bin)
# ---------------------------------------------------------
da-install:
	@echo "➜ Installing dev-assist CLI"
	@[ -f "$(DA_BINARY)" ] || { echo "✖ Binary not found — run: make da-build"; exit 1; }
	bash install.sh da-install

# ---------------------------------------------------------
# Uninstall
# ---------------------------------------------------------
da-uninstall:
	bash install.sh da-remove

# ---------------------------------------------------------
# Quick run helpers
# ---------------------------------------------------------
da-run:
	@[ -f "$(DA_BINARY)" ] || { echo "✖ Binary not found — run: make da-build"; exit 1; }
	./$(DA_BINARY)

da-web:
	@[ -f "$(DA_BINARY)" ] || { echo "✖ Binary not found — run: make da-build"; exit 1; }
	./$(DA_BINARY) --web
