#!/usr/bin/env bash
# Idempotent one-paste setup for pii_redactor_lightening.
#
# Safe to re-run: each step is a no-op once it has succeeded.
#
# Verifies:
#   - uv is installed
#   - .venv exists
#   - package is installed in editable mode
#   - GLiNER model is downloaded and cached (~120 MB, paid once here)
#
# After this completes, the next step is to import the Apple Shortcut and
# bind a hotkey (see README.md).

set -euo pipefail

# Resolve repo root regardless of CWD so this works as `bash scripts/bootstrap.sh`.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

echo "==> [1/4] Verifying uv is installed"
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv is not installed. Install it first:" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi
echo "    uv: $(uv --version)"

echo "==> [2/4] Creating .venv (idempotent)"
if [ -x ".venv/bin/python" ]; then
  echo "    .venv already exists, reusing"
else
  uv venv
fi

echo "==> [3/4] Installing package in editable mode (idempotent)"
# `uv pip install -e .` is itself idempotent; if already installed it re-links quickly.
uv pip install -e . >/dev/null

echo "==> [4/4] Prewarming GLiNER model cache (~120 MB, one-time)"
# Touch the cache by triggering the lazy load once. Subsequent CLI invocations
# (and Shortcut runs) skip the network download.
if uv run python -c "from redactor import model_redactor; model_redactor._load_model()" 2>/dev/null; then
  echo "    model cached"
else
  echo "ERROR: model prewarm failed." >&2
  echo "Check your network connection and re-run this script." >&2
  exit 1
fi

cat <<'EOF'

==> Setup complete.

Next steps:
  1. Open Shortcuts.app and import the generated shortcut (see README.md
     for the build + import flow), OR run the CLI directly:

         uv run redact-clipboard run

  2. In Shortcuts.app, assign a global keyboard shortcut to it
     (info (i) button -> Add Keyboard Shortcut).

  3. Add Shortcuts.app to System Settings -> General -> Login Items
     so the hotkey works without manually launching the app.
EOF
