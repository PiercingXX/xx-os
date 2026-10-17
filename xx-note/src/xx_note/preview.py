"""Card preview text (pure): the Android ``styledPreview`` in Pango markup.

The first non-blank body lines, clipped like the Android card, with the
Markdown markers kept but dimmed (heading hashes, list bullets, task
boxes, emphasis delimiters) so the note reads as text first. Done tasks
dim and strike. Checklist progress counts every task line.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape

PREVIEW_LINES = 6
LINE_CHARS = 160
MARKER_ALPHA = "45%"
DONE_ALPHA = "60%"

_TASK = re.compile(r"^([-*+] \[([ xX])\] )(.*)$")
_LEAD = re.compile(r"^(#{1,6} |[-*+] |\d+[.)] |> )(.*)$")
_INLINE = re.compile(r"(\*\*|__|~~|`|==)")


def _dim(text: str, alpha: str = MARKER_ALPHA) -> str:
    return f'<span alpha="{alpha}">{escape(text)}</span>'


def _inline(text: str) -> str:
    out: list[str] = []
    pos = 0
    for match in _INLINE.finditer(text):
        out.append(escape(text[pos:match.start()]))
        out.append(_dim(match.group(1)))
        pos = match.end()
    out.append(escape(text[pos:]))
    return "".join(out)


def preview_lines(body: str, lines: int = PREVIEW_LINES) -> list[str]:
    """The first ``lines`` non-blank, stripped body lines, each clipped."""
    out: list[str] = []
    for line in body.split("\n"):
        stripped = line.strip()
        rule = len(stripped) >= 3 and set(stripped) <= {"-", "*", "_"}
        if not stripped or stripped.startswith("```") or rule:
            continue
        out.append(stripped[:LINE_CHARS])
        if len(out) >= lines:
            break
    return out


def preview_markup(body: str, lines: int = PREVIEW_LINES, title: str = "") -> str:
    """Markup for the card body. A first line that only repeats the card
    title (an untitled note's title comes from it) is left out."""
    rendered: list[str] = []
    shown = preview_lines(body, lines + 1)
    if shown and title and shown[0].lstrip("#").strip() == title.strip():
        shown = shown[1:]
    for line in shown[:lines]:
        task = _TASK.match(line)
        if task:
            done = task.group(2) in "xX"
            text = escape(task.group(3))
            if done:
                text = f'<span alpha="{DONE_ALPHA}" strikethrough="true">{text}</span>'
            rendered.append(_dim(task.group(1)) + text)
            continue
        lead = _LEAD.match(line)
        if lead:
            rendered.append(_dim(lead.group(1)) + _inline(lead.group(2)))
            continue
        rendered.append(_inline(line))
    return "\n".join(rendered)


def checklist_progress(body: str) -> tuple[int, int]:
    """(done, total) over every task line; (0, 0) when there are none."""
    done = total = 0
    for line in body.split("\n"):
        task = _TASK.match(line.strip())
        if task:
            total += 1
            if task.group(2) in "xX":
                done += 1
    return done, total
