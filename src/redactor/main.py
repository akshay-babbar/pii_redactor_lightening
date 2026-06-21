"""CLI glue for the clipboard redaction roundtrip.

Flow: read clipboard -> redact -> write clipboard back. All logging uses metadata
only; raw clipboard text is never logged.
"""

from __future__ import annotations

import typer
from loguru import logger

from . import clipboard
from . import pipeline as pipeline_mod
from .logging_setup import setup_logging

app = typer.Typer(add_completion=False, help="Local clipboard PII redactor.")


@app.command()
def run() -> None:
    """Read clipboard, redact PII, write redacted text back to clipboard."""
    run_id = setup_logging()
    logger.info("run_id={} stage=read_clipboard", run_id)
    original = clipboard.read_clipboard()

    if not original:
        logger.warning("run_id={} clipboard_empty=1 nothing_to_do=1", run_id)
        typer.echo("Clipboard is empty or not text; nothing to redact.")
        raise typer.Exit(code=0)

    logger.info("run_id={} input_len={}", run_id, len(original))

    try:
        result = pipeline_mod.redact(original, run_id)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    total = sum(result.regex_counts.values()) + sum(result.model_counts.values())
    if total == 0:
        logger.info("run_id={} no_pii_detected=1", run_id)
        typer.echo("No PII detected; clipboard left unchanged.")
        raise typer.Exit(code=0)

    ok = clipboard.write_clipboard(result.text)
    logger.info(
        "run_id={} clipboard_write_ok={} output_len={}",
        run_id, ok, len(result.text),
    )
    summary = ", ".join(
        f"{k}={v}" for k, v in {**result.regex_counts, **result.model_counts}.items() if v
    )
    if ok:
        typer.echo(f"Redacted {total} span(s): {summary}")
    else:
        typer.echo("Redaction computed but clipboard write failed. See logs.")
        raise typer.Exit(code=1)


@app.command()
def text() -> None:
    """Read text from stdin, redact PII, write redacted text to stdout."""
    import sys
    run_id = setup_logging()
    original = sys.stdin.read()
    if not original.strip():
        raise typer.Exit(code=0)
    try:
        result = pipeline_mod.redact(original, run_id)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)
    typer.echo(result.text, nl=False)


@app.command()
def version() -> None:
    """Print version."""
    from . import __version__
    typer.echo(__version__)


if __name__ == "__main__":
    app()
