"""Paragraph-aware text chunking with overlap, plus span merging helpers.

Used only by the model pass. The regex pass always runs on the full text first;
this module slices the *regex-redacted, uncovered* text into model-safe chunks.

Chunking strategy: try separators in priority order (paragraph -> sentence ->
char). Whenever accumulated text would exceed `chunk_size`, emit a chunk, keep
the last `overlap` chars as the seed of the next one, and continue. This is a
single recursive-pass algorithm; no nested buffers per separator tier.

Hard guarantee used by the caller: `text[chunk.start:chunk.end] == chunk.text`.
This is what lets us remap model spans back to global offsets safely. We do NOT
rely on any external chunker because every candidate library tested
(langchain `RecursiveCharacterTextSplitter`, `chunkweaver`) violates this
invariant when overlap is enabled — see git history.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_CHUNK_SIZE = 1600
DEFAULT_OVERLAP = 200

# Separators tried in priority order. Paragraph break first (keeps separator
# attached to the preceding piece), then sentence end, then hard char split.
_SENTENCE_END = re.compile(r"(?<=[.!?])[\"'\")\]]?\s+")
_PARAGRAPH = re.compile(r"\n{2,}")


@dataclass(frozen=True)
class Chunk:
    """A slice of text with its absolute offset in the source string."""
    start: int
    end: int
    text: str


def _flatten(text: str, size: int, overlap: int) -> list[tuple[int, int]]:
    """Return absolute (start, end) ranges covering `text` with `size`+`overlap`.

    Recursive descent: try paragraphs; if a unit still exceeds `size`, recurse
    into sentences; if a sentence still exceeds `size`, hard char-split it.
    """
    if len(text) <= size:
        return [(0, len(text))]

    units = _split_units(text)
    # If the coarsest separator found nothing finer than the whole text, we are
    # at the bottom of the hierarchy -> hard char-split to avoid infinite recursion.
    if len(units) == 1 and units[0] == (0, len(text)):
        return _hard_split_ranges(len(text), size, overlap)

    ranges: list[tuple[int, int]] = []
    cursor = 0          # end of last emitted chunk
    buf_start = 0       # start of the in-progress chunk
    for u_start, u_end in units:
        if u_end - buf_start <= size:
            cursor = u_end
            continue
        if cursor > buf_start:
            ranges.append((buf_start, cursor))
            buf_start = max(buf_start, cursor - overlap) if overlap > 0 else cursor
        if u_end - u_start > size:
            sub = _flatten(text[u_start:u_end], size, overlap)
            ranges.extend((u_start + s, u_start + e) for s, e in sub)
            buf_start = u_end
            cursor = u_end
        else:
            buf_start = u_start
            cursor = u_end
    if cursor > buf_start:
        ranges.append((buf_start, cursor))
    return ranges


def _hard_split_ranges(n: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """Pure character split. Always progresses by at least 1 char."""
    if n == 0:
        return []
    step = max(1, size - overlap)
    out: list[tuple[int, int]] = []
    i = 0
    while i < n:
        end = min(i + size, n)
        out.append((i, end))
        if end >= n:
            break
        i += step
    return out


def _split_units(text: str) -> list[tuple[int, int]]:
    """Yield (start, end) ranges for the coarsest available separator.

    Paragraphs if any, else sentences, else the whole string as one unit.
    """
    if _PARAGRAPH.search(text):
        return _ranges_from_regex(text, _PARAGRAPH)
    if _SENTENCE_END.search(text):
        return _ranges_from_regex(text, _SENTENCE_END)
    return [(0, len(text))]


def _ranges_from_regex(text: str, pattern: re.Pattern[str]) -> list[tuple[int, int]]:
    """Split text on `pattern`. Separator stays attached to preceding piece.

    This preserves both the coverage invariant (every char of `text` is in some
    chunk) and the slice invariant (text[start:end] == chunk.text) when the
    caller slices with these ranges.
    """
    ranges: list[tuple[int, int]] = []
    last = 0
    for m in pattern.finditer(text):
        ranges.append((last, m.end()))   # include separator with preceding piece
        last = m.end()
    if last < len(text):
        ranges.append((last, len(text)))
    return ranges or [(0, len(text))]


def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Paragraph-aware chunking with overlap. Returns absolute-offset chunks.

    Order is preserved. Empty input returns [].
    Hard invariant: ``text[c.start:c.end] == c.text`` for every chunk c.
    """
    if not text:
        return []
    return [
        Chunk(start=s, end=e, text=text[s:e])
        for s, e in _flatten(text, chunk_size, overlap)
    ]


def remap_chunk_entities_to_global_offsets(
    chunk_entities: list[dict],
    chunk: Chunk,
) -> list[tuple[int, int, str]]:
    """Convert chunk-local (start,end,label) dicts to global (start,end,label)."""
    return [
        (chunk.start + e["start"], chunk.start + e["end"], e["label"])
        for e in chunk_entities
    ]


def merge_spans(spans: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    """De-duplicate overlapping/duplicate spans. Longest wins at each start.

    Spans from chunk overlap regions will appear twice; this collapses them.
    A span is dropped if it is fully contained in the previous kept span.
    """
    if not spans:
        return []
    spans = sorted(spans, key=lambda s: (s[0], -(s[1] - s[0])))
    kept: list[tuple[int, int, str]] = []
    for start, end, label in spans:
        if kept and start < kept[-1][1] and end <= kept[-1][1]:
            continue
        kept.append((start, end, label))
    return kept
