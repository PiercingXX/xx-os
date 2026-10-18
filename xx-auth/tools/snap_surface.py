"""Helpers for snap_all.sh navigation snippets (run inside snap.py with
``win``, ``app``, ``Gtk`` and ``GLib`` in scope; never imported).

``snap_widget(widget, path)`` paints a separate surface such as the
overflow popover, which snap.py's window capture does not include.
``scroll_to_bottom(page_name)`` scrolls a stack page to its end, so the
capture shows the lower half of a long page."""


def snap_widget(widget, path):  # type: ignore[no-untyped-def]
    import gi

    gi.require_version("Graphene", "1.0")
    from gi.repository import Graphene
    from gi.repository import Gtk as _Gtk

    width, height = widget.get_width(), widget.get_height()
    native = widget.get_native()
    if width <= 0 or height <= 0 or native is None:
        return True  # not allocated yet; retry on the next tick
    snap = _Gtk.Snapshot()
    snap.scale(2, 2)
    _Gtk.WidgetPaintable.new(widget).snapshot(snap, width, height)
    node = snap.to_node()
    if node is None:
        return True
    rect = Graphene.Rect().init(0, 0, width * 2, height * 2)
    texture = native.get_renderer().render_texture(node, rect)
    if texture is None:
        return True
    texture.save_to_png(path)
    print("snap: wrote", path, flush=True)
    return False


def _find_scroller(widget):  # type: ignore[no-untyped-def]
    from gi.repository import Gtk as _Gtk

    if isinstance(widget, _Gtk.ScrolledWindow):
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = _find_scroller(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def scroll_to_bottom(page_name):  # type: ignore[no-untyped-def]
    from gi.repository import GLib as _GLib

    window = app._auth_window  # noqa: F821
    page = window.stack.get_child_by_name(page_name)
    scroller = _find_scroller(page) if page is not None else None
    if scroller is None:
        return

    turns = {"left": 8}

    def settle():  # type: ignore[no-untyped-def]
        # The page may not be laid out yet (upper 0): keep pinning the
        # end for a few turns until the capture.
        adj = scroller.get_vadjustment()
        adj.set_value(max(0.0, adj.get_upper() - adj.get_page_size()))
        turns["left"] -= 1
        return turns["left"] > 0

    _GLib.timeout_add(200, settle)
