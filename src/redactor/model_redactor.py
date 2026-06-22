"""Contextual model redaction layer using a single small GLiNER2 PII model.

- Model: fastino/gliner2-privacy-filter-PII-multi (Apache-2.0, ~0.3B params,
  mDeBERTa-v3-base backbone). Trained on a 42-label PII taxonomy across 7
  European languages; Romanized Indian names/cities tested empirically.
- Library: gliner2 (successor to gliner). API is extract_entities().
- Device: MPS on Apple Silicon, else CPU. Loaded once, lazily.
- Precision: fp32 on both MPS and CPU. fp16 via .half() is unavailable because
  the model ships as safetensors-only and transformers rejects fp16 load.
- Chunked inference: the regex-redacted text is sliced into overlapping,
  paragraph-aware chunks (see chunking.py) before being sent to the model.
  This keeps peak memory flat regardless of clipboard size.
- Labels: a subset of the model's 42 trained labels, chosen to not overlap
  with what the regex layer already owns (email, phone, IP, secret, address).
  Net-new coverage vs the old model: city, passport number, credit card
  number, iban, username, social security number, bank account number.
- Masking runs only on text the regex layer did NOT already cover, so
  placeholders never get re-masked.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os

from loguru import logger

from .chunking import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_OVERLAP,
    Chunk,
    chunk_text,
    merge_spans,
    remap_chunk_entities_to_global_offsets,
)

DEFAULT_MODEL_ID = "fastino/gliner2-privacy-filter-PII-multi"
# Canonical labels from the model's 42-label trained taxonomy
# (https://huggingface.co/fastino/gliner2-privacy-filter-PII-multi). The model is
# label-conditioned, so passing the exact training-time label strings maximises
# recall. Email, phone, IP, secret, password, api_key are intentionally omitted
# because the regex layer already handles those deterministically. 'age' is
# zero-shot on this checkpoint (not in the 42 trained labels) but kept for
# continuity with prior behaviour. 'organization' and 'location' are also not in
# the taxonomy but work zero-shot via DeBERTa-v3 for the India certificate use
# case; flagged as lower-confidence than the canonical labels below.
LABELS = [
    "person",
    "organization",            # zero-shot; kept for India use case
    "city",                    # canonical; fixes Bengaluru miss
    "location",                # zero-shot; broader than city (neighborhoods, regions)
    "address",                 # single-line; multi-line owned by regex layer
    "age",                     # zero-shot; kept for continuity
    "date_of_birth",           # canonical (was "date of birth")
    "passport_number",         # canonical (was "passport number")
    "card_number",             # canonical (was "credit card number")
    "iban",                    # canonical
    "username",                # canonical
    "government_id",           # canonical (was "social security number") — covers SSN/ITIN/national IDs
    "bank_account",            # canonical (was "bank account number")
    "drivers_license_number",  # canonical; US-relevant
    "routing_number",          # canonical; US bank routing
    "postal_code",             # canonical; HIPAA Safe Harbor identifier (PIN/ZIP)
]
# 0.75 chosen empirically: at 0.70 the gliner2-PII checkpoint over-masks common
# noun phrases (e.g. "brown fox", "lazy dog" -> PERSON). 0.75 eliminates those
# false positives while keeping all true positives on Indian test cases.
DEFAULT_THRESHOLD = 0.75

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


@dataclass(frozen=True)
class ModelConfig:
    model_id: str = DEFAULT_MODEL_ID
    threshold: float = DEFAULT_THRESHOLD
    disable_model: bool = False


_TRUE_VALUES = {"1", "true", "yes", "on"}


def load_config() -> ModelConfig:
    """Load model-related configuration from environment variables."""
    model_id = os.environ.get("PII_REDACTOR_MODEL_ID", DEFAULT_MODEL_ID).strip() or DEFAULT_MODEL_ID

    threshold_raw = os.environ.get("PII_REDACTOR_MODEL_THRESHOLD")
    threshold = DEFAULT_THRESHOLD
    if threshold_raw is not None:
        try:
            threshold = float(threshold_raw)
        except ValueError as exc:
            raise ValueError(
                "PII_REDACTOR_MODEL_THRESHOLD must be a float between 0.0 and 1.0"
            ) from exc
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("PII_REDACTOR_MODEL_THRESHOLD must be between 0.0 and 1.0")

    disable_model = os.environ.get("PII_REDACTOR_DISABLE_MODEL", "").strip().lower() in _TRUE_VALUES
    return ModelConfig(model_id=model_id, threshold=threshold, disable_model=disable_model)


def pick_device() -> str:
    """Prefer MPS on Apple Silicon; fall back to CPU."""
    try:
        import torch
    except ImportError:  # pragma: no cover - torch is a hard dep
        return "cpu"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


@lru_cache(maxsize=None)
def _load_model(model_id: str):
    """Load GLiNER2 once per process. Cached to amortize across calls."""
    from gliner2 import GLiNER2  # imported lazily so CLI --help stays fast
    # gliner2 prints a "Model Configuration" banner to stdout on instantiation
    # with no quiet/verbose flag. Redirect stdout during load so the `text`
    # CLI subcommand (which writes only the redacted text to stdout for piping)
    # does not get contaminated.
    import contextlib
    import io

    device = pick_device()
    # fp32 on both MPS and CPU. fp16 via .half() fails on this checkpoint
    # because the model ships as safetensors-only; transformers rejects the
    # fp16 load path with a HuggingFace 404 for pytorch_model.bin.
    logger.info("loading GLiNER2 model={} device={}", model_id, device)
    with contextlib.redirect_stdout(io.StringIO()):
        model = GLiNER2.from_pretrained(model_id, map_location=device)
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
    threshold: float,
) -> list[tuple[int, int, str]]:
    """Run GLiNER2 on each chunk; return merged global-offset (start, end, label).

    GLiNER2's extract_entities returns {"entities": {label: [{text,start,end,confidence}]}}.
    We flatten that into the list-of-dicts shape that
    remap_chunk_entities_to_global_offsets expects (start, end, label keys).
    """
    raw: list[tuple[int, int, str]] = []
    for chunk in chunks:
        if not chunk.text.strip():
            continue
        try:
            result = model.extract_entities(
                chunk.text,
                LABELS,
                threshold=threshold,
                include_spans=True,
            )
        except Exception as exc:  # never let the model layer kill the pipeline
            logger.error("model.extract_entities failed on chunk: {}", type(exc).__name__)
            continue
        # Flatten {label: [{start,end,text,confidence}]} -> [{start,end,label}]
        flat: list[dict] = []
        for label, ents in result.get("entities", {}).items():
            for ent in ents:
                if ent.get("text", "").strip().lower() in _STOPWORDS:
                    continue
                flat.append({"start": ent["start"], "end": ent["end"], "label": label})
        raw.extend(remap_chunk_entities_to_global_offsets(flat, chunk))
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
    config = load_config()
    model, device = _load_model(config.model_id)

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
        "model_pass chunks={} chunk_size={} overlap={} threshold={}",
        len(all_chunks), chunk_size, overlap, config.threshold,
    )

    abs_spans = _run_model_on_chunks(model, all_chunks, threshold=config.threshold)
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
