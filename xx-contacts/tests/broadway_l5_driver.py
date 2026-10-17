"""Broadway driver for the L5 smoke test.

Builds the REAL contacts window plus the new import/export page against
a seeded temp database (real-looking rows, one with a normalized photo
and an unmapped X- property), pumps the main loop, and prints
``KEY: value`` markers. Any unexpected exception exits nonzero so the
parent test fails instead of passing on empty output. Not collected by
pytest: executed as a subprocess by ``tests/test_l5_broadway.py``.
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


def _note_error(text: str) -> None:
    _ERRORS.append(text)


sys.excepthook = lambda *args: _note_error("".join(
    traceback.format_exception(*args)))


def _seed(base: str):
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    from xx_contacts import photos as photos_module
    from xx_contacts.app_config import AppConfig
    from xx_contacts.store import (
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
        org="Analytical Engines", starred=True,
        phones=[PhoneEntry(number_raw="+1 555 0100",
                           number_e164="+15550100",
                           number_digits="15550100",
                           type="CELL", is_primary=True)],
        emails=[EmailEntry(address="ada@example.org", type="WORK")],
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
    store.create(ContactData(
        display_name="Grace Hopper", given="Grace", family="Hopper",
        phones=[PhoneEntry(number_raw="+1 555 0202",
                           number_e164="+15550202",
                           number_digits="15550202",
                           type="WORK")]))
    config = AppConfig(show_photos=True, me_uid=ada_uid,
                       default_region="US")
    config.save(config_path)
    return store, config, config_path, db_path


def main(argv: list) -> None:
    from xx_contacts.import_export_ui import ImportExportPage
    from xx_contacts.list_ui import ContactsWindow

    base = argv[1]
    store, config, config_path, db_path = _seed(base)
    markers: dict[str, str] = {}
    try:
        app = Adw.Application(application_id="io.piercingxx.XXContacts")
        app.register(None)
        window = ContactsWindow(app, store, config, config_path,
                                db_path)
        window.present()
        _wait_until(window.get_mapped)
        markers["LIST_VISIBLE"] = str(window.is_visible())
        window._show_overflow()
        popover = window._overflow
        _wait_until(popover.is_visible)
        labels: list[str] = []
        box = popover.get_child()
        child = box.get_first_child()
        while child is not None:
            text_child = child.get_child()
            get_text = getattr(text_child, "get_text", None)
            if callable(get_text):
                labels.append(get_text())
            child = child.get_next_sibling()
        markers["LIST_HAS_IMPORT_EXPORT"] = str(
            popover.is_visible() and "Import/export" in labels)
        popover.popdown()
        _wait_until(lambda: not popover.is_visible())

        page = ImportExportPage(
            window._navigation, window, store, config, config_path,
            default_region=config.default_region)
        window._navigation.push(page)
        _wait_until(lambda: window._navigation.get_visible_page() is page)
        markers["IMPORT_TITLE"] = page.get_title()
        markers["IMPORT_VISIBLE"] = str(page.is_visible())
        markers["IMPORT_VCF"] = str(
            page._import_vcf_button.is_visible()
            and page._import_vcf_button.is_sensitive())
        markers["IMPORT_BACKUP"] = str(
            page._import_backup_button.is_visible()
            and page._import_backup_button.is_sensitive())
        markers["IMPORT_EDS_SENSITIVE"] = str(
            page._import_eds_button.is_sensitive())
        markers["EXPORT_READY"] = str(
            page._export_button.is_visible()
            and page._export_button.is_sensitive())
        markers["PUBLISH_STATE"] = str(
            page._publish_switch.get_active())
        markers["STATUS_EMPTY"] = str(page._status.get_text() == "")
        window._navigation.pop()
        _wait_until(lambda: window._navigation.get_visible_page() is not page)
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
