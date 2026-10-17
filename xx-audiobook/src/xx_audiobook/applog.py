"""On-phone log ring: file under the state dir plus in-memory lines.

Secrets are redacted before any write or share. Mirrors log/AppLog.kt and
the xx-vitals applog shape (bounded ring, capped reads).
"""

from __future__ import annotations

import re
import threading
from collections import deque

RING_SIZE = 300
FILE_CAP_BYTES = 256 * 1024
SHARE_CAP_BYTES = 200 * 1024
LOG_NAME = "xx-audiobook.log"
PREV_NAME = "xx-audiobook.log.1"
CRASH_NAME = "last-crash.txt"

_lock = threading.Lock()
_ring: deque[str] = deque(maxlen=RING_SIZE)
_logger = None
_log_dir = None

_BEARER_RE = re.compile(r"Bearer\s+[A-Za-z0-9._\-~+/=]+")
_SECRET_RE = re.compile(
    r"((?:password|passwd|secret|token|otp|pin)\s*[=:]\s*)(\S+)",
    re.IGNORECASE,
)
_JSON_SECRET_RE = re.compile(
    r'("(?:password|passwd|secret|token|otp|pin)"\s*:\s*")([^"]*)"',
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def redact(line: str) -> str:
    redacted = _BEARER_RE.sub("Bearer [REDACTED]", line)
    redacted = _SECRET_RE.sub(r"\1[REDACTED]", redacted)
    redacted = _JSON_SECRET_RE.sub("\\g<1>[REDACTED]\"", redacted)
    if "otpauth://" in redacted:
        redacted = re.sub(r"otpauth://\S+", "otpauth://[REDACTED]", redacted)
    return _EMAIL_RE.sub("[REDACTED]", redacted)


def _rotate_if_needed(path) -> bool:
    try:
        import os
        if os.path.getsize(path) <= FILE_CAP_BYTES:
            return False
    except OSError:
        return False
    prev = path.parent / PREV_NAME
    try:
        if prev.exists():
            prev.unlink()
    except OSError:
        pass
    try:
        path.rename(prev)
    except OSError:
        return False
    return True


class AppLog:
    def __init__(self) -> None:
        self._file = None
        self._path = None

    def _log(self, level: str, message: str, *args) -> None:
        try:
            text = message % args if args else message
        except (TypeError, ValueError):
            text = message
        line = f"{level} {redact(text)}"
        with _lock:
            _ring.append(line)
        if self._file is not None and self._path is not None:
            try:
                if _rotate_if_needed(self._path) or self._file.closed:
                    try:
                        self._file.close()
                    except OSError:
                        pass
                    self._file = open(str(self._path), "a",
                                      encoding="utf-8")
                self._file.write(line + "\n")
                self._file.flush()
            except OSError:
                pass

    def info(self, message: str, *args) -> None:
        self._log("INFO", message, *args)

    def warning(self, message: str, *args) -> None:
        self._log("WARNING", message, *args)

    def error(self, message: str, *args) -> None:
        self._log("ERROR", message, *args)


def get_logger() -> AppLog:
    global _logger, _log_dir
    with _lock:
        if _logger is None:
            _logger = AppLog()
            try:
                from xx_audiobook import paths as paths_module
                paths_module.ensure_app_dirs()
                path = paths_module.log_file()
                _log_dir = path.parent
                _logger._path = path
                _logger._file = open(str(path), "a", encoding="utf-8")
            except OSError:
                _logger._file = None
                _logger._path = None
        return _logger


def read_recent(limit: int = 300) -> list[str]:
    with _lock:
        lines = list(_ring)
    return lines[-limit:]


def dump() -> str:
    return "\n".join(read_recent(limit=RING_SIZE))


def last_crash() -> str | None:
    directory = _log_dir
    if directory is None:
        try:
            from xx_audiobook import paths as paths_module
            directory = paths_module.state_dir()
        except OSError:
            return None
    crash = directory / CRASH_NAME
    try:
        if not crash.is_file() or crash.stat().st_size == 0:
            return None
        return crash.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def persist_crash(error) -> None:
    import traceback
    try:
        from xx_audiobook import paths as paths_module
        paths_module.ensure_app_dirs()
        crash = paths_module.state_dir() / CRASH_NAME
    except OSError:
        return
    try:
        header = redact(f"crash {type(error).__name__}: {error}")
        detail = redact("".join(traceback.format_exception(error)))
        crash.write_text(header + "\n" + detail + "\n--- recent ---\n"
                         + dump() + "\n", encoding="utf-8")
    except OSError:
        pass


def share_text() -> str:
    parts = []
    crashed = last_crash()
    if crashed:
        parts.append("--- last crash ---")
        parts.append(crashed)
        parts.append("")
    parts.append("--- live ---")
    parts.append(dump())
    text = "\n".join(parts)
    encoded = text.encode("utf-8", "replace")
    if len(encoded) > SHARE_CAP_BYTES:
        encoded = encoded[-SHARE_CAP_BYTES:]
        text = encoded.decode("utf-8", "replace")
    return text


SETUP_SHARE_CAP_BYTES = 100 * 1024


def share_tail(cap_bytes: int = SETUP_SHARE_CAP_BYTES) -> str:
    """Setup's Share logs: the last crash, then the log file's newest
    lines (the in-memory ring when there is no file), capped at
    ``cap_bytes`` from the end and redacted line by line once more."""
    parts = []
    crashed = last_crash()
    if crashed:
        parts += ["--- last crash ---", crashed, ""]
    parts.append("--- log ---")
    body = ""
    directory = _log_dir
    if directory is not None:
        chunks = []
        for name in (PREV_NAME, LOG_NAME):
            try:
                chunks.append((directory / name).read_text(
                    encoding="utf-8", errors="replace"))
            except OSError:
                continue
        body = "".join(chunks)
    parts.append(body.rstrip("\n") if body else dump())
    text = "\n".join(redact(line) for line in "\n".join(parts).split("\n"))
    encoded = text.encode("utf-8", "replace")
    if len(encoded) > cap_bytes:
        text = encoded[-cap_bytes:].decode("utf-8", "replace")
        text = text.split("\n", 1)[-1]
    return text


def reset() -> None:
    global _logger, _log_dir
    with _lock:
        _ring.clear()
        if _logger is not None and _logger._file is not None:
            try:
                _logger._file.close()
            except OSError:
                pass
        _logger = None
        _log_dir = None
