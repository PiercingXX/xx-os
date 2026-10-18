"""Header title that reads whole at large text and on narrow screens.

``page_header`` ellipsizes its display title ("Schedu…" at text scale 1.5
on 360 px). ``shrink_header_title`` swaps it for one that keeps every
letter and scales down (to ``TITLE_MIN_SCALE`` at most) where the header
row is narrower than it, never widening the window. Same technique as
xx-contacts' list header.
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")

from gi.repository import Graphene, Gsk, Gtk, Pango  # noqa: E402

#: The header title never shrinks below this share of its full size.
TITLE_MIN_SCALE = 0.3


class ShrinkToFit(Gtk.Widget):
    """Holds one label whole: natural size where that fits, scaled down
    where the row is narrower."""

    __gtype_name__ = "XXCalendarShrinkToFit"

    def __init__(self, child: Gtk.Widget) -> None:
        super().__init__()
        self._child = child
        child.set_parent(self)

    def _natural(self) -> tuple[int, int]:
        _lo, width, _b1, _b2 = self._child.measure(
            Gtk.Orientation.HORIZONTAL, -1)
        _lo, height, _b1, _b2 = self._child.measure(
            Gtk.Orientation.VERTICAL, width)
        return width, height

    def do_measure(self, orientation, for_size):
        width, height = self._natural()
        if orientation == Gtk.Orientation.HORIZONTAL:
            return (int(width * TITLE_MIN_SCALE), width, -1, -1)
        return (int(height * TITLE_MIN_SCALE), height, -1, -1)

    def do_size_allocate(self, width, height, baseline):
        nat_w, nat_h = self._natural()
        scale = min(1.0, width / nat_w) if nat_w > 0 else 1.0
        offset = Graphene.Point()
        offset.init(0, max(0.0, (height - nat_h * scale) / 2))
        transform = Gsk.Transform.new().translate(offset).scale(scale, scale)
        self._child.allocate(nat_w, nat_h, -1, transform)

    def do_snapshot(self, snapshot):
        self.snapshot_child(self._child, snapshot)

    def do_dispose(self):
        if self._child is not None:
            self._child.unparent()
            self._child = None


def shrink_header_title(header: Gtk.Widget) -> Gtk.Widget:
    """Swap a ``page_header``'s ellipsizing title for a whole one that
    scales down to fit. Returns ``header``."""
    top = header.get_first_child()
    title = top.get_first_child() if top is not None else None
    if not isinstance(title, Gtk.Label):
        return header
    top.remove(title)
    title.set_ellipsize(Pango.EllipsizeMode.NONE)
    title.set_max_width_chars(-1)
    title.set_hexpand(False)
    holder = ShrinkToFit(title)
    holder.set_hexpand(True)
    holder.set_valign(Gtk.Align.CENTER)
    top.prepend(holder)
    return header
