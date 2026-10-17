"""The rotating, redacted application log file.

One stdlib logger writing ``<state dir>/<app>.log`` through a
``RotatingFileHandler``. Every record passes through
:func:`xx_music.diagnostics.redact_text`, so cookies, tokens and hashes
never reach the disk even when a caller logs a raw header dict by mistake.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from xx_music.diagnostics import redact_text
from xx_music.paths import AppPaths

LOG_MAX_BYTES = 256 * 1024
LOG_BACKUPS = 2
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class _RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def setup(path: Path | str, *,
          max_bytes: int = LOG_MAX_BYTES,
          backups: int = LOG_BACKUPS,
          logger_name: str = "xx-radio",
          level: int = logging.INFO) -> logging.Logger:
    """Create (or reuse) the redacted rotating logger for ``path``.

    The parent directory is created when missing and chmodded 0700 only
    then, so first run on a clean device can open the log. Safe to call
    twice: a handler for the same file is added once.
    """
    target = Path(path)
    if not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(target.parent, 0o700)
    logger = logging.getLogger(logger_name)
    logger.setLevel(level)
    logger.propagate = False
    for handler in logger.handlers:
        if (isinstance(handler, RotatingFileHandler)
                and Path(handler.baseFilename) == target):
            return logger
    handler = RotatingFileHandler(str(target), maxBytes=max_bytes,
                                  backupCount=backups, encoding="utf-8")
    handler.setFormatter(_RedactingFormatter(LOG_FORMAT))
    logger.addHandler(handler)
    return logger


def default_path(dir_name: str) -> Path:
    """The log file for one app built on xx_music (its state dir)."""
    return AppPaths(dir_name).log_file()
