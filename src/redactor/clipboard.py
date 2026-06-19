"""macOS clipboard read/write via pbpaste/pbcopy.

Defensive: never raises on empty/non-text clipboard; returns "" on read failure.
"""

from __future__ import annotations

import subprocess

PBPASTE = "/usr/bin/pbpaste"
PBCOPY = "/usr/bin/pbcopy"


def read_clipboard() -> str:
    """Return current clipboard text, or "" if unavailable / non-text."""
    try:
        result = subprocess.run(
            [PBPASTE, "-Prefer", "txt"],
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        # Don't log clipboard content; log the failure mode only.
        from loguru import logger
        logger.warning("clipboard read failed: {}", type(exc).__name__)
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", errors="replace")


def write_clipboard(text: str) -> bool:
    """Write text to clipboard. Returns True on success."""
    try:
        result = subprocess.run(
            [PBCOPY],
            input=text.encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        from loguru import logger
        logger.error("clipboard write failed: {}", type(exc).__name__)
        return False
    ok = result.returncode == 0
    return ok
