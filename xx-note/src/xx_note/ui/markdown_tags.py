"""Live Markdown styling for the editor TextView.

Applies ``Gtk.TextTag``s per line: headings (scale), bold/italic/strike
(weight/style/strikethrough), inline code (monospace), links (underline),
task bullets. Checkbox toggling lives in ``ui/editor.py`` (tap handling),
not here. Pure-text helpers are headless-safe; tag application needs GTK.
"""

from __future__ import annotations

import re

_HEADING = re.compile(r"^(#{1,3})\s+(.*)$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^\s*][^*]*?)\*(?!\*)")
_STRIKE = re.compile(r"~~(.+?)~~")
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_TASK = re.compile(r"^([ \t]*)([-*+]) \[([ xX])\] ")


def ensure_tags(buffer) -> dict[str, object]:  # type: ignore[no-untyped-def]
    """Create (or fetch) the styling tags on a ``Gtk.TextBuffer``."""
    table = buffer.get_tag_table()
    tags: dict[str, object] = {}
    specs = {
        "md-h1": {"scale": 1.4, "weight": 400},
        "md-h2": {"scale": 1.25, "weight": 400},
        "md-h3": {"scale": 1.1, "weight": 400},
        "md-bold": {"weight": 700},
        "md-italic": {"style": 2},
        "md-strike": {"strikethrough": True},
        "md-code": {"family": "Monospace"},
        "md-link": {"underline": 1},
    }
    for name in specs:
        tag = table.lookup(name)
        if tag is None:
            import gi

            gi.require_version("Gtk", "4.0")
            from gi.repository import Gtk

            tag = Gtk.TextTag.new(name)
            for prop, value in specs[name].items():
                try:
                    tag.set_property(prop, value)
                except TypeError:
                    continue
            table.add(tag)
        tags[name] = tag
    return tags


def style_buffer(buffer, tags: dict[str, object]) -> None:  # type: ignore[no-untyped-def]
    """Re-apply Markdown tags over the whole buffer (notes are small)."""
    start, end = buffer.get_bounds()
    for tag in tags.values():
        buffer.remove_tag(tag, start, end)
    text = buffer.get_text(start, end, True)
    lines = text.split("\n")
    offset = 0
    for line in lines:
        _style_line(buffer, tags, offset, line)
        offset += len(line) + 1


def _tag_range(buffer, tags, name: str, start_off: int, end_off: int) -> None:  # type: ignore[no-untyped-def]
    start = buffer.get_iter_at_offset(start_off)
    end = buffer.get_iter_at_offset(end_off)
    buffer.apply_tag(tags[name], start, end)


def _style_line(buffer, tags: dict[str, object], base: int, line: str) -> None:  # type: ignore[no-untyped-def]
    heading = _HEADING.match(line)
    if heading:
        level = min(len(heading.group(1)), 3)
        _tag_range(buffer, tags, f"md-h{level}", base, base + len(line))
        return
    for pattern, tag in (
        (_BOLD, "md-bold"),
        (_STRIKE, "md-strike"),
        (_CODE, "md-code"),
        (_LINK, "md-link"),
    ):
        for match in pattern.finditer(line):
            _tag_range(
                buffer, tags, tag,
                base + match.start(1), base + match.end(1),
            )
    for match in _ITALIC.finditer(line):
        _tag_range(
            buffer, tags, "md-italic",
            base + match.start(1), base + match.end(1),
        )
