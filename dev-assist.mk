# =========================================================
# dev-assist.mk
# =========================================================

DA_BINARY    = da
DA_BUILD     = builder/build-dev-assist.sh
DA_SUBDIR    = dev-assist
DA_CLI_NAME  = da

.PHONY: da-build da-clean da-clean-all da-rebuild da-install da-uninstall da-run da-web

# ---------------------------------------------------------
# Build
# ---------------------------------------------------------
da-build:
	@echo "➜ Building dev-assist"
	bash $(DA_BUILD)
	@echo "✔ dev-assist binary ready: ./$(DA_BINARY)"

# ---------------------------------------------------------
# Clean
# ---------------------------------------------------------
da-clean:
	@echo "➜ Cleaning dev-assist build artifacts"
	rm -f $(DA_BINARY)
	rm -rf $(DA_SUBDIR)/build $(DA_SUBDIR)/dist
	@echo "✔ dev-assist cleaned (.venv preserved)"

da-clean-all:
	@echo "➜ Full clean"
	rm -f $(DA_BINARY)
	rm -rf $(DA_SUBDIR)/build $(DA_SUBDIR)/dist $(DA_SUBDIR)/.venv
	@echo "✔ fully cleaned"

# ---------------------------------------------------------
# Rebuild
# ---------------------------------------------------------
da-rebuild: da-clean da-build

# ---------------------------------------------------------
# Install (auto-build handled in install.sh)
# ---------------------------------------------------------
da-install:
	@echo "➜ Installing dev-assist CLI"
	bash install.sh da-install

# ---------------------------------------------------------
# Uninstall
# ---------------------------------------------------------
da-uninstall:
	bash install.sh da-remove

# ---------------------------------------------------------
# Run helpers
# ---------------------------------------------------------
da-run:
	@[ -f "$(DA_BINARY)" ] || { echo "✖ Binary not found — run: make da-build"; exit 1; }
	./$(DA_BINARY)

da-web:
	@[ -f "$(DA_BINARY)" ] || { echo "✖ Binary not found — run: make da-build"; exit 1; }
	./$(DA_BINARY) --web
