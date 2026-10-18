# ruff: noqa: F821 (win/app/Gtk/GLib come from scripts/snap.py)
import datetime

win.jump_to(datetime.date(2026, 8, 15))
win.show_view("schedule")
win.open_editor(day=datetime.date(2026, 8, 15))


def _find(widget, text):
    if isinstance(widget, Gtk.Button) and widget.get_label() == text:
        return widget
    child = widget.get_first_child()
    while child is not None:
        hit = _find(child, text)
        if hit is not None:
            return hit
        child = child.get_next_sibling()
    return None


_find(win._editor_rows, "Save").emit("clicked")
