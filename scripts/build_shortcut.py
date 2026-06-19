#!/usr/bin/env python3
"""Generate a signed `.shortcut` file for "Redact PII from Clipboard".

Why this exists
---------------
The Apple Shortcut is intentionally a thin launcher: a single "Run Shell
Script" action that calls `scripts/redact_via_shortcut.sh`. No redaction
logic, no model paths, no Python internals leak into the Shortcut.

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
WRAPPER = REPO_ROOT / "scripts" / "redact_via_shortcut.sh"
OUT_DIR = REPO_ROOT / "dist"
SHORTCUT_NAME = "Redact PII"
OUT_FILE = OUT_DIR / f"{SHORTCUT_NAME}.shortcut"


def build_workflow() -> dict:
    """Build the WFWorkflow plist for a single Run Shell Script action."""
    if not WRAPPER.exists():
        sys.exit(f"ERROR: wrapper not found at {WRAPPER}")
    wrapper_abs = str(WRAPPER)

    # The Run Shell Script action. Identifier and parameter keys come from
    # Apple's WFWorkflowActions format (is.workflow.actions.runshell).
    # We pass an absolute path to the wrapper so the Shortcut doesn't depend
    # on cwd. Shell = /bin/sh (Shortcuts default), input = none.
    return {
        "WFWorkflowMinimumClientVersion": 900,
        "WFWorkflowMinimumClientVersionString": "900",
        "WFWorkflowIcon": {
            "WFWorkflowIconGlyphNumber": 59511,  # shield-ish
            "WFWorkflowIconStartColor": 4282601983,  # blue
        },
        "WFWorkflowImportQuestions": [],
        "WFWorkflowTypes": ["NCWidget", "WatchKit"],
        "WFWorkflowInputContentItemClasses": [],
        "WFWorkflowActions": [
            {
                "WFWorkflowActionIdentifier": "is.workflow.actions.runshell",
                "WFWorkflowActionParameters": {
                    "Script": f'bash "{wrapper_abs}"',
                    "UseAsArgument": False,
                    "Input": {
                        "Value": {
                            "AttachmentListType": "Output",
                            "OutputUUID": str(uuid.uuid4()),
                            "Type": "Attachment",
                        },
                        "WFSerializationType": "WFTextTokenStringAttachment",
                    },
                    "Shell": "/bin/sh",
                    "CustomOutputName": "Redacted",
                },
            }
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
