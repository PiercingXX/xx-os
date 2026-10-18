"""Settings: scrolled to the end of the history list."""
from gi.repository import Gtk as _Gtk


def _scroll(widget):
    if isinstance(widget, _Gtk.ScrolledWindow):
        adj = widget.get_vadjustment()
        adj.set_value(adj.get_upper())
        return True
    child = widget.get_first_child()
    while child is not None:
        if _scroll(child):
            return True
        child = child.get_next_sibling()
    return False


_scroll(win)
