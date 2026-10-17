"""Broadway driver for the pick-window smoke test.

Builds the REAL ``ContactsApp`` pick-mode window (the GUI half of the
out-of-service ``Pick``) against a seeded temp database with stubbed
``reply_pick_async``, activates the first row through the real
``ContactPickerPage``, and prints ``KEY: value`` markers. Any
unexpected exception exits nonzero so the parent test fails instead
of passing on empty output. Not collected by pytest: executed as a
subprocess by ``tests/test_pick_window_broadway.py``.
"""

import os
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "common"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw  # noqa: E402

from xx_testing.broadway_wait import pump_until as _wait_until  # noqa: E402

_ERRORS = []


sys.excepthook = lambda *args: _ERRORS.append("".join(
    traceback.format_exception(*args)))


def main(argv: list) -> None:
    from xx_contacts import service_client
    from xx_contacts.main import ContactsApp
    from xx_contacts.store import ContactData, ContactStore, PhoneEntry

    base = argv[1]
    db_path = os.path.join(base, "contacts.db")
    store = ContactStore(db_path)
    ada_uid = store.create(ContactData(
        display_name="Ada Lovelace", given="Ada", family="Lovelace",
        phones=[PhoneEntry(number_raw="+1 555 0100",
                           number_e164="+15550100",
                           number_digits="15550100",
                           type="CELL", is_primary=True)]))
    store.close()
    captured: dict[str, tuple] = {}
    real_reply = service_client.reply_pick_async

    def fake_reply(token, uid, value, on_done, on_error=None):
        captured["args"] = (token, uid, value)
        on_done()

    service_client.reply_pick_async = fake_reply
    markers: dict[str, str] = {}
    try:
        Adw.init()
        app = ContactsApp()
        app.register(None)
        window = app.open_pick_window("phone", "broadway-token",
                                      db_path)
        assert window is not None, "no pick window"
        markers["WINDOW"] = "True"
        assert _wait_until(window.get_mapped, timeout_s=10.0), \
            "pick window never mapped"
        nav = window.get_content()
        page = nav.get_visible_page()
        markers["PAGE"] = type(page).__name__
        assert _wait_until(lambda: page._model.get_n_items() > 0,
                           timeout_s=10.0), "pick rows never loaded"
        markers["ROWS"] = str(page._model.get_n_items())
        item = page._model.get_item(0)
        assert (item.uid, item.value) == (ada_uid, "+1 555 0100"), (
            item.uid, item.value)
        page._list.emit("activate", 0)
        assert _wait_until(lambda: "args" in captured,
                           timeout_s=10.0), "pick reply never sent"
        markers["REPLY"] = repr(captured["args"])
        assert captured["args"] == ("broadway-token", ada_uid,
                                    "+1 555 0100"), captured["args"]
        assert _wait_until(lambda: not window.get_visible(),
                           timeout_s=10.0), "pick window never closed"
        markers["CLOSED"] = "True"
        markers["PAGES_OK"] = "True"
        window.destroy()
    except Exception:
        _ERRORS.append(traceback.format_exc())
    finally:
        service_client.reply_pick_async = real_reply
    for key in sorted(markers):
        print(f"{key}: {markers[key]}")
    print(f"EXCEPTIONS: {len(_ERRORS)}")
    for text in _ERRORS:
        print(text)
    sys.stdout.flush()
    if _ERRORS or markers.get("PAGES_OK") != "True":
        sys.exit(1)


main(sys.argv)
