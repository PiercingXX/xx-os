# ruff: noqa: F821 (win/app/Gtk/GLib come from scripts/snap.py)
import datetime

win.jump_to(datetime.date(2026, 8, 15))
win.show_view("month")
win._on_month_day(datetime.date(2026, 8, 10))
