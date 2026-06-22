#!/usr/bin/env python3
"""Generate a signed `.shortcut` file for "Redact PII from Clipboard".

Why this exists
---------------
The Apple Shortcut is intentionally a thin launcher: three actions —

1. Show Notification: "Redacting PII..."     (instant, non-blocking signal)
2. Run Shell Script:  ~/.local/bin/pii-redact-clipboard
3. Show Notification: "Redaction complete. Redacted text is on clipboard."

No redaction logic, no model paths, no Python internals leak into the
Shortcut. The shell action calls `~/.local/bin/pii-redact-clipboard`, which
is installed by `scripts/bootstrap.sh`.

The launcher lives under `~/.local` (not TCC-protected) so external Shortcut
triggers (hotkey, BackgroundShortcutRunner) can execute it even if the repo
clone lives under Desktop/Documents/Downloads.

Notifications are status-only: the redacted text and the original clipboard
are never put in a notification body or in any log.

Icon: green lightning bolt — matches the project name "Lightening".
- Glyph 59764 = "Lightning Bolt" (Apple Shortcuts glyph catalog).
- Color 4292093695 = Shortcuts preset green.

Why a generator (and not a committed `.shortcut`)
-------------------------------------------------
The `.shortcut` plist schema has changed across macOS versions and a
hand-committed binary artifact is brittle. Instead, this script rebuilds the
file from a small Python dict on the user's machine, then signs it locally
with Apple's own `shortcuts sign` CLI. That keeps the artifact correct for
the host macOS version.

Usage
-----
    uv run python scripts/build_shortcut.py

Outputs `dist/Redact PII.shortcut` and signs it in place. Double-click the
file (or `open "dist/Redact PII.shortcut"`) to import into Shortcuts.app.

Requirements
------------
- macOS 12+ (for the `shortcuts` CLI).
- The repo must be installed first (`scripts/bootstrap.sh`).
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "dist"
SHORTCUT_NAME = "Redact PII"
OUT_FILE = OUT_DIR / f"{SHORTCUT_NAME}.shortcut"

# The launcher installed by bootstrap.sh at a fixed, non-TCC-protected path.
# External Shortcut triggers (hotkey, BackgroundShortcutRunner) cannot execute
# scripts under Desktop/Documents/Downloads; ~ is safe.
LAUNCHER = Path.home() / ".local" / "bin" / "pii-redact-clipboard"

# Matches shortcutpy's emitted workflow envelope (verified against macOS Shortcuts).
_CLIENT_VERSION = "4033.0.4.3"
_MIN_CLIENT_VERSION = 900


def _uid() -> str:
    return str(uuid.uuid4()).upper()


def build_workflow() -> dict:
    """Build the WFWorkflow plist: notify-start → run shell → notify-complete.

    The completion notification's body references the shell script's stdout via
    a WFTextTokenString + attachmentsByRange, so the CLI summary
    ("Redacted N span(s): ...") is shown to the user. The shell action's UUID
    is generated once and reused as the OutputUUID for that reference.
    """
    if not LAUNCHER.exists():
        sys.exit(
            f"ERROR: launcher not found at {LAUNCHER}\n"
            "Run scripts/bootstrap.sh first to install it."
        )
    launcher_abs = str(LAUNCHER)
    shell_action_uuid = _uid()

    # Local "Run Shell Script" is is.workflow.actions.runshellscript.
    # (is.workflow.actions.runsshscript is a different action: SSH to a remote host.)
    # is.workflow.actions.runshell does not exist and imports as "Unknown Action".
    return {
        "WFQuickActionSurfaces": [],
        "WFWorkflowClientVersion": _CLIENT_VERSION,
        "WFWorkflowHasOutputFallback": False,
        "WFWorkflowHasShortcutInputVariables": False,
        "WFWorkflowImportQuestions": [],
        "WFWorkflowMinimumClientVersion": _MIN_CLIENT_VERSION,
        "WFWorkflowMinimumClientVersionString": str(_MIN_CLIENT_VERSION),
        "WFWorkflowName": SHORTCUT_NAME,
        "WFWorkflowNoInputBehavior": {},
        "WFWorkflowOutputContentItemClasses": [],
        "WFWorkflowTypes": [],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowIcon": {
            "WFWorkflowIconGlyphNumber": 59764,  # "Lightning Bolt" glyph
            "WFWorkflowIconStartColor": 4292093695,  # Shortcuts preset green
        },
        "WFWorkflowActions": [
            # 1. Instant, non-blocking start signal. No clipboard text in body.
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.notification",
                "WFWorkflowActionParameters": {
                    "WFNotificationActionTitle": "Redact PII",
                    "WFNotificationActionBody": "Redacting PII...",
                    "WFNotificationActionSound": False,
                },
            },
            # 2. The actual work. Clipboard is read inside the Python CLI;
            #    Shortcut input is not passed, so no PII flows through Shortcut.
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.runshellscript",
                "WFWorkflowActionParameters": {
                    "UUID": shell_action_uuid,
                    "Script": f'bash "{launcher_abs}"',
                    "Shell": "/bin/bash",
                    # Clipboard is read inside the Python CLI; don't pass Shortcut input.
                    "InputMode": "don't pass",
                    "CustomOutputName": "Shell Script Result",
                },
            },
            # 3. Completion signal. Body references the shell script's stdout via
            #    WFTextTokenString so the CLI summary ("Redacted N span(s): ...")
            #    reaches the user. Status-only beyond that; never includes raw text.
            #    The U+FFFC char in the string marks where the variable is inserted;
            #    attachmentsByRange maps its position to the shell action's OutputUUID.
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.notification",
                "WFWorkflowActionParameters": {
                    "WFNotificationActionTitle": "Redact PII",
                    "WFNotificationActionBody": {
                        "Value": {
                            "string": "Redacted: \uFFFC",
                            "attachmentsByRange": {
                                "{10, 1}": {
                                    "OutputUUID": shell_action_uuid,
                                    "OutputName": "Shell Script Result",
                                    "Type": "ActionOutput",
                                }
                            },
                        },
                        "WFSerializationType": "WFTextTokenString",
                    },
                    "WFNotificationActionSound": True,
                },
            },
        ],
    }


def main() -> int:
    if sys.platform != "darwin":
        sys.exit("ERROR: this script only runs on macOS (needs `shortcuts` CLI).")

    if not shutil.which("shortcuts"):
        sys.exit(
            "ERROR: `shortcuts` CLI not found. Requires macOS 12 (Monterey) or later."
        )

    OUT_DIR.mkdir(exist_ok=True)

    # plistlib needs a ".shortcut" extension for `shortcuts sign` to accept it.
    unsigned = OUT_DIR / f"{SHORTCUT_NAME}.unsigned.shortcut"
    with open(unsigned, "wb") as f:
        plistlib.dump(build_workflow(), f, fmt=plistlib.FMT_BINARY)

    # Sign in-place using Apple's own CLI so it imports cleanly.
    print(f"Signing {unsigned.name} -> {OUT_FILE.name} ...")
    rc = subprocess.call(
        [
            "shortcuts", "sign",
            "-i", str(unsigned),
            "-o", str(OUT_FILE),
            "--mode", "anyone",
        ]
    )
    unsigned.unlink(missing_ok=True)
    if rc != 0:
        sys.exit(
            "ERROR: signing failed. The `.shortcut` schema may differ on this "
            "macOS version. Fall back to the manual build steps in README.md."
        )

    print(f"\nDone: {OUT_FILE}")
    print("Next: double-click the file (or run `open \"%s\"`) to import, "
          "then assign a keyboard shortcut in Shortcuts.app." % OUT_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
