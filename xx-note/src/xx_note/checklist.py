"""Checklist sort + item-wise merge (port of ChecklistSorter/ChecklistMerge).

Sorting is a stable partition per task block on editor save only — never
on pull, never during sync (D19). Merging matches items across base/local/
remote by normalized text in three steps (exact groups pair k-th
occurrences positionally; only leftovers go through the fuzzy similarity
pass; contested pairings fork): checkbox ticks can never conflict; a
same-item text edit on both sides to different texts forks the note.
"""

from __future__ import annotations

import re

from xx_note import frontmatter as _fm
from xx_note import tasklist as _tl

_WS_RUN = re.compile(r"\s+")

SIMILARITY_THRESHOLD = 0.6
AMBIGUITY_EPSILON = 0.05
_PREFIX_BONUS_WEIGHT = 0.1
_PREFIX_BONUS_MAX_CHARS = 5


def _normalize(text: str) -> str:
    return _WS_RUN.sub(" ", text).strip()


def sort_body(body: str) -> str:
    return _tl.render(
        [
            _tl.BlockSegment(
                _tl.TaskBlock(
                    tuple(
                        [i for i in seg.block.items if not i.checked]
                        + [i for i in seg.block.items if i.checked]
                    )
                )
            )
            if isinstance(seg, _tl.BlockSegment)
            else seg
            for seg in _tl.split(body)
        ]
    )


def sort_checked_to_bottom(whole_file_text: str) -> str:
    doc = _fm.parse(whole_file_text)
    if not doc.has_frontmatter or doc.is_malformed or doc.note_type != "checklist":
        return whole_file_text
    body = doc.body_text
    head = whole_file_text[: len(whole_file_text) - len(body)]
    return head + sort_body(body)


def similarity(left: str, right: str) -> float:
    if not left or not right:
        return 1.0 if not left and not right else 0.0
    prefix = 0
    while (
        prefix < _PREFIX_BONUS_MAX_CHARS
        and prefix < len(left)
        and prefix < len(right)
        and left[prefix] == right[prefix]
    ):
        prefix += 1
    base = 1.0 - _levenshtein(left, right) / max(len(left), len(right))
    return min(1.0, max(0.0, base + prefix * _PREFIX_BONUS_WEIGHT))


def _levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    prev = list(range(len(right) + 1))
    for i, char_l in enumerate(left, 1):
        cur = [i]
        for j, char_r in enumerate(right, 1):
            cost = 0 if char_l == char_r else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[len(right)]


def _with_checked(item: _tl.TaskItem, checked: bool) -> _tl.TaskItem:
    if item.checked == checked:
        return item
    bracket = item.raw_line.find("[", len(item.raw_line) - len(item.raw_line) + item.indent + len(item.marker))
    if bracket < 0 or bracket + 1 >= len(item.raw_line):
        return item
    rewritten = (
        item.raw_line[: bracket + 1]
        + ("x" if checked else " ")
        + item.raw_line[bracket + 2 :]
    )
    return _tl.TaskItem(rewritten, item.indent, item.marker, checked, item.text)


def _same_segment(left: _tl.Segment | None, right: _tl.Segment | None) -> bool:
    if left is None or right is None:
        return False
    if isinstance(left, _tl.TextSegment) and isinstance(right, _tl.TextSegment):
        return list(left.lines) == list(right.lines)
    if isinstance(left, _tl.BlockSegment) and isinstance(right, _tl.BlockSegment):
        l_items, r_items = left.block.items, right.block.items
        return len(l_items) == len(r_items) and all(
            x.indent == y.indent
            and x.marker == y.marker
            and x.checked == y.checked
            and _normalize(x.text) == _normalize(y.text)
            for x, y in zip(l_items, r_items)
        )
    return False


def _position_segment(
    segments: list[_tl.Segment], index: int
) -> _tl.Segment | None:
    if index >= len(segments):
        return None
    segment = segments[index]
    if (
        index == len(segments) - 1
        and isinstance(segment, _tl.TextSegment)
        and all(not line.strip() for line in segment.lines)
    ):
        return None
    return segment


def merge_bodies(base: str, local: str, remote: str) -> str | None:
    """Merge three checklist bodies. Returns merged text, or None to fork."""
    base_segs = _tl.split(base)
    local_segs = _tl.split(local)
    remote_segs = _tl.split(remote)
    merged: list[_tl.Segment] = []
    for i in range(max(len(base_segs), len(local_segs), len(remote_segs))):
        piece = _resolve_position(
            _position_segment(base_segs, i),
            _position_segment(local_segs, i),
            _position_segment(remote_segs, i),
        )
        if piece is None:
            return None
        if piece == "drop":
            continue
        merged.append(piece)
    if merged and (base.endswith("\n") or local.endswith("\n") or remote.endswith("\n")):
        merged.append(_tl.TextSegment(("",)))
    return _tl.render(merged)


def _resolve_position(
    b: _tl.Segment | None, loc: _tl.Segment | None, rem: _tl.Segment | None
) -> _tl.Segment | str | None:
    """Keep-segment, ``"drop"``, or None to fork."""
    if loc is None and rem is None:
        return "drop"
    if loc is None:
        assert rem is not None
        return _one_side_absent(b, rem)
    if rem is None:
        return _one_side_absent(b, loc)
    if _same_segment(loc, rem):
        return loc
    if b is not None and _same_segment(loc, b):
        return rem
    if b is not None and _same_segment(rem, b):
        return loc
    blockish = (
        (b is None or isinstance(b, _tl.BlockSegment))
        and isinstance(loc, _tl.BlockSegment)
        and isinstance(rem, _tl.BlockSegment)
    )
    if blockish:
        items = _merge_blocks(
            b.block.items if isinstance(b, _tl.BlockSegment) else None,
            list(loc.block.items),
            list(rem.block.items),
        )
        if items is None:
            return None
        if not items:
            return "drop"
        return _tl.BlockSegment(_tl.TaskBlock(tuple(items)))
    return None


def _one_side_absent(
    b: _tl.Segment | None, present: _tl.Segment
) -> _tl.Segment | str | None:
    if b is not None and _same_segment(present, b):
        return "drop"
    if isinstance(present, _tl.BlockSegment):
        items = _merge_blocks(
            b.block.items if isinstance(b, _tl.BlockSegment) else None,
            list(present.block.items),
            [],
        )
        if items is None:
            return None
        if not items:
            return "drop"
        return _tl.BlockSegment(_tl.TaskBlock(tuple(items)))
    return present


def _merge_blocks(
    base: tuple[_tl.TaskItem, ...] | list[_tl.TaskItem] | None,
    local: list[_tl.TaskItem],
    remote: list[_tl.TaskItem],
) -> list[_tl.TaskItem] | None:
    base_items = list(base) if base else []
    local_match = _match_side(local, base_items)
    if local_match is None:
        return None
    remote_match = _match_side(remote, base_items)
    if remote_match is None:
        return None
    local_by_base, _ = local_match
    remote_by_base, _ = remote_match
    groups = [
        (item, local_by_base.get(index), remote_by_base.get(index))
        for index, item in enumerate(base_items)
    ]
    additions: list[tuple] = []
    free_remote = list(remote_match[1])
    for local_idx in local_match[1]:
        norm = _normalize(local[local_idx].text)
        hit = next(
            (
                pos
                for pos, remote_idx in enumerate(free_remote)
                if _normalize(remote[remote_idx].text) == norm
            ),
            -1,
        )
        if hit >= 0:
            additions.append((None, local[local_idx], remote[free_remote.pop(hit)]))
        else:
            additions.append((None, local[local_idx], None))
    for remote_idx in free_remote:
        additions.append((None, None, remote[remote_idx]))
    resolved: list[_tl.TaskItem] = []
    for group in groups + additions:
        item = _resolve_group(group)
        if item is None:
            return None
        if item != "drop":
            resolved.append(item)
    return resolved


def _resolve_group(
    group: tuple[_tl.TaskItem | None, _tl.TaskItem | None, _tl.TaskItem | None],
) -> _tl.TaskItem | str | None:
    base, local, remote = group
    if base is None:
        source = local if local is not None else remote
        if source is None:
            return "drop"
        checked = (local.checked if local else False) or (remote.checked if remote else False)
        return _with_checked(source, checked)
    if local is None and remote is None:
        return "drop"
    if (local is None) != (remote is None):
        survivor = local if local is not None else remote
        assert survivor is not None
        touched = (
            _normalize(survivor.text) != _normalize(base.text)
            or survivor.checked != base.checked
        )
        return survivor if touched else "drop"
    assert local is not None and remote is not None
    checked = local.checked if local.checked != base.checked else remote.checked
    base_n = _normalize(base.text)
    local_n = _normalize(local.text)
    remote_n = _normalize(remote.text)
    if local_n == base_n and remote_n == base_n:
        winner = base
    elif local_n == base_n:
        winner = remote
    elif remote_n == base_n:
        winner = local
    elif local_n == remote_n:
        winner = local
    else:
        return None
    return _with_checked(winner, checked)


def _match_side(
    side: list[_tl.TaskItem], base: list[_tl.TaskItem]
) -> tuple[dict[int, _tl.TaskItem], list[int]] | None:
    base_groups: dict[str, list[int]] = {}
    for index, item in enumerate(base):
        base_groups.setdefault(_normalize(item.text), []).append(index)
    by_base: dict[int, _tl.TaskItem] = {}
    matched_side: set[int] = set()
    used_base: set[int] = set()
    side_groups: dict[str, list[int]] = {}
    for index, item in enumerate(side):
        side_groups.setdefault(_normalize(item.text), []).append(index)
    for text, group in side_groups.items():
        base_group = base_groups.get(text)
        if base_group is None:
            continue
        for rank in range(min(len(group), len(base_group))):
            matched_side.add(group[rank])
            used_base.add(base_group[rank])
            by_base[base_group[rank]] = side[group[rank]]
    candidates: list[tuple[float, int, int]] = []
    for side_idx, item in enumerate(side):
        if side_idx in matched_side:
            continue
        norm = _normalize(item.text)
        for base_idx, base_item in enumerate(base):
            if base_idx in used_base:
                continue
            score = similarity(norm, _normalize(base_item.text))
            if score >= SIMILARITY_THRESHOLD:
                candidates.append((score, side_idx, base_idx))
    candidates.sort(key=lambda c: (-c[0], c[1], c[2]))
    for score, side_idx, base_idx in candidates:
        if side_idx in matched_side or base_idx in used_base:
            continue
        contested = any(
            other != (score, side_idx, base_idx)
            and (other[1] not in matched_side and other[2] not in used_base)
            and (other[1] == side_idx or other[2] == base_idx)
            and other[0] >= score - AMBIGUITY_EPSILON
            for other in candidates
        )
        if contested:
            return None
        matched_side.add(side_idx)
        used_base.add(base_idx)
        by_base[base_idx] = side[side_idx]
    unmatched = [i for i in range(len(side)) if i not in matched_side]
    return by_base, unmatched
