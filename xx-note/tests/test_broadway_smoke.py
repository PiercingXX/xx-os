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
        win.win.present()
    except Exception:
        failures.append(traceback.format_exc())
app.connect("activate", _activate)
GLib.timeout_add(1200, lambda: (app.quit(), False)[1])
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
