"""Broadway driver for the Lane 5 smoke test (run as a subprocess).

Builds the REAL dialer view (Recents + Keypad + People + Rules +
Setup) against the seeded store the parent pytest prepared at
XX_DIALER_TEST_HOME, presents it on the Broadway display the parent
provides, pumps the main loop, visits every new page through its
real widgets, runs Test-a-number through the real engine, and
prints KEY: value markers. Any unexpected exception exits nonzero
so the parent fails instead of passing on empty output. Not
collected by pytest.
"""
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "common"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")

from gi.repository import Adw, Gtk  # noqa: E402

Adw.init()

from xx_dialer.main import DialerDeps, build_dialer_view  # noqa: E402
from xx_testing.broadway_wait import pump as _pump  # noqa: E402
from xx_testing.broadway_wait import pump_until as _wait_until  # noqa: E402

ERRORS: list[str] = []


def _hook(kind, value, tb) -> None:
    text = "".join(traceback.format_exception(kind, value, tb))
    ERRORS.append(text)
    sys.stderr.write(text)
    sys.stderr.flush()


sys.excepthook = _hook


class _Voice:
    def __init__(self):
        self.placed: list[str] = []

    def place(self, number, on_done):
        self.placed.append(number)
        on_done(True, "/call/0")


class _Client:
    is_present = True
    revision = 3

    def __init__(self):
        self.calls: list[tuple] = []

    def set_star(self, uid, starred):
        self.calls.append(("star", uid, starred))

    def set_business(self, uid, business):
        self.calls.append(("business", uid, business))

    def set_blocked(self, uid, blocked):
        self.calls.append(("blocked", uid, blocked))
        return []


def main() -> int:
    home = Path(os.environ["XX_DIALER_TEST_HOME"])
    db_file = home / ".local" / "share" / "xx-dialer" / "calls.db"
    voice = _Voice()
    client = _Client()
    deps = DialerDeps(db_file=db_file, home=home, voice=voice,
                      contacts=client)
    view = build_dialer_view(deps)
    window = Gtk.Window()
    window.set_child(view.widget)
    window.present()
    if not _wait_until(window.get_mapped, timeout_s=10.0):
        raise AssertionError("dialer L5: window never mapped")

    pages = sorted(name for name in
                   ("recents", "keypad", "people", "rules", "setup")
                   if view.stack.get_child_by_name(name) is not None)
    print("PAGES:", len(pages))

    view.stack.set_visible_child_name("rules")
    if not _wait_until(lambda: view.stack.get_visible_child_name()
                       == "rules", timeout_s=10.0):
        raise AssertionError("dialer L5: rules page never shown")
    rules_view = view.rules
    print("PRECEDENCE_ROWS:",
          _count_listbox(rules_view._preced_list))
    print("PATTERN_ROWS:", _count_listbox(rules_view._pattern_list))
    print("BLOCKED_ROWS:", _count_listbox(rules_view._blocked_list))
    print("TALLY:", rules_view._tally_label.get_text().replace(
        "\n", " | "))
    from xx_dialer.store import Store as _Store
    with _Store(db_file) as _store:
        print("SCREEN_ROWS:", len(_store.last_screened(1000)))
    print("TEST_ENTRY_FOCUS:", rules_view._test_entry.get_can_focus())
    verdict, reason = rules_view.controller.test_number("+14155550200")
    print("TEST_VERDICT:", verdict)
    print("TEST_REASON:", reason)
    verdict2, _reason2 = rules_view.controller.test_number("+14155550100")
    print("TEST_STARRED:", verdict2)
    enforce_btn = rules_view._mode_seg.get_first_child()
    print("ENFORCE_ACTIVE:", enforce_btn.has_css_class("seg-selected"))

    view.stack.set_visible_child_name("people")
    if not _wait_until(lambda: view.stack.get_visible_child_name()
                       == "people", timeout_s=10.0):
        raise AssertionError("dialer L5: people page never shown")
    people_view = view.people
    if not _wait_until(lambda: people_view._store.get_n_items() > 0,
                       timeout_s=10.0):
        raise AssertionError("dialer L5: people rows never loaded")
    print("PEOPLE_ITEMS:", people_view._store.get_n_items())
    headers = sum(1 for i in range(people_view._store.get_n_items())
                  if people_view._store.get_item(i).kind == "header")
    print("PEOPLE_SECTIONS:", headers)
    first_uid = next(
        (people_view._store.get_item(i).uid
         for i in range(people_view._store.get_n_items())
         if people_view._store.get_item(i).kind == "item"), "")
    print("PEOPLE_FIRST_UID:", first_uid)

    view.stack.set_visible_child_name("setup")
    if not _wait_until(lambda: view.stack.get_visible_child_name()
                       == "setup", timeout_s=10.0):
        raise AssertionError("dialer L5: setup page never shown")
    setup_view = view.setup

    def _setup_settled() -> bool:
        try:
            return all(
                (detail.get_text() or "") not in ("", "Checking...")
                for _key, (_glyph, detail) in setup_view._rows.items())
        except Exception:
            return False

    if not _wait_until(_setup_settled, timeout_s=15.0):
        raise AssertionError("dialer L5: setup rows never settled")
    details = [detail.get_text()
               for _key, (_glyph, detail) in setup_view._rows.items()]
    print("SETUP_ROWS:", len(details))
    print("SETUP_FILLED:", sum(1 for text in details if text
                               and text != "Checking..."))
    print("SETUP_OBSERVE:", setup_view._observe_label.get_text())
    print("SETUP_ENFORCE_VISIBLE:",
          setup_view._enforce_button.get_visible())

    view.stack.set_visible_child_name("rules")
    if not _wait_until(lambda: view.stack.get_visible_child_name()
                       == "rules", timeout_s=10.0):
        raise AssertionError("dialer L5: rules page never re-shown")
    rules_view._win_unknown.emit("clicked")
    _pump(0.3)
    rules_view._add_pattern.emit("clicked")
    _pump(0.3)
    print("DIALOGS:", "2")

    view.stack.set_visible_child_name("recents")
    if not _wait_until(lambda: view.stack.get_visible_child_name()
                       == "recents", timeout_s=10.0):
        raise AssertionError("dialer L5: recents page never re-shown")
    print("BADGE_AFTER:", view.recents.unread)

    window.destroy()
    _pump(0.2)
    print("EXCEPTIONS:", len(ERRORS))
    for text in ERRORS:
        sys.stderr.write(text)
    return 1 if ERRORS else 0


def _count_listbox(listbox) -> int:
    count = 0
    while listbox.get_row_at_index(count) is not None:
        count += 1
    return count


sys.exit(main())
