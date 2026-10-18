"""Broadway smoke: the REAL main window builds and pumps ~1 s clean."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).parent.parent / "src")
COMMON = str(Path(__file__).parent.parent.parent / "common")

DRIVER = """\
import os, sys, traceback
sys.path.insert(0, SRC)
sys.path.insert(0, COMMON)
failures = []
def _hook(kind, value, tb):
    failures.append("".join(traceback.format_exception(kind, value, tb)))
sys.excepthook = _hook
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib
from xx_note import paths as paths_mod
from xx_note import prefs as prefs_mod
from xx_note import secrets as secrets_mod
from xx_note import store as store_mod
from xx_note.ui import shell as shell_mod
os.environ["XX_NOTE_SCENARIO"] = "full"
paths_mod.ensure_app_dirs()
settings = prefs_mod.load()
vault = store_mod.Vault(settings.resolved_notes_dir())
vault.ensure()
app = Adw.Application(application_id="io.piercingxx.XXNote")
holder = {}
def _activate(a):
    try:
        import xx_ui
        from gi.repository import Adw as _Adw
        from gi.repository import Gdk as _Gdk
        from gi.repository import Gtk as _Gtk
        from gi.repository import Pango as _Pango
        win = shell_mod.NoteWindow(a, vault, settings,
                                   secrets_mod.MemorySecrets(),
                                   _Adw, _Gtk, _Pango, GLib)
        holder["win"] = win
        win.seed_demo("full")
        win.refresh()
        try:
            xx_ui.apply(win.win)
        except Exception:
            pass
        # The narrowest phone: the selection bar keeps "2 selected" whole.
        win.win.set_default_size(320, 640)
        win.win.set_size_request(320, 640)
        win.win.present()
    except Exception:
        failures.append(traceback.format_exc())
def _select():
    w = holder["win"]
    w._on_long_press(None, 0, 0, w._notes[0].id)
    w._on_long_press(None, 0, 0, w._notes[2].id)
    return False
def _check_select():
    # Headless Broadway never re-lays out the bar, so measure it: with
    # the label unellipsized, the whole bar must still fit 320 px.
    from gi.repository import Gtk as _Gtk
    from gi.repository import Pango as _Pango
    w = holder["win"]
    label = w.select_label
    text = label.get_text()
    if text != "2 selected":
        failures.append("selection label reads " + repr(text))
        return False
    mode = label.get_ellipsize()
    label.set_ellipsize(_Pango.EllipsizeMode.NONE)
    need = label.get_parent().measure(_Gtk.Orientation.HORIZONTAL, -1)[0]
    label.set_ellipsize(mode)
    if need > 320:
        failures.append("selection bar needs %d px at 320" % need)
    return False
def _check_back():
    # Escape / a back key steps back one level: selection, then search,
    # then a pushed page; at a tab root it lets the key through.
    w = holder["win"]
    steps = []
    steps.append(w.back() and not w._selecting)
    w.toggle_search()
    steps.append(w.back() and not w._search_open)
    w.show_page("settings")
    steps.append(w.back() and w._page == "notes")
    w.show_page("sync")
    steps.append(w.back() and w._page == "notes")
    steps.append(not w.back())
    if not all(steps):
        failures.append("back steps: " + repr(steps))
    return False
def _trash_from_editor():
    # The editor's Trash icon leaves at once and moves the note.
    w = holder["win"]
    loft = next(n for n in w._notes if n.title == "Loft")
    holder["loft"] = loft.path
    w.open_note(loft.id)
    w.editor_page.trash_button.emit("clicked")
    if w._page != "notes":
        failures.append("trash left the editor on " + repr(w._page))
    return False
def _check_trashed():
    names = [n.path for n in vault.scan_trash()]
    if holder.get("loft") not in names:
        failures.append("Loft not in trash: " + repr(names))
    if any(n.path == holder.get("loft") for n in vault.scan()):
        failures.append("Loft still in notes")
    return False
app.connect("activate", _activate)
GLib.timeout_add(500, _select)
GLib.timeout_add(1000, _check_select)
GLib.timeout_add(1100, _check_back)
GLib.timeout_add(1200, _trash_from_editor)
GLib.timeout_add(2200, _check_trashed)
GLib.timeout_add(2400, lambda: (app.quit(), False)[1])
code = app.run([])
print("FAILURES:" + str(len(failures)))
for item in failures:
    print(item)
sys.exit(1 if failures else 0)
"""


def test_broadway_window_builds():
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    if shutil.which("dbus-daemon") is None:
        pytest.skip("dbus-daemon is missing")
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    workdir = Path(str(runtime) + f"/xx-note-bw-{os.getpid()}")
    workdir.mkdir(mode=0o700, parents=True, exist_ok=True)
    home = workdir / "home"
    home.mkdir(exist_ok=True)
    display = f":{40 + os.getpid() % 50}"
    daemon = subprocess.Popen(
        ["gtk4-broadwayd", display],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=dict(os.environ, XDG_RUNTIME_DIR=str(workdir)))
    bus = subprocess.Popen(
        ["dbus-daemon", "--session", "--print-address", "--nofork"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        env=dict(os.environ, XDG_RUNTIME_DIR=str(workdir)))
    try:
        address = bus.stdout.readline().strip()
        assert address
        from xx_testing.broadway_wait import wait_until as _wait_until

        display_num = int(str(display).lstrip(":"))
        socket_path = workdir / f"broadway{display_num + 1}.socket"
        _wait_until(socket_path.exists, timeout_s=15.0,
                    message="xx-note smoke: broadway socket never appeared")
        driver = DRIVER.replace("SRC", repr(SRC)).replace("COMMON",
                                                          repr(COMMON))
        env = dict(os.environ, GDK_BACKEND="broadway",
                   BROADWAY_DISPLAY=display, GSK_RENDERER="cairo",
                   XDG_RUNTIME_DIR=str(workdir),
                   HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                   XDG_DATA_HOME=str(home / ".local" / "share"),
                   DBUS_SESSION_BUS_ADDRESS=address)
        proc = subprocess.run(
            [sys.executable, "-c", driver], capture_output=True, text=True,
            timeout=60, env=env)
        assert "FAILURES:0" in proc.stdout, proc.stdout + proc.stderr
    finally:
        for proc in (bus, daemon):
            try:
                proc.terminate()
            except Exception:
                pass
        try:
            bus.wait(timeout=10)
        except Exception:
            try:
                bus.kill()
            except Exception:
                pass
        try:
            daemon.wait(timeout=10)
        except Exception:
            try:
                daemon.kill()
            except Exception:
                pass
