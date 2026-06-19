# pii_redactor_lightening

Minimal **local** macOS clipboard PII redaction tool. Copy text → press a
hotkey → sensitive spans get replaced in place on the clipboard. No cloud
calls, no UI, no eval harness.

Two equally supported ways to trigger it:

- **Apple Shortcuts** — recommended for everyday use (free, native, syncs via iCloud).
- **CLI** — fallback, debugging, scripting.

## Prerequisites

- macOS 12+ (for Apple Shortcuts CLI signing)
- [`uv`](https://docs.astral.sh/getting-started/installation/) installed
- `pbpaste` / `pbcopy` (built into macOS)

## Setup (one paste)

From the repo root:

```bash
bash scripts/bootstrap.sh
```

This is idempotent — safe to re-run. It installs a **self-contained copy**
under `~/.local/share/pii-redactor/` and a launcher at
`~/.local/bin/pii-redact-clipboard`. It will:

1. Verify `uv` is installed
2. Create a venv at `~/.local/share/pii-redactor/.venv`
3. Install the package **non-editable** (copy, not symlink) into that venv
4. Download + cache the GLiNER model (~120 MB, paid once here, not on first hotkey press)
5. Write `~/.local/bin/pii-redact-clipboard` (the launcher the Shortcut calls)

The repo clone is **no longer needed at runtime** — the launcher points at
the installed venv, not the clone. This is why it works even when the clone
lives under Desktop/Documents/Downloads (which are TCC-blocked for external
Shortcut triggers; see [Troubleshooting](#troubleshooting)).

## Use via Apple Shortcuts (recommended)

### 1. Build the Shortcut (once)

```bash
uv run python scripts/build_shortcut.py
```

This writes a signed `dist/Redact PII.shortcut`. (The file is generated on
your machine rather than committed because the `.shortcut` plist schema has
shifted across macOS versions; building locally keeps it correct for your OS.)

### 2. Import it

```bash
open "dist/Redact PII.shortcut"
```

Confirm in the dialog. The Shortcut is a single "Run Shell Script" action
that calls `~/.local/bin/pii-redact-clipboard`, which invokes the installed
CLI. No redaction logic lives inside the Shortcut.

If you previously imported a version that errors or shows **Unknown Action**,
delete it in Shortcuts.app first, then re-run `build_shortcut.py` and import
again.

### 3. Assign a global hotkey

1. Open **Shortcuts.app**
2. Double-click **Redact PII**
3. Click the info (i) icon → **Add Keyboard Shortcut** → press a combo (e.g. `⌘⇧R`)
4. Add **Shortcuts.app** to **System Settings → General → Login Items**
   so the hotkey works without manually launching the app

### 4. First use

Copy any text, press the hotkey, paste back. PII is replaced with `[EMAIL]`,
`[PHONE]`, `[PAN]`, `[PERSON]`, etc.

### Cold-start latency (honest note)

Each Shortcut run is a **fresh process**, so the GLiNER model (~120 MB) loads
on every invocation. Cold start is ~8–9 s; warm in-process calls would be
sub-second but require a daemon, which is explicitly out of scope. Bootstrap
prewarm only saves the **download** — the per-invocation load cost remains.

## Use via CLI (fallback)

```bash
~/.local/bin/pii-redact-clipboard
```

Reads the clipboard, redacts in place, prints a one-line summary. Exits 0 on
empty clipboard or no-PII; exits 1 only on clipboard write failure.

(For development against the repo clone, you can still run
`uv run redact-clipboard run` from the repo root after `uv venv && uv pip install -e .`.)

## Use via Raycast (optional alternative trigger)

If you already use Raycast, `scripts/redact_clipboard.py` is a Script Command
that wraps the same CLI.

1. Raycast → **Extensions** → **Add Script Command** → **Scripts Directory** →
   point at this repo's `scripts/` folder.
2. The command **Redact PII from Clipboard** appears in root search.
3. Hover it → **Record Hotkey** (e.g. `⌘⇧R`).

The Raycast script auto-detects `.venv/bin/python` so torch / gliner / loguru
resolve even under Raycast's minimal `PATH`.

## Design at a glance

Two stacked layers:

1. **Deterministic regex layer** — fast, predictable, pinned for the formats
   you can't afford to miss: email, Indian phone, PAN, Aadhaar-like 12-digit
   patterns, UPI IDs, IPv4 addresses, long secret/token strings, long
   card/account-like numeric sequences, and **multi-line Indian addresses**
   (PIN-validated against the India Post registry via `bharataddress`).
2. **One small local model** —
   [`urchade/gliner_multi_pii-v1`](https://huggingface.co/urchade/gliner_multi_pii-v1)
   (Apache-2.0, ~0.3B params, DeBERTa-small backbone), specialised on a 40+
   PII taxonomy. Used only for high-confidence contextual entities that regex
   cannot catch cleanly: `person`, `organization`, `location`, `address`,
   `age`, `date of birth`.

The regex pass runs first; the model pass then runs **only on the text segments
regex did not already cover**, so placeholders are never re-masked and the model
never wastes compute on already-redacted spans.

### Why regex + one small model

- Regex gives zero-latency, zero-false-positive coverage on the formats where
  false negatives are unacceptable (PAN, Aadhaar, emails, tokens).
- The small GLiNER model covers the open-set cases regex fundamentally can't
  (people, places, organisations) with a sub-second local forward pass.
- Together they hit the practical PII surface without dragging in an 8B chat
  model or a heavy cloud dependency.

### Address and age handling

Multi-line addresses (the GLiNER model's known weak spot — it fails on
newline-spanning address spans even at threshold 0.3) are caught by a regex
pattern anchored on a valid Indian PIN code. The PIN is validated against the
embedded India Post registry via
[`bharataddress`](https://github.com/Neelagiri65/bharataddress) (zero-dependency,
4.3 MB, MIT). Invalid PINs cause the candidate span to be dropped (fail-closed).
Single-line addresses still flow through the model layer.

`age` and `date of birth` are GLiNER labels added to the model pass. `date of
birth` is in the model's training taxonomy; `age` is zero-shot but empirically
returns 0.88+ confidence on real phrasings.

### Why no large LLM

- Latency: an 8B model adds seconds per paste; this tool targets sub-second.
- Reliability: a local model has no network failure mode and no data egress.
- Cost & privacy: no inference provider, no API keys, nothing to leak.

## Hardware acceleration

- Apple Silicon: uses **MPS** with **fp16** weights (loaded directly via
  `GLiNER.from_pretrained(map_location="mps", dtype="fp16")`). Halves memory
  with no quality loss for inference.
- Other platforms: falls back to CPU fp32 automatically.
- **Quantization is intentionally skipped.** GLiNER's int8 path needs a
  QAT-trained model to preserve accuracy; the chosen model is not, so int8
  would trade accuracy for a memory win we already get from fp16.

## Large clipboard handling (chunked inference)

The model never sees the full clipboard in one pass. After the regex pass
(which always runs globally on the entire text), the regex-redacted text is
sliced into **overlapping, paragraph-aware chunks** before model inference:

1. Paragraph boundaries (`\n\n`) are preferred.
2. Oversized blocks fall back to sentence / newline splits.
3. Single oversized sentences are hard-split by character count.

Defaults (tunable in `chunking.py`):

| Parameter | Value | Why |
|---|---|---|
| `chunk_size_chars` | 1600 | Comfortably under the model's 384-token context with margin for tokenization expansion |
| `overlap_chars` | 200 | Recovers entities that straddle chunk boundaries |
| `threshold` | 0.7 | Conservative; favours precision over recall |

This keeps peak memory flat regardless of clipboard size. **Latency scales
roughly linearly with the number of chunks**, so a very large clipboard takes
proportionally longer — but the process remains one-shot, on-demand, and frees
all resources on exit. We deliberately did **not** solve this by switching to a
larger-context model: chunking fixes the real bottleneck (context window, not
model intelligence) with far less RAM and startup cost.

## Logs (never your text)

Logs go to `~/.pii_redactor/redactor.log` (rotating, 5 MB, zip-compressed,
10 kept). Each run records:

- run id (12-char hex)
- input length and output length
- regex match counts by type
- model match counts by type
- device used (`mps` or `cpu`)
- elapsed seconds
- clipboard write success/failure

**Raw clipboard text is never logged.** Match counts and lengths only.

## Project layout

```
pyproject.toml
src/redactor/
  __init__.py
  clipboard.py        # pbpaste / pbcopy wrapper
  regex_redactor.py   # compiled-once patterns + overlap resolution
  chunking.py         # paragraph-aware chunking + span merge helpers
  model_redactor.py   # lazy single GLiNER load, MPS+fp16, chunked inference
  pipeline.py         # regex -> chunked model, no double-mask
  logging_setup.py    # loguru console + rotating file
  main.py             # Typer CLI: read -> redact -> write
scripts/
  bootstrap.sh            # one-paste idempotent setup (installs to ~/.local)
  build_shortcut.py       # generates + signs dist/Redact PII.shortcut
                           # (points at ~/.local/bin/pii-redact-clipboard)
  redact_clipboard.py     # Raycast Script Command (optional trigger)
```

## Troubleshooting

### Shortcut works in Shortcuts.app but fails via hotkey/Quick Action with "Operation not permitted"

This is macOS TCC, not a bug in the redactor. External Shortcut triggers
(keyboard hotkey, Services menu, Quick Action) run inside a **sandboxed**
`BackgroundShortcutRunner` XPC that inherits the permissions of the
triggering app, not Shortcuts.app. On macOS 14+ that sandbox is blocked from
executing or reading scripts under **Desktop, Documents, and Downloads** —
even when Shortcuts.app has Full Disk Access.

Fix (already baked into this repo's setup): `scripts/bootstrap.sh` installs
the runnable bits under `~/.local/` (not TCC-protected), and the Shortcut
calls a fixed launcher there. The repo clone can live anywhere.

If you are still hitting it after a fresh setup, check:

- `~/.local/bin/pii-redact-clipboard` exists and is executable
- The Shortcut's Run Shell Script calls `bash "$HOME/.local/bin/pii-redact-clipboard"`
  (or an expanded absolute path to it)
- Shortcuts.app is in **System Settings → General → Login Items** (so the
  hotkey fires reliably without the app in the foreground)

References:
- [Operation Not Permitted: Spotlight, Apple Shortcuts, and Shell Script](https://frkd.dev/tech/apple-shortcuts-shell-operation-not-permitted/)
- [Shortcuts Shell Script cannot access User's folders when executed via Quick Action](https://discussions.apple.com/thread/255170532)

## License

Apache-2.0 (matches the GLiNER model and library).
