# ruff: noqa: F821 (win/app/Gtk/GLib come from scripts/snap.py)
import os

from xx_contacts import picker as picker_module
from xx_contacts.picker_ui import present_pick_dialog

_kind = os.environ.get("PICK_KIND", "contact")
_rows = picker_module.build_pick_rows(_kind, win._store.all_records())
present_pick_dialog(_kind, _rows, lambda uid, value: None)
win.set_visible(False)
for _top in Gtk.Window.list_toplevels():
    if _top is not win and _top.get_visible():
        # Pin the phone size like snap.py does for the main window:
        # a default size alone is clamped to Broadway's 768 px
        # screen at taller sizes (450x1000, 360x800).
        _size = (int(os.environ.get("SNAP_W", "360")),
                 int(os.environ.get("SNAP_H", "720")))
        _top.set_default_size(*_size)
        _top.set_size_request(*_size)
