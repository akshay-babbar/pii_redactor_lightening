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

## Setup

```bash
make install
```

That's it. One command does everything:
1. Verifies `uv` is installed, creates `.venv`, installs the package
2. Downloads + caches the GLiNER model (~120 MB, paid once here)
3. Builds the signed `dist/Redact PII.shortcut` for your macOS version
4. Opens it for import and opens Login Items settings

Two clicks you must do yourself (macOS security gates — not scriptable):
- Click **Add** in the Shortcuts import dialog
- Add **Shortcuts.app** to the Login Items list that opens

Then assign a hotkey in Shortcuts.app: open **Redact PII** → info (i) icon →
**Add Keyboard Shortcut** → press a combo (e.g. `⌘⇧R`).

After that: Cmd+A, Cmd+C, press hotkey, paste. Done.

<details>
<summary>Manual steps (for power users or if make install fails)</summary>

```bash
bash scripts/bootstrap.sh
uv run python scripts/build_shortcut.py
open "dist/Redact PII.shortcut"
```

Then in Shortcuts.app: double-click **Redact PII** → info (i) icon →
**Add Keyboard Shortcut**. Add Shortcuts.app to **System Settings → General →
Login Items**.

</details>

### Cold-start latency (honest note)

Each Shortcut run is a **fresh process**, so the GLiNER model (~120 MB) loads
on every invocation. Cold start is ~8–9 s. Bootstrap prewarm only saves the
**download** — the per-invocation load cost remains.

### Rebuild shortcut (after a macOS upgrade)

```bash
make shortcut
```

## Use via CLI

**Clipboard mode** — reads clipboard, redacts in place:

```bash
uv run redact-clipboard run
```

**Pipe mode** — reads stdin, writes redacted text to stdout:

```bash
echo "Call John at 9876543210" | uv run redact-clipboard text
cat report.txt | uv run redact-clipboard text > report_redacted.txt
```

Exits 0 on empty input or no PII; exits 1 only on clipboard write failure (run mode).

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
  bootstrap.sh            # one-paste idempotent setup
  redact_via_shortcut.sh  # absolute-path entrypoint for Shortcuts.app
  build_shortcut.py       # generates + signs dist/Redact PII.shortcut
  redact_clipboard.py     # Raycast Script Command (optional trigger)
```

## License

Apache-2.0 (matches the GLiNER model and library).
