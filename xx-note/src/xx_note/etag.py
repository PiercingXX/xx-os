"""HTTP validator strength + body digest (port of ``core/Etag.kt``)."""

from __future__ import annotations

import hashlib

WEAK_PREFIX = "W/"


def is_strong(value: str | None) -> bool:
    return bool(value and value.strip()) and not value.startswith(WEAK_PREFIX)


def is_weak(value: str | None) -> bool:
    return value is not None and value.startswith(WEAK_PREFIX)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
