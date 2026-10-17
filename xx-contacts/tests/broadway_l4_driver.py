"""Broadway driver for the L4 smoke test.

Builds the REAL contacts window plus the new editor (new + edit),
detail, and Me pages against a seeded temp database, pumps the main
loop, and prints ``KEY: value`` markers. Any unexpected exception
exits nonzero so the parent test fails instead of passing on empty
output. Not collected by pytest: executed as a subprocess by
``tests/test_l4_broadway.py``.
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


def _tap_first_entry(page) -> bool:
    entries = list(getattr(page, "_tap_entries", ()))
    if not entries:
        return False
    entry = entries[0]
    model = entry.observe_controllers()
    for index in range(model.get_n_items()):
        controller = model.get_item(index)
        if isinstance(controller, Gtk.GestureClick):
            controller.emit("pressed", 1, 5.0, 5.0)
            return True
    return False


def _seed(base: str):
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    from xx_contacts import photos as photos_module
    from xx_contacts.app_config import AppConfig
    from xx_contacts.store import (
        AddressEntry,
        ContactData,
        EmailEntry,
        ExtraLine,
        PhoneEntry,
    )

    db_path = os.path.join(base, "contacts.db")
    config_path = os.path.join(base, "config.json")
    photos_dir = os.path.join(base, "photos")
    from xx_contacts.store import ContactStore

    store = ContactStore(db_path)
    ada = ContactData(
        display_name="Ada Lovelace", given="Ada", family="Lovelace",
        org="Analytical Engines", title="Mathematician",
        birthday="1815-12-10", nickname="Ada",
        note="First program", starred=True,
        phones=[PhoneEntry(number_raw="+1 555 0100",
                           number_e164="+15550100",
                           number_digits="15550100",
                           type="CELL", is_primary=True)],
        emails=[EmailEntry(address="ada@example.org", type="WORK")],
        addresses=[AddressEntry(street="12 Test Road",
                                locality="Springfield",
                                country="USA")],
        groups=["Friends"],
        extra=[ExtraLine(prop="X-CUSTOM-THING", params="FOO=bar",
                         value="baz value", group="")])
    ada_uid = store.create(ada)
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8,
                                  96, 72)
    pixbuf.fill(0x336699ff)
    _ok, raw = pixbuf.save_to_bufferv("jpeg", ["quality"], ["85"])
    ada_record = store.get(ada_uid)
    ada_record.photo_path = photos_module.save_photo(
        photos_dir, ada_uid, bytes(raw))
    store.update(ada_uid, ada_record)
    grace = ContactData(
        display_name="Grace Hopper", given="Grace", family="Hopper",
        phones=[PhoneEntry(number_raw="+1 555 0202",
                           number_e164="+15550202",
                           number_digits="15550202",
                           type="WORK")])
    grace_uid = store.create(grace)
    store.create(ContactData(
        display_name="+1 555 0199",
        phones=[PhoneEntry(number_raw="+1 555 0199",
                           number_e164="+15550199",
                           number_digits="15550199",
                           type="CELL")]))
    config = AppConfig(show_photos=True, me_uid=ada_uid,
                       default_region="US")
    config.save(config_path)
    return store, config, config_path, db_path, photos_dir, ada_uid, \
        grace_uid


def main(argv: list) -> None:
    from xx_contacts.detail_ui import ContactDetailPage
    from xx_contacts.editor_ui import ContactEditorPage
    from xx_contacts.list_ui import ContactsWindow
    from xx_contacts.mecard_ui import MeCardPage

    base = argv[1]
    (store, config, config_path, db_path, photos_dir, ada_uid,
     grace_uid) = _seed(base)
    markers: dict[str, str] = {}
    try:
        app = Adw.Application(application_id="io.piercingxx.XXContacts")
        app.register(None)
        window = ContactsWindow(app, store, config, config_path,
                                db_path)
        window.present()
        _wait_until(window.get_mapped)
        _wait_until(lambda: window._model.get_n_items() > 0)
        markers["LIST_VISIBLE"] = str(window.is_visible())
        markers["LIST_ROWS"] = str(
            window._model.get_n_items() > 0)

        new_page = ContactEditorPage(
            window._navigation, window, store, None, db_path,
            photos_dir, config=config, config_path=config_path,
            default_region=config.default_region)
        window._navigation.push(new_page)
        _wait_until(lambda: window._navigation.get_visible_page()
                    is new_page)
        markers["EDITOR_NEW_FOCUS"] = _focus_name(window)
        markers["EDITOR_NEW_ROWS"] = str(len(new_page._phone_rows))
        tapped = _tap_first_entry(new_page)
        _wait_until(lambda: _focus_name(window) == "Text")
        markers["EDITOR_TAP_WORKS"] = str(tapped)
        markers["EDITOR_TAP_FOCUS"] = _focus_name(window)
        window._navigation.pop()
        _wait_until(lambda: window._navigation.get_visible_page()
                    is not new_page)

        edit_page = ContactEditorPage(
            window._navigation, window, store, ada_uid, db_path,
            photos_dir, config=config, config_path=config_path,
            default_region=config.default_region)
        window._navigation.push(edit_page)
        _wait_until(lambda: window._navigation.get_visible_page()
                    is edit_page)
        markers["EDIT_LOADED"] = edit_page._entries["given"].get_text()
        markers["EDIT_PHONES"] = str(len(edit_page._phone_rows))
        markers["EDIT_DISPLAY"] = edit_page._birthday_display.get_text()
        window._navigation.pop()
        _wait_until(lambda: window._navigation.get_visible_page()
                    is not edit_page)

        detail_page = ContactDetailPage(
            window._navigation, window, store, ada_uid, db_path,
            config=config, config_path=config_path,
            photos_dir=photos_dir,
            default_region=config.default_region)
        window._navigation.push(detail_page)
        _wait_until(lambda: window._navigation.get_visible_page()
                    is detail_page)
        markers["DETAIL_TITLE"] = detail_page._name.get_text()
        markers["DETAIL_ME"] = detail_page._me_button.get_label()
        window._navigation.pop()
        _wait_until(lambda: window._navigation.get_visible_page()
                    is not detail_page)

        me_page = MeCardPage(
            window._navigation, window, store, config, config_path,
            db_path, photos_dir,
            default_region=config.default_region)
        window._navigation.push(me_page)
        _wait_until(lambda: window._navigation.get_visible_page()
                    is me_page)
        markers["ME_NAME"] = me_page._name.get_text()
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
