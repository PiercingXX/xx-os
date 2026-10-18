"""Navigation driver for scripts/snap.py (run with ``win`` in scope).

SNAP_TARGET selects the surface: home, journal, browse, detail, profile,
profile-2..profile-5 (the long Profile page scrolled one screen at a
time), metrics (Activity's metric list), search-miss (a search with no
matches), add-activity, add-weight, weight-error (Log weight after a bad
Save), activity (a journal entry's detail sheet) or menu (SNAP_MENU_PAGE
picks the page). Windows are captured by snap.py
itself; dialogs and popovers are separate surfaces, so those targets
snapshot the fresh widget straight to DIALOG_OUT while snap.py writes a
dummy.
"""

import os

TARGET = os.environ.get("SNAP_TARGET", "home")


def snap_widget(widget, path: str) -> bool:
    import gi

    gi.require_version("Graphene", "1.0")
    from gi.repository import Graphene, Gtk

    width, height = widget.get_width(), widget.get_height()
    if width <= 0 or height <= 0:
        return True  # not allocated yet; retry on the next tick
    snap = Gtk.Snapshot()
    snap.scale(2, 2)
    Gtk.WidgetPaintable.new(widget).snapshot(snap, width, height)
    node = snap.to_node()
    if node is None:
        return True  # a relayout invalidated the frame; try the next tick
    rect = Graphene.Rect().init(0, 0, width * 2, height * 2)
    native = widget.get_native()
    if native is None:
        return True
    texture = native.get_renderer().render_texture(node, rect)
    if texture is not None:
        texture.save_to_png(path)
        print("snap: wrote", path, flush=True)
        return False
    return True


def _walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _walk(child)
        child = child.get_next_sibling()


def _page_scroller(root):
    """The visible page's scroller: the drawable one with the most to
    scroll (inner log and chip scrollers are smaller)."""
    best = None
    for widget in _walk(root):
        if isinstance(widget, Gtk.ScrolledWindow) and widget.is_drawable():  # noqa: F821
            upper = widget.get_vadjustment().get_upper()
            if best is None or upper > best.get_vadjustment().get_upper():
                best = widget
    return best


_ticks = {"left": 20}


def _scroll(screens: int) -> bool:
    """Scroll ``screens`` pages down; re-applied for a few ticks while the
    page finishes laying out."""
    _ticks["left"] -= 1
    scroller = _page_scroller(win)  # noqa: F821
    if scroller is not None:
        adj = scroller.get_vadjustment()
        if adj.get_upper() > adj.get_page_size():
            adj.set_value(min(adj.get_upper() - adj.get_page_size(),
                              screens * adj.get_page_size() * 0.85))
    return _ticks["left"] > 0


def _press(root, text: str) -> None:
    for widget in _walk(root):
        if isinstance(widget, Gtk.Button):  # noqa: F821
            label = widget.get_label()
            child = widget.get_child()
            if label is None and isinstance(child, Gtk.Label):  # noqa: F821
                label = child.get_label()
            if label == text:
                widget.emit("clicked")
                return


def _type_into(root, text: str) -> None:
    for widget in _walk(root):
        if isinstance(widget, Gtk.Entry):  # noqa: F821
            widget.set_text(text)
            return


def _dialog(dialog, after=None) -> None:
    out = os.environ["DIALOG_OUT"]

    def shoot() -> bool:
        if after is not None:
            after(dialog)
        GLib.timeout_add(200, lambda: snap_widget(dialog, out))  # noqa: F821
        return False

    # Poll fast and keep the first allocated frame; snap.py's own capture
    # (at 2x --delay) bounds the run.
    GLib.timeout_add(300, shoot)  # noqa: F821


nav = win._vitals_nav  # noqa: F821

if TARGET in ("home", "journal", "browse", "profile"):
    nav["show"](TARGET)
elif TARGET.startswith("profile-"):
    nav["show"]("profile")
    pages = int(TARGET.split("-")[1]) - 1
    GLib.timeout_add(100, lambda: _scroll(pages))  # noqa: F821
elif TARGET == "detail":
    nav["show"]("browse")
    nav["open_metric"]("steps")
elif TARGET == "weight":
    nav["show"]("browse")
    nav["open_metric"]("weight")
elif TARGET == "metrics":
    nav["show"]("browse")
    nav["open_category"]("activity")
elif TARGET == "search-miss":
    nav["show"]("browse")
    for entry in _walk(win):  # noqa: F821
        if isinstance(entry, Gtk.Entry) and entry.is_drawable():  # noqa: F821
            entry.set_text("glucose")
            break
elif TARGET in ("add-activity", "add-weight"):
    _dialog(nav["add_activity"]() if TARGET == "add-activity"
            else nav["add_weight"]())
elif TARGET == "weight-error":
    _dialog(nav["add_weight"](), after=lambda d: (
        _type_into(d, "5"), _press(d, "Save")))
elif TARGET == "activity":
    nav["show"]("journal")
    _dialog(nav["open_activity"]())
elif TARGET == "menu":
    out = os.environ["DIALOG_OUT"]
    page = os.environ.get("SNAP_MENU_PAGE", "home")
    nav["show"](page)
    popover = nav["open_menu"](page)
    GLib.timeout_add(200, lambda: snap_widget(popover, out))  # noqa: F821
