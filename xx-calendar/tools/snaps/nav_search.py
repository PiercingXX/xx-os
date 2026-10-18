# ruff: noqa: F821 (win/app/Gtk/GLib come from scripts/snap.py)
import datetime

win.jump_to(datetime.date(2026, 8, 15))
win.show_view("search")
win._search_entry.set_text("standup")
