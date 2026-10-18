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
        _top.set_default_size(int(os.environ.get("SNAP_W", "360")),
                                  int(os.environ.get("SNAP_H", "720")))
