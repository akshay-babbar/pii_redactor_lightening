"""End-to-end redaction pipeline: regex pass, then model pass.

Never raises on text content. Surfaces a structured result so the caller can log
match counts and timing without touching raw text.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from loguru import logger

from . import model_redactor
from . import regex_redactor


@dataclass(frozen=True)
class PipelineResult:
    text: str
    regex_counts: dict[str, int] = field(default_factory=dict)
    model_counts: dict[str, int] = field(default_factory=dict)
    device: str = "cpu"
    num_chunks: int = 0
    elapsed_s: float = 0.0


def redact(text: str, run_id: str) -> PipelineResult:
    """Redact text: regex first (global), then chunked model pass."""
    if not text or not text.strip():
        logger.info("run_id={} empty_input=1", run_id)
        return PipelineResult(text=text or "")

    t0 = time.perf_counter()

    rx = regex_redactor.redact(text)
    total_regex_hits = sum(rx.counts.values())
    logger.info(
        "run_id={} input_len={} stage=regex regex_hits={} regex_matches={}",
        run_id, len(text), total_regex_hits, rx.counts,
    )

    config = model_redactor.load_config()
    if config.disable_model:
        elapsed = time.perf_counter() - t0
        logger.info(
            "run_id={} stage=model skipped=1 reason=disabled_by_env regex_hits={} elapsed_s={:.3f}",
            run_id, total_regex_hits, elapsed,
        )
        return PipelineResult(
            text=rx.text,
            regex_counts=rx.counts,
            model_counts={},
            device="disabled",
            num_chunks=0,
            elapsed_s=elapsed,
        )

    mr = model_redactor.redact(rx.text, covered=rx.covered)
    total_model_hits = sum(mr.counts.values())
    logger.info(
        "run_id={} stage=model chunks={} model_hits={} model_matches={} device={}",
        run_id, mr.num_chunks, total_model_hits, mr.counts, mr.device,
    )

    elapsed = time.perf_counter() - t0
    logger.info(
        "run_id={} stage=done device={} chunks={} regex_hits={} model_hits={} elapsed_s={:.3f}",
        run_id, mr.device, mr.num_chunks, total_regex_hits, total_model_hits, elapsed,
    )

    return PipelineResult(
        text=mr.text,
        regex_counts=rx.counts,
        model_counts=mr.counts,
        device=mr.device,
        num_chunks=mr.num_chunks,
        elapsed_s=elapsed,
    )
