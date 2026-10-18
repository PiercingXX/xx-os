# ruff: noqa: F821 (win/app/Gtk/GLib come from scripts/snap.py)
import datetime

win.jump_to(datetime.date(2026, 8, 15))
win.show_view("schedule")
win.open_editor(uid="standup-1")
