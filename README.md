# Redact PII

Local macOS clipboard PII redactor. Copy text, trigger redaction, paste the
cleaned result. Everything runs on your Mac — no cloud, no UI, no data leaving
your machine.

## What it redacts

- Emails, phone numbers, PAN, Aadhaar, UPI IDs
- IP addresses, tokens, card and account numbers
- Names, organizations, cities, addresses
- Passport numbers, usernames, dates of birth

## How it works

```mermaid
flowchart LR
  copy[Copy text] --> clip[Clipboard]
  clip --> trigger["Hotkey or CLI"]
  trigger --> redact["Redact PII locally"]
  redact --> clip2[Clipboard updated]
  clip2 --> paste[Paste anywhere]
```

Regex catches structured IDs; a small on-device model catches names and places.
Everything stays on your Mac.

## Requirements

- macOS 12+
- [`uv`](https://docs.astral.sh/getting-started/installation/)

## Setup

```bash
make install
```

Click **Add** in the Shortcuts import dialog when it opens — this step is
mandatory. Then assign a keyboard shortcut in Shortcuts.app (open **Redact
PII** → info icon → **Add Keyboard Shortcut**, e.g. `⌘⇧R`).

**Reboot resilience (recommended):** Add Shortcuts.app to **System Settings →
General → Login Items**. Without this, the hotkey stops working after each
reboot until you launch Shortcuts.app once manually. To open Login Items
directly:

```bash
open "x-apple.systempreferences:com.apple.LoginItems-Settings.extension"
```

## Use

### Keyboard shortcut (recommended)

Copy text → press your hotkey → paste the redacted text.

You'll get a notification when redaction starts and when it finishes.

### CLI

```bash
~/.local/bin/pii-redact-clipboard
```

Reads the clipboard, redacts in place, prints a summary.

## Notes

- First install downloads a ~1 GB local model (one time).
- Each run loads the model fresh (~8–12 s).
- Logs: `~/.pii_redactor/redactor.log` (counts and timing only; never your
  clipboard text).

## License

Apache-2.0. See [LICENSE](LICENSE). This software is provided "AS IS", without
warranty of any kind, express or implied. The authors offer it as a personal
project to the community and assume no liability for any use or misuse.
