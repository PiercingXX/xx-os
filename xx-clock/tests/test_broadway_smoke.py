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
from xx_clock.main import ClockApp
os.environ["XX_CLOCK_LOCAL"] = "1"
os.environ["XX_CLOCK_SEED"] = "1"
app = ClockApp()
window = {}
app._app.connect("activate", lambda a: _open(a))
def _open(a):
    try:
        for tab in ("alarms", "timers", "setup", "clock"):
            app.window.show_tab(tab)
        app.window.clock.tick()
        app.window.timers.tick()
        # A rejected custom value is marked until the next keystroke.
        entry = app.window.timers._custom
        entry.set_text("0")
        app.window.timers._start_custom()
        assert entry.has_css_class("error"), "rejected minutes not marked"
        entry.set_text("5")
        assert not entry.has_css_class("error"), "error mark outlived the edit"
        # Setup lines wrap at 320 px / large text instead of ellipsizing.
        from gi.repository import Gtk, Pango
        stack = [app.window.setup.widget]
        while stack:
            w = stack.pop()
            if isinstance(w, Gtk.Label):
                assert w.get_ellipsize() == Pango.EllipsizeMode.NONE, w.get_text()
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        window["ok"] = True
    except Exception:
        failures.append(traceback.format_exc())
    GLib.timeout_add(300, _ring)
    return False
def _ring():
    try:
        app.window.on_ringing(99, True, "alarm", "Seed")
    except Exception:
        failures.append(traceback.format_exc())
    GLib.timeout_add(300, _check_ring)
    return False
def _check_ring():
    try:
        assert app.window._ring is not None, "ring card never built"
        app.window._ring.hide()
        app.window.open_editor(None)
        assert app.window._editor is not None, "editor never built"
        app.window._editor.dialog.close()
        window["surfaces"] = True
    except Exception:
        failures.append(traceback.format_exc())
    return False
GLib.timeout_add(1200, lambda: (app._app.quit(), False)[1])
code = app.run([])
print("FAILURES:" + str(len(failures)))
for item in failures:
    print(item)
print("OPENED:" + str(window.get("ok", False)))
print("SURFACES:" + str(window.get("surfaces", False)))
sys.exit(1 if failures or not (window.get("ok") and window.get("surfaces")) else 0)
"""


def test_broadway_window_builds():
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    if shutil.which("dbus-daemon") is None:
        pytest.skip("dbus-daemon is missing")
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    workdir = Path(str(runtime) + f"/xx-clock-bw-{os.getpid()}")
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
                    message="xx-clock smoke: broadway socket never appeared")
        driver = DRIVER.replace("SRC", repr(SRC)).replace("COMMON",
                                                          repr(COMMON))
        env = dict(os.environ, GDK_BACKEND="broadway",
                   BROADWAY_DISPLAY=display, GSK_RENDERER="cairo",
                   XDG_RUNTIME_DIR=str(workdir),
                   HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                   XDG_DATA_HOME=str(home / ".local" / "share"),
                   XDG_STATE_HOME=str(home / ".local" / "state"),
                   DBUS_SESSION_BUS_ADDRESS=address,
                   PYTHONPATH=os.pathsep.join([SRC, COMMON]))
        proc = subprocess.run(
            [sys.executable, "-c", driver], capture_output=True, text=True,
            timeout=60, env=env)
        assert "FAILURES:0" in proc.stdout, proc.stdout + proc.stderr
        assert "OPENED:True" in proc.stdout, proc.stdout + proc.stderr
        assert "SURFACES:True" in proc.stdout, proc.stdout + proc.stderr
        assert proc.returncode == 0, proc.stdout + proc.stderr
    finally:
        daemon.terminate()
        bus.terminate()
        try:
            daemon.wait(timeout=10)
        except Exception:
            daemon.kill()
        try:
            bus.wait(timeout=10)
        except Exception:
            bus.kill()
