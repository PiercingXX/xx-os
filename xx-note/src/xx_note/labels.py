"""Label helpers (port of ``ui/labels/LabelOps.kt`` + aggregation).

Labels are a flat tag set stored in frontmatter ``labels: [a, b]`` and
read by Obsidian as tags. Normalization: trim, collapse inner whitespace,
drop empties, de-duplicate preserving first-seen order (case-sensitive —
``Home`` and ``home`` are distinct tags, as in Obsidian).
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")
MAX_LABEL_LEN = 64


def normalize(name: str) -> str:
    return _WS.sub(" ", name.strip()).strip()


def clean_list(names: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in names:
        item = normalize(raw)[:MAX_LABEL_LEN]
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def add(labels: list[str], name: str) -> list[str]:
    item = normalize(name)[:MAX_LABEL_LEN]
    if not item or item in labels:
        return list(labels)
    return [*labels, item]


def remove(labels: list[str], name: str) -> list[str]:
    return [item for item in labels if item != name]


def rename(labels: list[str], old: str, new: str) -> list[str]:
    item = normalize(new)[:MAX_LABEL_LEN]
    if not item:
        return list(labels)
    return [item if entry == old else entry for entry in labels]


def aggregate(notes_labels: list[list[str]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for labels in notes_labels:
        for item in dict.fromkeys(labels):
            counts[item] = counts.get(item, 0) + 1
    return counts
