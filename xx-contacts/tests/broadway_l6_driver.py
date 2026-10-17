"""Broadway driver for the L6 smoke test.

Builds the REAL contacts window plus the new pick-mode pages (one per
kind), a prefilled editor, and an import page fed with a ``.vcf`` file,
against a seeded temp database; drives the real service ``Pick`` path
through the real GTK presenter; pumps the main loop; prints ``KEY:
value`` markers. Any unexpected exception exits nonzero so the parent
test fails instead of passing on empty output. Not collected by pytest:
executed as a subprocess by ``tests/test_l6_broadway.py``.
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

from gi.repository import Adw, Gtk  # noqa: E402

from xx_testing.broadway_wait import pump_until as _wait_until  # noqa: E402

_ERRORS = []


def _note_error(text: str) -> None:
    _ERRORS.append(text)


sys.excepthook = lambda *args: _note_error("".join(
    traceback.format_exception(*args)))


def _focus_name(window: Gtk.Window) -> str:
    widget = window.get_focus()
    return type(widget).__name__ if widget is not None else "None"


def _seed(base: str):
    from xx_contacts.app_config import AppConfig
    from xx_contacts.store import (
        ContactData,
        ContactStore,
        EmailEntry,
        PhoneEntry,
    )

    db_path = os.path.join(base, "contacts.db")
    config_path = os.path.join(base, "config.json")
    photos_dir = os.path.join(base, "photos")
    store = ContactStore(db_path)
    ada_uid = store.create(ContactData(
        display_name="Ada Lovelace", given="Ada", family="Lovelace",
        phones=[PhoneEntry(number_raw="+1 555 0100",
                           number_e164="+15550100",
                           number_digits="15550100",
                           type="CELL", is_primary=True),
                PhoneEntry(number_raw="+1 555 0101",
                           number_e164="+15550101",
                           number_digits="15550101", type="HOME")],
        emails=[EmailEntry(address="ada@example.org", type="WORK")]))
    store.create(ContactData(
        display_name="Grace Hopper", given="Grace", family="Hopper",
        phones=[PhoneEntry(number_raw="+1 555 0202",
                           number_e164="+15550202",
                           number_digits="15550202", type="WORK")]))
    store.create(ContactData(display_name="Bob"))
    config = AppConfig(show_photos=False, default_region="US")
    config.save(config_path)
    return store, config, config_path, db_path, photos_dir, ada_uid


class _CaptureInvocation:
    def __init__(self) -> None:
        self.reply = None
        self.error = None

    def return_value(self, parameters):
        self.reply = parameters.unpack()

    def return_dbus_error(self, name, message):
        self.error = (name, message)


def _picker_window(title: str):
    for window in Gtk.Window.list_toplevels():
        try:
            kind = window.get_title()
        except Exception:
            continue
        if kind == title:
            return window
    return None


def main(argv: list) -> None:
    from xx_contacts import picker as picker_module
    from xx_contacts.editor_ui import ContactEditorPage
    from xx_contacts.list_ui import ContactsWindow
    from xx_contacts.phones import LookupIndex
    from xx_contacts.picker_ui import TITLES
    from xx_contacts.picker_ui import present_pick_dialog
    from xx_contacts.service import ContactsService

    base = argv[1]
    store, config, config_path, db_path, photos_dir, ada_uid = _seed(base)
    markers: dict[str, str] = {}
    try:
        app = Adw.Application(application_id="io.piercingxx.XXContacts")
        app.register(None)
        window = ContactsWindow(app, store, config, config_path,
                                db_path)
        window.present()
        _wait_until(window.get_mapped)
        markers["LIST_VISIBLE"] = str(window.is_visible())

        for kind in ("contact", "phone", "email"):
            rows = picker_module.build_pick_rows(
                kind, store.all_records())
            markers[f"PICK_{kind.upper()}_ROWS"] = str(len(rows))
            service = ContactsService(store, LookupIndex(), "US")
            invocation = _CaptureInvocation()
            service.request_pick(kind, invocation,
                                 presenter=present_pick_dialog)
            _wait_until(lambda: _picker_window(TITLES[kind]) is not None)
            dialog = _picker_window(TITLES[kind])
            markers[f"PICK_{kind.upper()}_WINDOW"] = str(
                dialog is not None and dialog.is_visible())
            if dialog is None:
                raise RuntimeError(f"picker window missing for {kind}")
            markers[f"PICK_{kind.upper()}_FOCUS"] = _focus_name(dialog)
            nav = dialog.get_child()
            page = nav.get_visible_page()
            page._list.emit("activate", 0)
            _wait_until(lambda: invocation.reply is not None)
            markers[f"PICK_{kind.upper()}_REPLY"] = repr(invocation.reply)
            assert invocation.error is None, (kind, invocation.error)
            assert invocation.reply == (rows[0].uid, rows[0].value), (
                kind, invocation.reply)
            assert not service.pick_pending()

        cancel_service = ContactsService(store, LookupIndex(), "US")
        cancel_invocation = _CaptureInvocation()
        cancel_service.request_pick("contact", cancel_invocation,
                                    presenter=present_pick_dialog)
        _wait_until(lambda: _picker_window(TITLES["contact"]) is not None)
        dialog = _picker_window(TITLES["contact"])
        nav = dialog.get_child()
        page = nav.get_visible_page()
        page._cancel_button.emit("clicked")
        _wait_until(lambda: cancel_invocation.reply is not None)
        markers["PICK_CANCEL_REPLY"] = repr(cancel_invocation.reply)
        assert cancel_invocation.reply == ("", ""), cancel_invocation.reply

        editor = ContactEditorPage(
            window._navigation, window, store, None, db_path,
            photos_dir, config=config, config_path=config_path,
            default_region=config.default_region,
            prefill_phone="+1 555 0100")
        window._navigation.push(editor)
        _wait_until(lambda: window._navigation.get_visible_page() is editor)
        markers["PREFILL_NUMBER"] = editor._phone_rows[0].entry.get_text()
        markers["PREFILL_FOCUS"] = _focus_name(window)
        assert editor._phone_rows[0].entry.get_text() == "+1 555 0100"
        window._navigation.pop()
        _wait_until(lambda: window._navigation.get_visible_page()
                    is not editor)

        vcf_path = os.path.join(base, "incoming.vcf")
        with open(vcf_path, "w", encoding="utf-8") as handle:
            handle.write("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Zed Shaw\r\n"
                         "TEL;TYPE=CELL:+1 555 0303\r\nEND:VCARD\r\n")
        count_before = store.count()
        before = window._navigation.get_visible_page()
        window.open_vcf_file(vcf_path)
        _wait_until(lambda: window._navigation.get_visible_page()
                    is not before)
        page = window._navigation.get_visible_page()
        markers["VCF_PAGE"] = type(page).__name__
        markers["VCF_PENDING"] = str(bool(page._pending_vcf))
        markers["VCF_UNCHANGED"] = str(store.count() == count_before)
        assert store.count() == count_before
        window._navigation.pop()
        _wait_until(lambda: window._navigation.get_visible_page()
                    is not page)

        before = window._navigation.get_visible_page()
        window.open_new_editor(prefill_phone="+1 555 0404")
        _wait_until(lambda: window._navigation.get_visible_page()
                    is not before)
        page = window._navigation.get_visible_page()
        markers["LAUNCH_NEW_PAGE"] = type(page).__name__
        markers["LAUNCH_NEW_NUMBER"] = (
            page._phone_rows[0].entry.get_text())
        assert page._phone_rows[0].entry.get_text() == "+1 555 0404"

        markers["PAGES_OK"] = "True"
        window.destroy()
    except Exception:
        _note_error(traceback.format_exc())
    finally:
        try:
            store.close()
        except Exception:
            _note_error(traceback.format_exc())
    for key in sorted(markers):
        print(f"{key}: {markers[key]}")
    print(f"EXCEPTIONS: {len(_ERRORS)}")
    for text in _ERRORS:
        print(text)
    sys.stdout.flush()
    if _ERRORS or markers.get("PAGES_OK") != "True":
        sys.exit(1)


main(sys.argv)
