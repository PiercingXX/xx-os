"""Expand-in-place tree: flat rows with depth + guide geometry.

Port of the Android ``Tree`` JVM seam. Expanding a folder inserts its
children immediately below it; siblings stay visible. ``guide`` pins
the box-drawing geometry the on-screen tree draws.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .hidden import should_show
from .listing import Entry, sort


@dataclass(frozen=True)
class TreeNode:
    entry: Entry
    depth: int
    is_last: bool
    ancestor_continues: list[bool] = field(default_factory=list)
    expanded: bool = False

    @property
    def is_dir(self) -> bool:
        return self.entry.is_dir


def flatten(
    children: list[Entry],
    expanded: set[str],
    show_hidden: bool,
    read_children: Callable[[Entry], list[Entry] | None] | None = None,
    depth: int = 0,
    ancestor_continues: list[bool] | None = None,
    max_depth: int = 12,
) -> list[TreeNode]:
    if depth > max_depth:
        return []
    shown = sort([e for e in children if should_show(e.name, show_hidden)])
    out: list[TreeNode] = []
    for index, entry in enumerate(shown):
        last = index == len(shown) - 1
        open_ = entry.is_dir and entry.path in expanded
        out.append(TreeNode(entry, depth, last,
                            list(ancestor_continues or []), open_))
        if open_:
            nested = (read_children(entry) if read_children else None) or []
            out.extend(flatten(nested, expanded, show_hidden,
                               read_children, depth + 1,
                               list(ancestor_continues or []) + [not last],
                               max_depth))
    return out


def guide(node: TreeNode) -> str:
    if node.depth <= 0:
        return ""
    prefix = "".join("│  " if c else "   " for c in node.ancestor_continues)
    return prefix + ("└─ " if node.is_last else "├─ ")
