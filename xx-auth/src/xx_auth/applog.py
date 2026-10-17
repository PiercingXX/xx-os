"""Log redaction. Port of Android `AppLog.redact`: the second guard that
strips secrets, otpauth URIs, passwords and e-mail addresses from every
diagnostic line. The Linux port keeps redaction plus an in-memory ring only:
no log file, no crash uploader, no share path (log upload is out of scope)."""

from __future__ import annotations

import collections
import re
import time

_BEARER = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_PAIRS = re.compile(r"(?i)(password|passwd|secret|token|otp|pin)\s*[=:]\s*\S+")
_EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[A-Za-z]{2,}")
_OTPAUTH = re.compile(r"otpauth://\S+", re.IGNORECASE)

RING_SIZE = 300


def redact(message: str) -> str:
    text = _BEARER.sub("Bearer ***", message)
    text = _PAIRS.sub(lambda m: m.group(1) + "=***", text)
    text = _EMAIL.sub("***@***", text)
    text = _OTPAUTH.sub("otpauth://***", text)
    return text


class LogRing:
    def __init__(self, size: int = RING_SIZE) -> None:
        self._lines: collections.deque[tuple[int, str, str]] = collections.deque(maxlen=size)

    def emit(self, tag: str, message: str) -> None:
        self._lines.append((int(time.time() * 1000), tag, redact(message)))

    def dump(self) -> str:
        return "\n".join(f"{ts} {tag} {msg}" for ts, tag, msg in self._lines)
