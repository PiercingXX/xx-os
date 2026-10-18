"""Broadway smoke child: builds the real window and reports markers.

Runs in its own process (started by ``test_l5_broadway.py``) so the
Broadway display connection can never poison the pytest process: when
the daemon dies, only this child is at risk. Prints ``MARKER=value``
lines; exits nonzero with a traceback on any failure.
"""

import datetime as dt
import os
import sys
import threading
import time

UTC = dt.timezone.utc


def _pump_until(predicate, timeout=8.0):
    from gi.repository import GLib

    deadline = time.time() + timeout
    while time.time() < deadline:
        while GLib.main_context_default().pending():
            GLib.main_context_default().iteration(False)
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from _walk(child)
        child = child.get_next_sibling()


def main(db_path: str) -> int:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import GLib, Gtk, Pango

    from xx_calendar.main import CalendarApp, CalendarWindow, EventItem

    errors: list = []
    old_hook = sys.excepthook
    old_thread_hook = threading.excepthook

    def on_error(*args):
        errors.append(args)

    sys.excepthook = on_error  # type: ignore[assignment]
    threading.excepthook = on_error  # type: ignore[assignment]
    app = CalendarApp(launch={"date": None, "today": False,
                              "new_event": False, "opens": []},
                      db_path=db_path)
    window = None
    try:
        app.register(None)
        window = CalendarWindow(app, db_path)
        window.set_default_size(360, 720)
        window.present()
        if not _pump_until(
                lambda: window._schedule_model.get_n_items() > 0):
            raise AssertionError("schedule never loaded")
        titles = [window._schedule_model.get_item(i).props.title
                  for i in range(window._schedule_model.get_n_items())
                  if isinstance(window._schedule_model.get_item(i),
                                EventItem)]
        print(f"SCHEDULE_TITLES={len(titles):d}", flush=True)
        if not any("ellipsize" in title for title in titles):
            raise AssertionError("long seeded title never rendered")
        if not any("Standup" in title for title in titles):
            raise AssertionError("recurring seeded event never rendered")
        window.show_view("month")
        if not _pump_until(
                lambda: len(getattr(window._month_grid, "weeks", [])) > 0):
            raise AssertionError("month grid never rendered")
        rows = len(window._month_grid.weeks)
        print(f"MONTH_ROWS={rows:d}", flush=True)
        if rows * 7 < 28:
            raise AssertionError("month grid too small")
        window.show_view("day")
        if not _pump_until(lambda: window._view == "day"):
            raise AssertionError("day view never shown")
        window.show_view("calendars")
        if not _pump_until(lambda: window._view == "calendars"):
            raise AssertionError("calendars never shown")
        window.show_view("search")
        if not _pump_until(lambda: window._view == "search"):
            raise AssertionError("search never shown")
        window.show_view("settings")
        if not _pump_until(lambda: window._view == "settings"):
            raise AssertionError("settings never shown")
        window.show_view("schedule")
        if not _pump_until(
                lambda: window._schedule_model.get_n_items() > 0):
            raise AssertionError("schedule never reloaded")
        first_uid = None
        for i in range(window._schedule_model.get_n_items()):
            item = window._schedule_model.get_item(i)
            if isinstance(item, EventItem) and item.props.uid:
                first_uid = (item.props.uid, int(item.props.recurrence_id))
                break
        def _row_buttons(row):
            found = []
            kid = row.get_first_child()
            while kid is not None:
                found.append(kid)
                kid = kid.get_next_sibling()
            return found

        if first_uid is not None:
            window.open_detail(first_uid[0], first_uid[1])
            if not _pump_until(lambda: window._view == "detail"):
                raise AssertionError("detail never opened")
            pairs = []
            kid = window._detail_rows.get_first_child()
            while kid is not None:
                if (isinstance(kid, Gtk.Box)
                        and kid.get_orientation()
                        == Gtk.Orientation.HORIZONTAL
                        and len(_row_buttons(kid)) == 2
                        and all(isinstance(item, Gtk.Button)
                                for item in _row_buttons(kid))):
                    pairs.append(kid)
                kid = kid.get_next_sibling()
            assert pairs, "detail keeps an Edit/Copy action row"
            actions = pairs[0]
            assert actions.get_spacing() == 12
            assert actions.get_halign() != Gtk.Align.CENTER, \
                "Edit/Copy fill the gutters, never center"
            buttons = []
            kid = actions.get_first_child()
            while kid is not None:
                buttons.append(kid)
                kid = kid.get_next_sibling()
            assert len(buttons) == 2
            assert all(button.get_hexpand() for button in buttons), \
                "Edit/Copy are a full-width equal pair"
            window.show_view("schedule")
        probe = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        window._render_rows(probe, [], True, 0)
        assert probe.get_first_child() is None, \
            "an empty list shows nothing, no empty-state line"
        print("EMPTY_NOTHING=ok", flush=True)

        def _labels(widget, out):
            if isinstance(widget, Gtk.Label):
                out.append(widget.get_label() or "")
            if isinstance(widget, Gtk.Image):
                out.append(f"icon:{widget.get_icon_name()}")
            child = widget.get_first_child()
            while child is not None:
                _labels(child, out)
                child = child.get_next_sibling()
            return out

        for view in ("schedule", "month", "day", "settings"):
            window.show_view(view)
            shown = _labels(window._header_slot, [])
            assert "icon:view-more-symbolic" not in shown, \
                f"{view}: no overflow menu (one way to each thing)"
        print("NO_OVERFLOW=ok", flush=True)
        window.show_view("schedule")
        if first_uid is not None:
            window.open_detail(first_uid[0], first_uid[1])
            window.open_editor(uid=first_uid[0])
            assert window._view == "editor"
            captions = _labels(window._editor_rows, [])
            for name in ("STARTS", "ENDS", "REPEAT", "REMINDER"):
                assert name in captions, f"editor names its {name} field"
            assert not any(isinstance(w, Gtk.DropDown) for w in
                           _walk(window._editor_rows)), \
                "editor pickers are theme chips, not unstyled dropdowns"
            assert window.go_back() and window._view == "detail", \
                "back from an edit returns to the event"
            assert window.go_back() and window._view == "schedule", \
                "back from the event returns to the list"
            print("BACK_STACK=ok", flush=True)
        window.open_export_dialog()
        if not _pump_until(lambda: window._view == "export"):
            raise AssertionError("export never opened")

        def _pair_rows(rows_box):
            found = []
            kid = rows_box.get_first_child()
            while kid is not None:
                if isinstance(kid, Gtk.Box) and kid.get_orientation() == \
                        Gtk.Orientation.HORIZONTAL:
                    items = _row_buttons(kid)
                    if len(items) == 2 and all(
                            isinstance(item, Gtk.Button) for item in items):
                        found.append((kid, items))
                kid = kid.get_next_sibling()
            return found

        export_pairs = _pair_rows(window._export_rows)
        assert export_pairs, "export keeps a Cancel/Export action row"
        export_row, export_items = export_pairs[-1]
        assert export_row.get_spacing() == 12
        assert export_row.get_halign() != Gtk.Align.CENTER
        assert all(item.get_hexpand() for item in export_items), \
            "Cancel/Export are a full-width equal pair"
        print("EXPORT_PAIR=ok", flush=True)
        window.show_view("schedule")
        window.open_create_sheet(dt.date.today())
        if not _pump_until(lambda: window._view == "editor"):
            raise AssertionError("create sheet never opened")
        print("SHEET=ok", flush=True)
        window.show_view("schedule")
        window.set_default_size(320, 640)
        if not _pump_until(lambda: True):
            raise AssertionError("unreachable")
        ellipsized = []

        def check_labels(widget) -> None:
            if isinstance(widget, Gtk.Label):
                if widget.get_ellipsize() != Pango.EllipsizeMode.NONE:
                    ellipsized.append(widget)
            child = widget.get_first_child()
            while child is not None:
                check_labels(child)
                child = child.get_next_sibling()

        check_labels(window)
        print(f"ELLIPSIZE={len(ellipsized):d}", flush=True)
        if not ellipsized:
            raise AssertionError("no ellipsizing label in the window")
    finally:
        sys.excepthook = old_hook
        threading.excepthook = old_thread_hook
        if window is not None:
            try:
                window.close()
            except Exception:
                pass
            try:
                window._store.close()
            except Exception:
                pass
            try:
                window._service.stop()
            except Exception:
                pass
        while GLib.main_context_default().pending():
            GLib.main_context_default().iteration(False)
    print(f"ERRORS={len(errors):d}", flush=True)
    if errors:
        raise AssertionError(f"{len(errors):d} main-loop errors: "
                             f"{errors[0][1]!r}")
    print("SMOKE=ok", flush=True)
    return 0


if __name__ == "__main__":
    os.environ.setdefault("GDK_BACKEND", "broadway")
    raise SystemExit(main(sys.argv[1]))
