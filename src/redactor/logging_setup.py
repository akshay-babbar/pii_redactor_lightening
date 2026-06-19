"""Loguru logging setup: console + rotating file sink.

Never logs raw clipboard text. Only metadata: run id, lengths, match counts,
device, elapsed time, clipboard write status.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from loguru import logger

DEFAULT_LOG_DIR = Path(os.environ.get("PII_REDACTOR_LOG_DIR", Path.home() / ".pii_redactor"))


def setup_logging(log_dir: Path | str | None = None) -> str:
    """Configure loguru sinks. Returns the run id for this process.

    Console sink: human-friendly, INFO+.
    File sink: rotating, DEBUG+, never expires automatically.
    """
    log_path = Path(log_dir) if log_dir else DEFAULT_LOG_DIR
    log_path.mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(
        sys.stderr,
        level=os.environ.get("PII_REDACTOR_CONSOLE_LEVEL", "INFO"),
        format="<green>{time:HH:mm:ss.SSS}</green> | <level>{level:<7}</level> | {message}",
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        log_path / "redactor.log",
        level="DEBUG",
        rotation="5 MB",
        retention=10,
        compression="zip",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<7} | {process} | {message}",
        backtrace=True,
        diagnose=False,
        enqueue=True,
    )

    run_id = uuid.uuid4().hex[:12]
    logger.info("run_id={} log_dir={}", run_id, str(log_path))
    return run_id
