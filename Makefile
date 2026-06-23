.PHONY: install shortcut

install: ## Full setup: env + model warm + build shortcut + open for import
	FROM_MAKE_INSTALL=1 bash scripts/bootstrap.sh
	uv run python scripts/build_shortcut.py
	open "dist/Redact PII.shortcut"
	@echo ""
	@echo "==> Click 'Add' in the Shortcuts dialog (mandatory)."
	@echo "    Optional but recommended for reboot-resilience: System Settings →"
	@echo "    General → Login Items → add Shortcuts.app (otherwise the hotkey dies"
	@echo "    after each reboot until Shortcuts.app is manually launched once)."
	open "x-apple.systempreferences:com.apple.LoginItems-Settings.extension"

shortcut: ## Rebuild + reimport shortcut only (e.g. after a macOS upgrade)
	uv run python scripts/build_shortcut.py
	open "dist/Redact PII.shortcut"
