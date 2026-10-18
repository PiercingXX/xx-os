"""Fresh-interpreter driver for the Broadway smoke test.

Run in a subprocess with GDK_BACKEND=broadway, BROADWAY_DISPLAY and
XDG_RUNTIME_DIR pointing at a live gtk4-broadwayd (test_broadway_smoke.py)
and HOME already seeded (tools/demo_seed.py). Builds the REAL window
OFFLINE (no API base, no keyring, no hub, no player service) with an
in-memory credential store:

* ``signed-in`` (default): a stored token. The window opens on the
  neutral Checking frame, lands on Library, and the walk covers Library,
  Book, a damaged Book, Series, Collections, Now Playing, Downloads,
  Zones, Setup and Stats from the cache alone.
* ``signed-out``: nothing stored. The window opens on Checking, then
  Sign in covers the whole window; the tabs never show.

Checks icon-only controls carry a label and a 48 px target, and prints
LABEL:/GATE:/FOCUS:/SIZE:/FAIL: lines. Exits 0 only when all hold.
"""

from __future__ import annotations

import sys
import threading


def _context(signed_in: bool):
    from xx_audiobook import api as api_module
    from xx_audiobook import auth as auth_module
    from xx_audiobook.ui.context import AppContext

    stored = None
    if signed_in:
        stored = auth_module.StoredCredentials(
            origin="https://estate.example", token="probe-token",
            user_id="u-probe", username="sam", display_name="Sam Reader",
            role="user")
    coordinator = auth_module.AuthCoordinator(
        store=auth_module.InMemoryCredentialStore(stored),
        probe=lambda: None)
    session = auth_module.SessionStore(coordinator,
                                       debug_override=lambda: None)
    # No API base: every call fails fast with NotSignedIn, never a socket.
    return AppContext(api_module.AudiobookApi(), session=session)


def main(argv: list[str]) -> int:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk

    from xx_audiobook import main as main_module

    if Gtk.init_check() is not True or Gdk.Display.get_default() is None:
        print("FAIL: no Broadway display")
        return 1
    mode = argv[1] if len(argv) > 1 else "signed-in"

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
    ctx = _context(mode == "signed-in")
    window = main_module.build_window(app, ctx)
    shell = window._ab
    failed = []
    gates = [shell.gate]
    shell.root.connect("notify::visible-child-name",
                       lambda *_a: gates.append(shell.gate))
    window.present()
    if gates[0] != "checking":
        failed.append(f"opened on {gates[0]!r}, not the Checking frame")
    if window.get_title() != "xx-audiobook":
        failed.append(f"window title {window.get_title()!r}")

    def pump(ms: int = 900) -> None:
        loop = GLib.MainLoop()
        GLib.timeout_add(ms, lambda: (loop.quit(), False)[1])
        loop.run()

    def expect(*lines: str) -> None:
        # Data lands on workers: give the page up to 4 s to paint.
        for _ in range(20):
            texts = _label_texts(window)
            if all(any(t == line or t.startswith(line) for t in texts)
                   for line in lines):
                return
            pump(200)
        texts = _label_texts(window)
        for line in lines:
            if not any(t == line or t.startswith(line) for t in texts):
                failed.append(f"missing {line!r}")

    def absent(*lines: str) -> None:
        texts = _label_texts(window)
        for line in lines:
            if line in texts:
                failed.append(f"unexpected {line!r}")

    pump(1500)
    print("GATE:" + ">".join(gates))
    focus = window.get_focus()
    print(f"FOCUS:{type(focus).__name__ if focus else 'None'}")
    if focus is not None and "Entry" in type(focus).__name__:
        failed.append("a text entry holds focus at startup")

    if mode == "signed-out":
        if shell.gate != "signin":
            failed.append(f"signed out but the gate shows {shell.gate!r}")
        if "app" in gates:
            failed.append("Library flashed before Sign in")
        expect("Audiobook · estate account", "Estate address", "Username",
               "Password", "Sign in", "I signed in to xx-apps — try again")
        absent("Continue listening", "Playing", "Downloads")
        _check_targets(window, failed)
        _finish(window, failed, criticals, errors)
        return 1 if failed or criticals or errors else 0

    if shell.gate != "app":
        failed.append(f"signed in but the gate shows {shell.gate!r}")
    if "signin" in gates:
        failed.append("Sign in flashed for a signed-in user")
    expect("Library", "Project Hail Mary", "Continue listening", "Sort",
           "Show")
    spoken = [b.get_tooltip_text() for b in shell.tab_buttons.values()
              if b.get_mapped()]
    if spoken != ["Library", "Now Playing", "Downloads", "Zones", "Setup"]:
        failed.append(f"bottom bar reads {spoken!r}")
    _check_targets(window, failed)

    shell.nav.open_book("ph")
    pump()
    expect("Project Hail Mary", "Resume at ", "Chapters (30)", "Bookmarks",
           "Rocky's first words", "Downloaded", "Mark finished",
           "Ratings & reviews")
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
    expect("Wayfarers", "Series · 3 books", "A Closed and Common Orbit",
           "#2")

    shell.nav.collections()
    pump()
    expect("Collections", "Not connected to the estate server.")

    shell.show("playing")
    pump()
    expect("Paused where you left off.", "Project Hail Mary", "NOW PLAYING",
           "Play on…")
    _check_targets(window, failed)

    # A sheet on a phone-width window docks as a full-width bottom sheet
    # (Adw's AUTO floats a 360 px card on the 450 px FLX1).
    from xx_audiobook.ui import common

    dialog, _box = common.sheet("Probe")
    common.present(dialog, window)
    pump(100)
    width = window.get_width() or window.get_default_size().width
    sheet_mode = dialog.get_presentation_mode()
    print(f"SHEET:{sheet_mode.value_nick}:{dialog.get_content_width()}")
    if sheet_mode != Adw.DialogPresentationMode.BOTTOM_SHEET:
        failed.append("a phone sheet floats instead of docking")
    if dialog.get_content_width() != width:
        failed.append("the bottom sheet is narrower than the phone")
    dialog.close()
    pump(300)

    shell.show("downloads")
    pump()
    expect("Downloads", "Active", "On this phone", "Failed or cancelled",
           "Dune", "Checksum mismatch", "Storage used: ")
    _check_targets(window, failed)

    shell.show("zones")
    pump()
    expect("Zones", "On this phone")
    _check_targets(window, failed)

    shell.show("setup")
    pump()
    expect("Setup", "Account", "Sam Reader", "Listening stats", "Playback",
           "Skip back", "Sign out")
    _check_targets(window, failed)

    shell.nav.stats()
    pump()
    expect("Listening stats", "Recent sessions")
    _check_targets(window, failed)

    # Sign out: back to the full-window Sign in, the tabs gone.
    ctx.sign_out()
    pump()
    if shell.gate != "signin":
        failed.append(f"after sign-out the gate shows {shell.gate!r}")
    _finish(window, failed, criticals, errors)
    return 1 if failed or criticals or errors else 0


def _finish(window, failed, criticals, errors) -> None:
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
