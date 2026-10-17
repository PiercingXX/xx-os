"""Fresh-interpreter driver for the Broadway smoke test.

Run in a subprocess with GDK_BACKEND=broadway, BROADWAY_DISPLAY and
XDG_RUNTIME_DIR pointing at a live gtk4-broadwayd (see
test_broadway_smoke.py), and XX_RADIO_ENGINE=fake. Builds the REAL
production window, visits every tab, pumps the main loop so the engine
status lands from its worker thread, then prints LABEL:/FOCUS:/SIZE:/
CRITICAL: lines and exits 0 only when every expectation holds.
"""

from __future__ import annotations

import sys
import threading


def main(argv: list[str]) -> int:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk

    from xx_radio.main import SPEC
    from xx_radio.ui.window import build_window

    if Gtk.init_check() is not True or Gdk.Display.get_default() is None:
        print("FAIL: no Broadway display")
        return 1

    errors: list = []
    criticals: list = []
    old_hook, old_thread_hook = sys.excepthook, threading.excepthook
    sys.excepthook = lambda exc_type, value, _tb: errors.append(repr(value))
    threading.excepthook = lambda args: errors.append(repr(args.exc_value))

    def log_handler(_domain, level, message, _data) -> None:
        if level & (GLib.LogLevelFlags.LEVEL_CRITICAL
                    | GLib.LogLevelFlags.LEVEL_ERROR):
            criticals.append(str(message))

    domains = ("Gtk", "Gdk", "Adwaita", "GLib", "Gio")
    levels = (GLib.LogLevelFlags.LEVEL_CRITICAL
              | GLib.LogLevelFlags.LEVEL_ERROR)
    handler_ids = [GLib.log_set_handler(domain, levels, log_handler, None)
                   for domain in domains]
    failed = False
    try:
        app = Adw.Application(application_id=SPEC.app_id,
                              flags=Gio.ApplicationFlags.NON_UNIQUE)
        if app.register(None) is not True:
            print("FAIL: app registration failed")
            return 1
        window = build_window(app, SPEC)
        window.present()
        shell = window.xx_shell

        def pump(ms: int) -> None:
            loop = GLib.MainLoop()
            GLib.timeout_add(ms, lambda: (loop.quit(), False)[1])
            loop.run()

        pump(500)
        focus = window.get_focus()
        shell.open_now_playing()
        pump(200)
        shell.close_now_playing()
        focus_name = type(focus).__name__ if focus is not None else "None"
        print(f"FOCUS:{focus_name}")
        for key in SPEC.tab_keys():
            shell.show(key)
            pump(300)
        for _ in range(40):
            if "Checking…" not in _label_texts(window):
                break
            pump(250)

        for spoken in _buttons_in_buttons(window):
            print(f"FAIL: a button inside a button never gets a tap: "
                  f"{spoken!r}")
            failed = True
        texts = _label_texts(window)
        for text in texts:
            print(f"LABEL:{text}")
        size = window.get_default_size()
        print(f"SIZE:{size.width}x{size.height}")
        print(f"ENGINE:{shell.engine.key}")
        if "Entry" in focus_name:
            print("FAIL: a text entry holds focus at startup")
            failed = True
        expected = ["QUICK PICKS", "Playback engine",
                    "Find songs, albums, artists and playlists.",
                    "Open-source licenses"]
        for line in expected:
            if line not in texts:
                print(f"FAIL: missing line {line!r}")
                failed = True
        if "Checking…" in texts:
            print("FAIL: engine status never arrived")
            failed = True
        licenses = _row_titled(window, "Open-source licenses")
        if licenses is None or _images_in(licenses):
            print("FAIL: the About licenses row shows an arrow")
            failed = True
        # A sheet on a phone-width window docks as a full-width bottom
        # sheet (Adw's AUTO floats a 360 px card on the 450 px FLX1).
        from xx_music.ui import common

        dialog, _box = common.sheet("Probe")
        common.present(dialog, window)
        pump(100)
        width = window.get_width() or size.width
        mode = dialog.get_presentation_mode()
        print(f"SHEET:{mode.value_nick}:{dialog.get_content_width()}")
        if mode != Adw.DialogPresentationMode.BOTTOM_SHEET:
            print("FAIL: a phone sheet floats instead of docking")
            failed = True
        if dialog.get_content_width() != width:
            print("FAIL: the bottom sheet is narrower than the phone")
            failed = True
        dialog.close()
        window.destroy()
    finally:
        for domain, handler_id in zip(domains, handler_ids):
            GLib.log_remove_handler(domain, handler_id)
        sys.excepthook = old_hook
        threading.excepthook = old_thread_hook

    for message in criticals:
        print(f"CRITICAL:{message}")
    for message in errors:
        print(f"ERROR:{message}")
    return 1 if failed or criticals or errors else 0


def _label_texts(widget) -> list[str]:
    from gi.repository import Gtk

    texts: list[str] = []
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            texts.append(child.get_text())
        texts.extend(_label_texts(child))
        child = child.get_next_sibling()
    return texts


def _buttons_in_buttons(widget, inside: bool = False) -> list[str]:
    """Buttons nested in a button. GTK 4 buttons click in the capture
    phase, so the outer one takes every tap that ends on the inner one
    (a row's More button played the song instead of opening its menu)."""
    from gi.repository import Gtk

    found: list[str] = []
    is_button = isinstance(widget, Gtk.Button)
    if is_button and inside:
        found.append(widget.get_tooltip_text() or type(widget).__name__)
    child = widget.get_first_child()
    while child is not None:
        found.extend(_buttons_in_buttons(child, inside or is_button))
        child = child.get_next_sibling()
    return found


def _row_titled(widget, title: str):
    """The first button holding a label that reads ``title``."""
    from gi.repository import Gtk

    if isinstance(widget, Gtk.Button) and title in _label_texts(widget):
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = _row_titled(child, title)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None


def _images_in(widget) -> int:
    from gi.repository import Gtk

    count = 0
    child = widget.get_first_child()
    while child is not None:
        count += isinstance(child, Gtk.Image) + _images_in(child)
        child = child.get_next_sibling()
    return count


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
