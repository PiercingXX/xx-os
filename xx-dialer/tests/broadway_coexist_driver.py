"""Broadway coexistence driver (run as a subprocess under dbus-run-session).

Starts the REAL policy service subprocess, waits until it owns
``io.piercingxx.XXDialer.Policy``, then registers the REAL GUI
application id (``io.piercingxx.XXDialer``) and activates a real window
built by ``build_dialer_view``. Proves the service no longer squats
the name ``Gtk.Application`` needs. Prints KEY: value markers; any
failure exits nonzero. Not collected by pytest.
"""
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
sys.path.insert(0, SRC)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "..", "common"))

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib  # noqa: E402

Adw.init()

import xx_ui  # noqa: E402

from xx_dialer.main import (  # noqa: E402
    APP_ID,
    DialerDeps,
    build_dialer_view,
)
from xx_dialer.service import BUS_NAME, INTERFACE, OBJECT_PATH  # noqa: E402
from xx_testing.broadway_wait import pump_until as _wait_until  # noqa: E402

HOME = Path(os.environ["XX_DIALER_TEST_HOME"])
DB_FILE = HOME / ".local" / "share" / "xx-dialer" / "calls.db"

ERRORS: list[str] = []


def _hook(kind, value, tb) -> None:
    text = "".join(traceback.format_exception(kind, value, tb))
    ERRORS.append(text)
    sys.stderr.write(text)
    sys.stderr.flush()


sys.excepthook = _hook


class _Voice:
    def place(self, number, on_done):
        on_done(True, "/call/0")


def _wait_owned(bus, timeout_s: float = 25.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            owner = bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "GetNameOwner",
                GLib.Variant("(s)", (BUS_NAME,)), None,
                Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
        except Exception:
            owner = ""
        if owner:
            return True
        time.sleep(0.2)
    return False


def main() -> int:
    env = dict(os.environ)
    env["HOME"] = str(HOME)
    common = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "..", "common")
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (SRC, common, env.get("PYTHONPATH", ""))
        if part)
    service = subprocess.Popen(
        [sys.executable, "-m", "xx_dialer", "--service"], env=env)
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        owned = _wait_owned(bus)
        print("SERVICE_OWNED:", owned, flush=True)
        if not owned:
            return 1
        app = Adw.Application(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_OPEN)

        def _on_activate(active_app) -> None:
            deps = DialerDeps(db_file=DB_FILE, home=HOME,
                              voice=_Voice(), contacts=object())
            view = build_dialer_view(deps)
            window = Adw.ApplicationWindow(application=active_app)
            window.set_title("XX Dialer")
            window.set_default_size(360, 720)
            window.set_content(view.widget)
            xx_ui.apply(window)
            window.present()

        app.connect("activate", _on_activate)
        try:
            registered = bool(app.register(None))
        except Exception:
            registered = False
        print("REGISTERED:", registered, flush=True)
        remote = bool(app.get_is_remote()) if registered else True
        print("IS_REMOTE:", remote, flush=True)
        if not registered or remote:
            return 1
        app.activate()
        if not _wait_until(lambda: len(list(app.get_windows())) > 0,
                           timeout_s=10.0):
            sys.stderr.write("coexist: GUI window never opened\n")
            return 1
        windows = list(app.get_windows())
        print("WINDOW:", len(windows) > 0, flush=True)
        if not windows:
            return 1
        try:
            verdict, reason = bus.call_sync(
                BUS_NAME, OBJECT_PATH, INTERFACE, "CheckNumber",
                GLib.Variant("(s)", ("+14155550100",)),
                GLib.VariantType.new("(ss)"),
                Gio.DBusCallFlags.NONE, 5000, None).unpack()
        except Exception as err:
            sys.stderr.write("service stopped answering: %s\n" % (err,))
            return 1
        print("SERVICE_VERDICT:", verdict, flush=True)
        print("SERVICE_REASON:", reason, flush=True)
        if verdict not in ("ring", "silence", "block"):
            return 1
        print("EXCEPTIONS:", len(ERRORS), flush=True)
        return 1 if ERRORS else 0
    finally:
        try:
            service.terminate()
        except Exception:
            pass
        try:
            service.wait(timeout=10)
        except Exception:
            try:
                service.kill()
            except Exception:
                pass


sys.exit(main())
