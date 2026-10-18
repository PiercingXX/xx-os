"""A page title that keeps every letter at large text.

``widgets.page_header`` ellipsizes its display title, so at 320 px and
text-size 1.5 the inbox read "Inb…" beside New and the settings gear.
:func:`shrink_header_title` swaps that label for one that keeps its
natural size where it fits and scales down (to ``TITLE_MIN_SCALE``)
where the header row is narrower. (xx-contacts carries the same helper;
it belongs in ``xx_ui`` once shared code can change.)
"""

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')
gi.require_version('Pango', '1.0')

from gi.repository import Graphene, Gsk, Gtk, Pango  # noqa: E402

#: The header title never shrinks below this share of its full size.
TITLE_MIN_SCALE = 0.5


class ShrinkToFit(Gtk.Widget):
    """Holds one label whole: natural size where that fits, scaled down
    where the row is narrower; it never widens the window."""

    __gtype_name__ = 'TxxtShrinkToFit'

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


def shrink_header_title(header: Gtk.Widget) -> None:
    """Swap a ``page_header``'s ellipsizing title for a whole one that
    scales down to fit (large text, 320 px)."""
    top = header.get_first_child()
    title = top.get_first_child() if top is not None else None
    if not isinstance(title, Gtk.Label):
        return
    top.remove(title)
    title.set_ellipsize(Pango.EllipsizeMode.NONE)
    title.set_max_width_chars(-1)
    title.set_hexpand(False)
    holder = ShrinkToFit(title)
    holder.set_hexpand(True)
    holder.set_valign(Gtk.Align.CENTER)
    top.prepend(holder)
