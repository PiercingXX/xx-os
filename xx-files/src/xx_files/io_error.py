"""IO failures become one honest line, never a crash.

Port of the Android ``IoError`` JVM seam (todo.md WS3): the
exception's own message when it carries a non-blank one, a stable
fallback otherwise.
"""
from __future__ import annotations

FALLBACK = "Could not complete that operation"


def message(exc: BaseException | None) -> str:
    useful = str(exc).strip() if exc is not None else ""
    return useful if useful else FALLBACK
