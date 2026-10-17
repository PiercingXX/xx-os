"""Broadway driver for the row-alignment regression test.

Builds the REAL contacts window against a seeded temp database with two
rows of very different text widths, pumps the main loop, and prints
``KEY: value`` markers with the window-relative x of every visible row
avatar and title. Any unexpected exception exits nonzero.
Not collected by pytest: executed as a subprocess by
``tests/test_row_align_broadway.py``.
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
    from xx_contacts.app_config import AppConfig
    from xx_contacts.store import ContactData, ContactStore, PhoneEntry

    db_path = os.path.join(base, "contacts.db")
    config_path = os.path.join(base, "config.json")
    store = ContactStore(db_path)
    store.create(ContactData(
        display_name="A",
        phones=[PhoneEntry(number_raw="+1 555 0100",
                           number_e164="+15550100",
                           number_digits="15550100")]))
    store.create(ContactData(
        display_name="Analytical Engines Consolidated",
        phones=[PhoneEntry(number_raw="+1 555 0198",
                           number_e164="+15550198",
                           number_digits="15550198")]))
    store.create(ContactData(
        display_name="Linus Torvalds",
        phones=[PhoneEntry(number_raw="+1 555 0191",
                           number_e164="+15550191",
                           number_digits="15550191")]))
    config = AppConfig()
    config.save(config_path)
    return store, config, config_path, db_path


def _allocated_rows(view) -> int:
    """Rows actually laid out on the display (mapped, visible)."""
    count = 0
    child = view.get_first_child()
    while child is not None:
        try:
            page = child.get_first_child()
            header = page.get_first_child() if page else None
            row_box = (header.get_next_sibling()
                       if header is not None else None)
            if (row_box is not None and row_box.get_visible()
                    and row_box.get_mapped()):
                count += 1
        except Exception:
            pass
        try:
            child = child.get_next_sibling()
        except Exception:
            break
    return count


def _abs_x(widget, window) -> int:
    x = 0
    cur = widget
    while cur is not None and cur is not window:
        try:
            x += cur.get_allocation().x
        except Exception:
            pass
        try:
            cur = cur.get_parent()
        except Exception:
            break
    return x


def main(argv: list) -> None:
    from xx_contacts.list_ui import ContactsWindow

    base = argv[1]
    store, config, config_path, db_path = _seed(base)
    markers: dict[str, str] = {}
    try:
        app = Adw.Application(
            application_id="io.piercingxx.XXContactsRowAlign")
        app.register(None)
        window = ContactsWindow(app, store, config, config_path,
                                db_path)
        window.present()
        _wait_until(window.get_mapped)
        view = window._list
        _wait_until(lambda: _allocated_rows(view) >= 2)
        avatar_xs: list[int] = []
        title_xs: list[int] = []
        child = view.get_first_child()
        while child is not None:
            try:
                page = child.get_first_child()
                header = page.get_first_child() if page else None
                row_box = (header.get_next_sibling()
                           if header is not None else None)
                if row_box is not None and row_box.get_visible():
                    avatar_box = row_box.get_first_child()
                    text = (avatar_box.get_next_sibling()
                            if avatar_box is not None else None)
                    title = (text.get_first_child()
                             if text is not None else None)
                    if avatar_box is not None and title is not None:
                        avatar_xs.append(_abs_x(avatar_box, window))
                        title_xs.append(_abs_x(title, window))
            except Exception:
                _note_error(traceback.format_exc())
            try:
                child = child.get_next_sibling()
            except Exception:
                break
        markers["AVATAR_XS"] = ",".join(str(v) for v in avatar_xs)
        markers["TITLE_XS"] = ",".join(str(v) for v in title_xs)
        markers["ROWS"] = str(len(avatar_xs))
        markers["ALIGN_OK"] = str(
            len(avatar_xs) >= 2
            and len(set(avatar_xs)) == 1
            and len(set(title_xs)) == 1)
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
    if _ERRORS or markers.get("ALIGN_OK") != "True":
        sys.exit(1)


main(sys.argv)
