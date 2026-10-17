"""Broadway driver for the row-hairline audit (not collected by pytest).

Builds a real thread_list_view, fills it through
common.replace_store (the app's refill path, including incremental
ListView binds), pumps the main loop, then counts rendered xx-row
buttons and how many carry the last class. Only the final row may
carry it: every other row needs the 1 px hairline. Any unexpected
exception exits nonzero so the parent test fails instead of passing
on empty output.
"""

import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "common"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gtk  # noqa: E402

from xx_testing.broadway_wait import pump_until as _wait_until  # noqa: E402

Gtk.init()
opened = Gdk.Display.open(os.environ.get("BROADWAY_DISPLAY", ""))
assert opened is not None, "could not open the Broadway display"
Adw.init()

from xx_email import db as db_mod  # noqa: E402
from xx_email.ui import common as common_mod  # noqa: E402

ERRORS: list[str] = []

NOW_MS = 1_769_000_000_000


def _hook_errors() -> None:
    def _collect(exc_type, exc, tb) -> None:
        ERRORS.append("".join(
            traceback.format_exception(exc_type, exc, tb)))

    sys.excepthook = _collect


def _row_count(window) -> int:
    found = []

    def _walk(widget) -> None:
        try:
            classes = widget.get_css_classes()
        except Exception:  # noqa: BLE001
            classes = ()
        if "xx-row" in classes:
            found.append(widget)
        child = widget.get_first_child() \
            if hasattr(widget, "get_first_child") else None
        while child is not None:
            _walk(child)
            try:
                child = child.get_next_sibling()
            except Exception:  # noqa: BLE001
                break

    _walk(window)
    return len(found)


def _scenario_audit() -> None:
    threads = []
    for index, (tid, name, subject) in enumerate((
            ("t-hair-1", "Mara Voss", "Dinner on the 42?"),
            ("t-hair-2", "Ada Lovelace", "Tacos at noon?"),
            ("t-hair-3", "Mom", "Re: Sunday"))):
        threads.append(db_mod.Thread(
            account_email="user@dev.xxemail", id=tid,
            snippet="see inside", subject=subject,
            from_address=f"{name} <user@example.com>",
            from_name=name, date=NOW_MS - index * 60_000,
            message_count=1, unread_count=1,
            has_attachments=False, starred=False, in_inbox=True,
            categories="CATEGORY_PERSONAL", labels_csv="INBOX"))
    window = Gtk.Window()
    window.set_default_size(360, 720)
    store = common_mod.new_thread_store()
    view = common_mod.thread_list_view(
        store, lambda tid, n: None, lambda tid: None,
        lambda action, tid: None, "ARCHIVE", "DELETE")
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_vexpand(True)
    scrolled.set_child(view)
    window.set_child(scrolled)
    common_mod.replace_store(store, threads)
    window.present()
    if not _wait_until(lambda: _row_count(window) == 3, timeout_s=10.0):
        raise AssertionError(
            "email row hairline: 3 rows never rendered, got "
            f"{_row_count(window)}")

    rows = []

    def _walk(widget) -> None:
        try:
            classes = widget.get_css_classes()
        except Exception:  # noqa: BLE001
            classes = ()
        if "xx-row" in classes:
            rows.append("last" in classes)
        child = widget.get_first_child() \
            if hasattr(widget, "get_first_child") else None
        while child is not None:
            _walk(child)
            try:
                child = child.get_next_sibling()
            except Exception:  # noqa: BLE001
                break

    _walk(window)
    print(f"ROWS: {len(rows)}")
    print(f"LASTCOUNT: {sum(1 for last in rows if last)}")


def main() -> int:
    _hook_errors()
    try:
        _scenario_audit()
    except Exception:
        traceback.print_exc()
        return 1
    if ERRORS:
        for text in ERRORS:
            sys.stderr.write(text + "\n")
        return 1
    print("ERRORS: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
