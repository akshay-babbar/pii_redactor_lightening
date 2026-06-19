#!/usr/bin/env python3
# Required parameters:
# @raycast.schemaVersion 1
# @raycast.title Redact PII from Clipboard
# @raycast.mode compact
# @raycast.packageName PII Redactor
#
# Optional parameters:
# @raycast.icon 🛡️
# @raycast.needsConfirmation false
# @raycast.author PII Redactor Lightening
# @raycast.description Reads the macOS clipboard, redacts PII (regex + local
# GLiNER model), and writes the redacted text back to the clipboard.
#
# Bind a global hotkey to this Script Command in Raycast:
#   Raycast -> Extensions -> PII Redactor -> "Redact PII from Clipboard"
#   -> click "Record Hotkey" (e.g. Cmd-Shift-R).
#
# This script is a thin entry point. Logic lives in the installed `redactor`
# package. The PYTHON below should be the project venv interpreter so that
# torch / gliner / loguru resolve. See README for the one-time setup.

from __future__ import annotations

import os
import sys
from pathlib import Path

# Prefer the project-local venv if it exists; fall back to whichever python ran
# this file. Raycast runs Script Commands with a minimal PATH, so an absolute
# interpreter is the most reliable choice.
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
_VENV_PY = _PROJECT_ROOT / ".venv" / "bin" / "python"
PYTHON = str(_VENV_PY) if _VENV_PY.exists() else sys.executable

# Make sure the project's `redactor` package is importable regardless of CWD.
sys.path.insert(0, str(_PROJECT_ROOT / "src"))


def main() -> int:
    # Re-exec into the venv python so dependencies resolve, but only if needed.
    if Path(sys.executable).resolve() != Path(PYTHON).resolve():
        os.execv(PYTHON, [PYTHON, __file__])

    try:
        from redactor.main import app  # typer app
    except ImportError as exc:
        sys.stderr.write(f"Failed to import redactor: {exc}\n")
        sys.stderr.write(f"Tried python={PYTHON}\n")
        return 1

    try:
        app(["run"], standalone_mode=False)
    except SystemExit as exc:  # typer raises SystemExit on completion
        return int(exc.code or 0)
    except Exception as exc:  # never surface a traceback to Raycast UI
        sys.stderr.write(f"Redaction failed: {type(exc).__name__}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
