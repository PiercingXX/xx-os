"""GFM task-list block parsing (port of ``core/TaskList.kt``).

``split`` partitions a body into task blocks and raw text; ``render``
joins them back losslessly: ``render(split(body)) == body`` always.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

MAX_START_INDENT = 8

_TASK_ITEM = re.compile(r"^([ \t]*)([-*+])[ \t]+\[([ xX])\](?:[ \t]+(.*))?$")


@dataclass(frozen=True)
class TaskItem:
    raw_line: str
    indent: int
    marker: str
    checked: bool
    text: str


@dataclass(frozen=True)
class TaskBlock:
    items: tuple[TaskItem, ...]


@dataclass(frozen=True)
class BlockSegment:
    block: TaskBlock


@dataclass(frozen=True)
class TextSegment:
    lines: tuple[str, ...]


Segment = BlockSegment | TextSegment


def parse_line(line: str, block_open: bool) -> TaskItem | None:
    match = _TASK_ITEM.match(line)
    if match is None:
        return None
    indent = len(match.group(1))
    if not block_open and indent > MAX_START_INDENT:
        return None
    return TaskItem(
        raw_line=line,
        indent=indent,
        marker=match.group(2),
        checked=match.group(3) != " ",
        text=match.group(4) if match.group(4) is not None else "",
    )


def parse_item(line: str) -> TaskItem | None:
    return parse_line(line, True)


def split(body: str) -> list[Segment]:
    segments: list[Segment] = []
    current: list[TaskItem] = []

    def flush() -> None:
        if current:
            segments.append(BlockSegment(TaskBlock(tuple(current))))
            current.clear()

    for line in body.split("\n"):
        item = parse_line(line, bool(current))
        if item is not None:
            current.append(item)
        else:
            flush()
            segments.append(TextSegment((line,)))
    flush()
    return segments


def render(segments: list[Segment]) -> str:
    lines: list[str] = []
    for segment in segments:
        if isinstance(segment, BlockSegment):
            lines.extend(item.raw_line for item in segment.block.items)
        else:
            lines.extend(segment.lines)
    return "\n".join(lines)
