# ruff: noqa: F821 (win/app/Gtk/GLib come from scripts/snap.py)
import datetime

win._settings["week_numbers"] = True
win._settings["start_day_of_week"] = "SUNDAY"
win.jump_to(datetime.date(2026, 8, 15))
win.show_view("month")
