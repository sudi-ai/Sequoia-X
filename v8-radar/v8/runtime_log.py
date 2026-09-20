from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("v8.runtime")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    # The V8 parent and copied discovery child are separate Windows processes.
    # A shared RotatingFileHandler can hold an exclusive rotation lock and make
    # the child's sidecar import fail.  Keep one bounded file per process.
    handler = RotatingFileHandler(
        LOG_DIR / f"v8_runtime_errors_{os.getpid()}.log",
        maxBytes=2_000_000, backupCount=2, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s|%(levelname)s|%(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


LOGGER = _build_logger()


def log_exception(module: str, exc: BaseException, **context: object) -> None:
    details = "|".join(f"{key}={value}" for key, value in sorted(context.items()))
    LOGGER.error("module=%s|error=%s|%s", module, type(exc).__name__, details, exc_info=exc)


def log_warning(module: str, message: str, **context: object) -> None:
    details = "|".join(f"{key}={value}" for key, value in sorted(context.items()))
    LOGGER.warning("module=%s|message=%s|%s", module, message, details)
