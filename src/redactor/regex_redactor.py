"""Deterministic regex redaction layer.

Compiled once at import. Replaces exact sensitive spans with typed placeholders
like [EMAIL], [PHONE], [PAN]. Returns the redacted text plus a list of covered
character ranges so the model layer can skip already-redacted regions.

Design notes:
- Patterns use word boundaries / context where it cuts false positives cheaply.
- "Long numeric" catches card/account/UID-shaped sequences without trying to be
  a strict Luhn validator (avoid brittle false negatives on Indian banking).
- Secret/token pattern targets high-entropy alphanumeric blobs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Label : compiled regex. Order matters only for readability; overlap is resolved
# by the union pass below, which picks the longest match at each position.
PATTERNS: dict[str, re.Pattern[str]] = {
    # user@host.tld; tolerate plus-addressing and subdomains
    "EMAIL": re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    # Indian mobile / landline AND US/NANP phones. Indian branches first
    # (existing behaviour preserved), then US branches requiring a separator
    # (. - or space) or parens so they don't collide with the unseparated
    # Indian 10-digit branch. NANP area/exchange codes start with [2-9].
    "PHONE": re.compile(
        r"(?:(?:\+91|91|0)[\s\-]?)?"
        r"(?:\+91[\s\-]?\d{5}[\s\-]?\d{5}|"
        r"\b[6-9]\d{9}\b|"
        r"\b0\d{2,4}[\s\-]?\d{6,8}\b|"
        # US/NANP: +1 NXX NXX-XXXX, (NXX) NXX-XXXX, NXX-NXX-XXXX, NXX.NXX.XXXX
        r"\+1[\s\-]?\(?[2-9]\d{2}\)?[\s\-\.][2-9]\d{2}[\s\-\.]\d{4}|"
        r"\(?[2-9]\d{2}\)?[\s\-\.][2-9]\d{2}[\s\-\.]\d{4})"
    ),
    # Permanent Account Number: 5 letters, 4 digits, 1 letter
    "PAN": re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"),
    # Aadhaar-like 12 digit (with optional spaces/dashes); XX YYYY ZZZZ ZZZZ pattern
    "AADHAAR": re.compile(r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b"),
    # UPI: name@bank (handle 2+ alpha chars)
    "UPI": re.compile(r"\b[A-Za-z0-9.\-_]{2,64}@[A-Za-z][A-Za-z0-9]{1,63}\b"),
    # IPv4
    "IP": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    # Long secret / token: 20+ of base64url / hex / classic api-key alphabet
    "SECRET": re.compile(r"\b[A-Za-z0-9_\-]{20,}\b"),
    # Card / account-like long numeric (12-19 digits, optional separators)
    "LONGNUM": re.compile(r"\b\d[\d\s\-]{14,22}\d\b|\b\d{12,19}\b"),
    # Multi-line Indian address anchored on a valid 6-digit PIN code. The pattern
    # is a candidate finder; the PIN is validated against the India Post registry
    # via bharataddress before the span is kept. Catches multi-line addresses
    # that the GLiNER model misses (model fails on newline-spanning addresses).
    # Negative lookahead skips labelled form-style documents (Floor No.:, Building
    # No.:, Locality:, City:, State:, PIN Code:, Road/Street:, District:, Name Of)
    # so each address line falls through to per-line GLiNER redaction, preserving
    # the structural labels instead of collapsing the whole block into [ADDRESS].
    "ADDRESS": re.compile(
        r"(?!(?:[^\n]*\n){0,8}[^\n]*(?:Floor\s*No|Building\s*No|Name\s*Of|Locality|City/Town|State|PIN\s*Code|Road/Street|District))"
        r"(?:Flat|H\.?\s*No|House|Door|Plot|Tower|Block|Sector|Survey|No\.?)\s*"
        r"(?:No\.?\s*)?\d+[A-Za-z]?\s*,?\s*\n?"
        r"[A-Za-z0-9\s,.\-/]{4,120}?\n"
        r"[A-Za-z0-9\s,.\-/]{4,80}?\s+"
        r"\b[1-8]\d{5}\b"
        r"(?:\s*,?\s*[A-Za-z]{3,40})?"
    ),
    # US Social Security Number: XXX-XX-XXXX with SSA validation rules.
    # Rejects area 000, 666, 9xx (ITIN, not SSN) and group/serial 00/0000.
    # Only the canonical dashed form is matched; the unseparated 9-digit form
    # is intentionally skipped (too high false-positive risk on invoice/order IDs).
    "SSN": re.compile(
        r"\b(?!000|666|9\d{2})\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"
    ),
}

# 6-digit PIN candidate, used to extract the PIN for validation from any ADDRESS match.
_PIN_RE = re.compile(r"\b[1-8]\d{5}\b")


def _pin_is_valid(pin: str) -> bool:
    """True if `pin` is in the India Post registry (via bharataddress).

    Lazy-imported so CLI cold-start for non-address commands is unaffected.
    Fails closed (returns False) on any error -> span is dropped, never over-masked.
    """
    try:
        from bharataddress.pincode import is_valid
        return bool(is_valid(pin))
    except Exception:
        return False


@dataclass(frozen=True)
class RegexResult:
    text: str
    covered: list[tuple[int, int]]  # char ranges masked in the OUTPUT text
    counts: dict[str, int]


def redact(text: str) -> RegexResult:
    """Redact all pattern matches. Returns new text + covered ranges + counts."""
    # Collect every match as (start, end, label) against the ORIGINAL text.
    # ADDRESS candidates are PIN-validated; invalid ones are dropped (fail-closed).
    spans: list[tuple[int, int, str]] = []
    for label, pattern in PATTERNS.items():
        for m in pattern.finditer(text):
            if label == "ADDRESS":
                pin_match = _PIN_RE.search(m.group())
                if not pin_match or not _pin_is_valid(pin_match.group()):
                    continue
            spans.append((m.start(), m.end(), label))

    if not spans:
        return RegexResult(text=text, covered=[], counts={})

    # Resolve overlaps: sort by start asc, then by length desc (longest wins).
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    kept: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, label in spans:
        if start >= last_end:
            kept.append((start, end, label))
            last_end = end

    # Build output, tracking covered ranges in the OUTPUT string.
    counts: dict[str, int] = {}
    out_parts: list[str] = []
    covered: list[tuple[int, int]] = []
    cursor = 0
    for start, end, label in kept:
        out_parts.append(text[cursor:start])
        placeholder = f"[{label}]"
        covered.append((len("".join(out_parts)), len("".join(out_parts)) + len(placeholder)))
        out_parts.append(placeholder)
        counts[label] = counts.get(label, 0) + 1
        cursor = end
    out_parts.append(text[cursor:])
    return RegexResult(text="".join(out_parts), covered=covered, counts=counts)
