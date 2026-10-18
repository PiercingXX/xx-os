# ruff: noqa: F821 (win/app/Gtk/GLib come from scripts/snap.py)
import os

_row = next(iter(win._rows.values()))
win._show_row_menu(_row)
win.set_visible(False)
for _top in Gtk.Window.list_toplevels():
    if _top is not win and _top.get_visible():
        try:
            _top.set_default_size(int(os.environ.get("SNAP_W", "360")),
                                  int(os.environ.get("SNAP_H", "720")))
        except Exception:
            pass
