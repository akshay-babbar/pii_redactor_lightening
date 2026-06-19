.PHONY: install shortcut

install: ## Full setup: env + model warm + build shortcut + open for import
	bash scripts/bootstrap.sh
	uv run python scripts/build_shortcut.py
	open "dist/Redact PII.shortcut"
	@echo ""
	@echo "==> Click 'Add' in the Shortcuts dialog."
	@echo "    Then: System Settings → General → Login Items → add Shortcuts.app"
	open "x-apple.systempreferences:com.apple.LoginItems-Settings.extension"

shortcut: ## Rebuild + reimport shortcut only (e.g. after a macOS upgrade)
	uv run python scripts/build_shortcut.py
	open "dist/Redact PII.shortcut"
