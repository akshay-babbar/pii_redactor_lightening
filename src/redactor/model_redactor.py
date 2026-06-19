"""Contextual model redaction layer using a single small GLiNER PII model.

- Model: urchade/gliner_multi_pii-v1 (Apache-2.0, ~0.3B params, DeBERTa-small backbone).
- Device: MPS on Apple Silicon, else CPU. Loaded once, lazily.
- Precision: fp16 on MPS (cheap, no quality loss for inference); fp32 on CPU.
- Quantization: intentionally skipped. GLiNER int8 path requires a QAT-trained
  model to preserve accuracy, which the chosen model is not. fp16 gives us the
  memory win without that risk.
- Chunked inference: the regex-redacted text is sliced into overlapping,
  paragraph-aware chunks (see chunking.py) before being sent to the model.
  This keeps peak memory flat regardless of clipboard size.
- Labels: person / organization / location / address / age / date of birth.
  Empirically these out-of-perform alternative label sets on this checkpoint
  (the suggested 'name' / 'location address' / etc. labels missed all persons
  in A/B testing). 'age' is zero-shot but tested at 0.88+; 'date of birth' is
  in-distribution.
- Masking is high-confidence only (threshold 0.7) and runs only on text that the
  regex layer did NOT already cover, so placeholders never get re-masked.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from loguru import logger

from .chunking import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_OVERLAP,
    Chunk,
    chunk_text,
    merge_spans,
    remap_chunk_entities_to_global_offsets,
)

MODEL_ID = "urchade/gliner_multi_pii-v1"
# age and date of birth: empirically tested at 0.88-0.99 and 0.72-0.79 confidence
# respectively on this checkpoint (age is zero-shot, DOB is in-distribution).
LABELS = ["person", "organization", "location", "address", "age", "date of birth"]
THRESHOLD = 0.7

# Common English words the PII model over-triggers on as person/org. Coarse guard
# against false positives; not a precision system. Add observed offenders only.
_STOPWORDS = {
    "pan", "tan", "fan", "can", "man", "pin", "tin", "win",
    "customer", "office", "finance", "patient", "client",
}


@dataclass(frozen=True)
class ModelResult:
    text: str
    counts: dict[str, int]
    device: str
    num_chunks: int = 0


def pick_device() -> str:
    """Prefer MPS on Apple Silicon; fall back to CPU."""
    try:
        import torch
    except ImportError:  # pragma: no cover - torch is a hard dep
        return "cpu"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


@lru_cache(maxsize=1)
def _load_model():
    """Load GLiNER once per process. Cached to amortize across calls."""
    from gliner import GLiNER  # imported lazily so CLI --help stays fast

    device = pick_device()
    # fp16 on MPS is safe and halves memory. CPU keeps fp32 (fp16 on CPU is slow).
    dtype = "fp16" if device == "mps" else None
    logger.info("loading GLiNER model={} device={} dtype={}", MODEL_ID, device, dtype)
    model = GLiNER.from_pretrained(MODEL_ID, map_location=device, dtype=dtype)
    model.eval()
    return model, device


def _uncovered_segments(text: str, covered: list[tuple[int, int]]) -> list[tuple[int, str]]:
    """Return (start, substring) for each region of `text` not in `covered`."""
    segments: list[tuple[int, str]] = []
    cursor = 0
    for start, end in sorted(covered):
        if start > cursor:
            segments.append((cursor, text[cursor:start]))
        cursor = max(cursor, end)
    if cursor < len(text):
        segments.append((cursor, text[cursor:]))
    return segments


def _run_model_on_chunks(
    model,
    chunks: list[Chunk],
) -> list[tuple[int, int, str]]:
    """Run GLiNER on each chunk; return merged global-offset (start, end, label)."""
    raw: list[tuple[int, int, str]] = []
    for chunk in chunks:
        if not chunk.text.strip():
            continue
        try:
            ents = model.predict_entities(chunk.text, LABELS, threshold=THRESHOLD)
        except Exception as exc:  # never let the model layer kill the pipeline
            logger.error("model.predict_entities failed on chunk: {}", type(exc).__name__)
            continue
        for ent in ents:
            if ent["text"].strip().lower() in _STOPWORDS:
                continue
        raw.extend(remap_chunk_entities_to_global_offsets(
            [e for e in ents if e["text"].strip().lower() not in _STOPWORDS],
            chunk,
        ))
    return merge_spans(raw)


def redact(
    text: str,
    covered: list[tuple[int, int]] | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
) -> ModelResult:
    """Mask high-confidence contextual entities outside already-redacted spans.

    Pipeline: split text into uncovered segments -> chunk each segment ->
    run GLiNER per chunk -> remap to global offsets -> merge overlap dups ->
    apply non-overlapping masking to `text`.

    `covered` are character ranges already masked by the regex pass (in `text`).
    """
    covered = covered or []
    model, device = _load_model()

    # The model only ever sees text regex did NOT cover.
    segments = _uncovered_segments(text, covered)
    if not segments:
        return ModelResult(text=text, counts={}, device=device, num_chunks=0)

    # Chunk each uncovered segment independently, preserving offsets.
    # chunk_text returns offsets relative to its input; shift by seg_start to
    # make them global within `text`.
    all_chunks: list[Chunk] = []
    for seg_start, seg_text in segments:
        for c in chunk_text(seg_text, chunk_size=chunk_size, overlap=overlap):
            all_chunks.append(Chunk(
                start=seg_start + c.start,
                end=seg_start + c.end,
                text=c.text,
            ))

    if not all_chunks:
        return ModelResult(text=text, counts={}, device=device, num_chunks=0)

    logger.info(
        "model_pass chunks={} chunk_size={} overlap={}",
        len(all_chunks), chunk_size, overlap,
    )

    abs_spans = _run_model_on_chunks(model, all_chunks)
    if not abs_spans:
        return ModelResult(text=text, counts={}, device=device, num_chunks=len(all_chunks))

    # Apply non-overlapping masking; defensively skip any span that touches a
    # regex-covered range (shouldn't happen given segment-split, but cheap guard).
    covered_set = list(covered)
    out_parts: list[str] = []
    counts: dict[str, int] = {}
    cursor = 0
    for start, end, label in abs_spans:
        if start < cursor:
            continue
        if any(not (end <= c_start or start >= c_end) for c_start, c_end in covered_set):
            continue
        out_parts.append(text[cursor:start])
        out_parts.append(f"[{label.upper()}]")
        counts[label] = counts.get(label, 0) + 1
        cursor = end
    out_parts.append(text[cursor:])
    return ModelResult(
        text="".join(out_parts),
        counts=counts,
        device=device,
        num_chunks=len(all_chunks),
    )
