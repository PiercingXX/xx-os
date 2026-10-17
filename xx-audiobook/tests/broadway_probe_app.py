"""Fresh-interpreter driver for the Broadway smoke test.

Run in a subprocess with GDK_BACKEND=broadway, BROADWAY_DISPLAY and
XDG_RUNTIME_DIR pointing at a live gtk4-broadwayd (test_broadway_smoke.py)
and HOME already seeded (tools/demo_seed.py). Builds the REAL window
OFFLINE (no API base, no keyring, no player service), walks Library,
Book, a damaged Book, Series, Collections and Now Playing from the cache
alone, checks icon-only controls carry a label and a 48 px target, and
prints LABEL:/FOCUS:/SIZE:/FAIL: lines. Exits 0 only when all hold.
"""

from __future__ import annotations

import sys
import threading


def main(argv: list[str]) -> int:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk

    from xx_audiobook import api as api_module
    from xx_audiobook import main as main_module
    from xx_audiobook.ui.context import AppContext

    if Gtk.init_check() is not True or Gdk.Display.get_default() is None:
        print("FAIL: no Broadway display")
        return 1

    errors: list = []
    criticals: list = []
    sys.excepthook = lambda exc_type, value, _tb: errors.append(repr(value))
    threading.excepthook = lambda args: errors.append(repr(args.exc_value))

    def log_handler(_domain, level, message, _data) -> None:
        if level & (GLib.LogLevelFlags.LEVEL_CRITICAL
                    | GLib.LogLevelFlags.LEVEL_ERROR):
            criticals.append(str(message))

    levels = (GLib.LogLevelFlags.LEVEL_CRITICAL
              | GLib.LogLevelFlags.LEVEL_ERROR)
    for domain in ("Gtk", "Gdk", "Adwaita", "GLib", "Gio"):
        GLib.log_set_handler(domain, levels, log_handler, None)

    app = Adw.Application(application_id="io.piercingxx.XXAudiobook",
                          flags=Gio.ApplicationFlags.NON_UNIQUE)
    if app.register(None) is not True:
        print("FAIL: app registration failed")
        return 1
    ctx = AppContext(api_module.AudiobookApi())
    window = main_module.build_window(app, ctx)
    window.present()
    shell = window._ab
    failed = []

    def pump(ms: int = 900) -> None:
        loop = GLib.MainLoop()
        GLib.timeout_add(ms, lambda: (loop.quit(), False)[1])
        loop.run()

    def expect(*lines: str) -> None:
        texts = _label_texts(window)
        for line in lines:
            if not any(t == line or t.startswith(line) for t in texts):
                failed.append(f"missing {line!r}")

    pump(1500)
    focus = window.get_focus()
    print(f"FOCUS:{type(focus).__name__ if focus else 'None'}")
    if focus is not None and "Entry" in type(focus).__name__:
        failed.append("a text entry holds focus at startup")
    expect("Library", "LIBRARY", "PLAYING", "DOWNLOADS", "ZONES", "SETUP",
           "Project Hail Mary", "Not signed in. Sign in from Setup.",
           "CONTINUE LISTENING")
    _check_targets(window, failed)

    shell.nav.open_book("ph")
    pump()
    expect("Project Hail Mary", "Resume at ", "CHAPTERS · 30", "BOOKMARKS",
           "Rocky's first words", "Download", "Mark finished")
    _check_targets(window, failed)

    shell.nav.open_book("hy")
    pump()
    expect("This book's audio is damaged or incomplete.")
    if any(isinstance(w, Gtk.Button) and w.get_mapped() and w.is_sensitive()
           and w.get_child() is not None
           and isinstance(w.get_child(), Gtk.Label)
           and w.get_child().get_text() == "Play"
           for w in _walk(window)):
        failed.append("Play is enabled on a damaged book")

    shell.nav.browse("series", "Wayfarers", 7)
    pump()
    expect("Wayfarers", "A Closed and Common Orbit", "#2")

    shell.nav.collections()
    pump()
    expect("Collections", "Not signed in. Sign in from Setup.")

    shell.show("playing")
    pump()
    expect("Paused where you left off.", "Project Hail Mary")
    _check_targets(window, failed)

    width = window.get_width()
    print(f"SIZE:{window.get_default_size().width}"
          f"x{window.get_default_size().height}")
    print(f"WIDTH:{width}")
    if width > 360:
        failed.append(f"window grew to {width} px")
    for line in failed:
        print(f"FAIL: {line}")
    for message in criticals:
        print(f"CRITICAL:{message}")
    for message in errors:
        print(f"ERROR:{message}")
    window.destroy()
    return 1 if failed or criticals or errors else 0


def _walk(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from _walk(child)
        child = child.get_next_sibling()


def _check_targets(window, failed: list) -> None:
    """Icon-only buttons: a label for screen readers and 48 px targets."""
    from gi.repository import Gtk

    for widget in _walk(window):
        if not isinstance(widget, Gtk.Button) or not widget.get_mapped():
            continue
        child = widget.get_child()
        if not isinstance(child, (Gtk.Image, Gtk.DrawingArea)):
            continue
        if not widget.get_tooltip_text():
            failed.append(f"unlabelled icon button {child!r}")
        # Requested minimums: headless Broadway may not have laid out a
        # freshly pushed page yet, so allocations can still read 0.
        low_w = widget.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        low_h = widget.measure(Gtk.Orientation.VERTICAL, -1)[0]
        if low_w < 48 or low_h < 48:
            failed.append(f"small target {widget.get_tooltip_text()!r} "
                          f"{low_w}x{low_h}")


def _label_texts(widget) -> list[str]:
    from gi.repository import Gtk

    return [w.get_text() for w in _walk(widget)
            if isinstance(w, Gtk.Label) and w.get_mapped()]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
