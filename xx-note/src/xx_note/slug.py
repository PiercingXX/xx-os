"""Filesystem slugs for note filenames: ``<ulid>-<slug>.md``.

Port of ``core/Slug.kt``: ASCII-only, deterministic, NFKD-normalize so
diacritics decompose, collapse every run of non-``[a-z0-9]`` into one
hyphen, truncate without leaving a trailing hyphen, fall back to ``note``.
"""

from __future__ import annotations

import unicodedata

DEFAULT_MAX_LENGTH = 80
FALLBACK = "note"


def of(title: str, max_length: int = DEFAULT_MAX_LENGTH) -> str:
    if max_length <= 0:
        raise ValueError(f"maxLength must be positive: {max_length}")
    decomposed = unicodedata.normalize("NFKD", title)
    out: list[str] = []
    for char in decomposed:
        if unicodedata.category(char).startswith("M"):
            continue
        if "a" <= char <= "z" or "0" <= char <= "9":
            out.append(char)
        elif "A" <= char <= "Z":
            out.append(chr(ord(char) + 32))
        else:
            if out and out[-1] != "-":
                out.append("-")
    slug = "".join(out).strip("-")[:max_length].rstrip("-")
    if not slug:
        slug = FALLBACK
    return slug
