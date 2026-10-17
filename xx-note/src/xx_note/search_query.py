"""FTS-safe search sanitizer (port of ``ui/grid/SearchQuery.kt``).

Free user text becomes an inert token list the local index can match
without ever surfacing query syntax. Mirrors the Android rules: strip
controls and double quotes, drop content-less terms, at most 64 terms.
"""

from __future__ import annotations

import re

MAX_TERMS = 64
OPERATOR_WORDS = frozenset({"AND", "OR", "NOT", "NEAR"})

_SPLIT = re.compile(r"\s+")


def _strip_controls(term: str) -> str:
    out = []
    for char in term:
        code = ord(char)
        if char == '"':
            continue
        if code < 0x20 or 0x7F <= code <= 0x9F:
            continue
        out.append(char)
    return "".join(out)


def _has_content(char: str) -> bool:
    return char.isalnum() or char == "_" or ord(char) > 0x7F


def _is_unreserved(char: str) -> bool:
    return (
        "a" <= char <= "z"
        or "A" <= char <= "Z"
        or "0" <= char <= "9"
        or char == "_"
        or ord(char) > 0x7F
    )


def sanitize(raw: str) -> str:
    out: list[str] = []
    for term in _SPLIT.split(raw):
        if len(out) >= MAX_TERMS:
            break
        cleaned = _strip_controls(term).strip("*")
        if not any(_has_content(c) for c in cleaned):
            continue
        out.append(_emit(cleaned))
    return " ".join(out)


def _emit(cleaned: str) -> str:
    bareword_safe = all(_is_unreserved(c) for c in cleaned) and (
        cleaned.upper() not in OPERATOR_WORDS
    )
    if bareword_safe and len(cleaned) == 1:
        return cleaned
    if bareword_safe:
        return f"{cleaned}*"
    if len(cleaned) == 1:
        return f'"{cleaned}"'
    return f'"{cleaned}*"'
