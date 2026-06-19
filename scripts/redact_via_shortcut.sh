#!/usr/bin/env bash
# Thin entrypoint for Apple Shortcuts ("Run Shell Script" action).
#
# Shortcuts runs this from an arbitrary cwd with a minimal PATH. To stay robust
# we:
#   1. Resolve the repo root from this file's location (set at install time).
#   2. Export a sane PATH so `uv` and `pbcopy`/`pbpaste` are found.
#   3. Invoke the existing Typer CLI (`redact-clipboard run`), which already
#      handles empty clipboard (exit 0) and write failures (exit 1).
#
# This script does NOT reimplement any redaction logic. It is a launcher only.
#
#stdout is captured by Shortcuts and shown as the result; keep it to one line.

set -euo pipefail

# Resolve repo root relative to this file. The Shortcut only needs to know the
# absolute path to THIS file; everything else is derived.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Minimal but sufficient PATH for uv (covers Homebrew and the official
# installer location), system pbcopy/pbpaste, and core utils. Shortcuts runs
# with an almost empty PATH, so we must enumerate these explicitly.
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

cd "$REPO_ROOT"

# Run the existing CLI. It prints a one-line summary on success and exits 0
# on empty clipboard / no-PII. Any non-zero exit is surfaced as the result.
exec uv run --no-project redact-clipboard run
