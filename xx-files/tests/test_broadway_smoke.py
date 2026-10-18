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
from xx_files.main import FilesApp
from xx_files.prefs import Settings
os.environ["XX_FILES_SCENARIO"] = "files"
os.environ["XX_FILES_HOME"] = HOME
app = FilesApp()
GLib.timeout_add(1200, lambda: (app._app.quit(), False)[1])
code = app.run([])
print("FAILURES:" + str(len(failures)))
for item in failures:
    print(item)
sys.exit(1 if failures else 0)
"""


def _seed_home(home: Path) -> None:
    docs = home / "Documents"
    docs.mkdir(parents=True)
    (docs / "notes.txt").write_bytes(b"hello world" * 100)
    (docs / "report.pdf").write_bytes(b"%PDF" * 50)
    (home / "Downloads").mkdir(exist_ok=True)
    pics = home / "Pictures"
    pics.mkdir(exist_ok=True)
    (pics / "photo.jpg").write_bytes(b"img" * 4000)
    (pics / "clip.mp4").write_bytes(b"vid" * 4000)
    (home / "Music").mkdir(exist_ok=True)


def test_broadway_window_builds():
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    if shutil.which("dbus-daemon") is None:
        pytest.skip("dbus-daemon is missing")
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    workdir = Path(str(runtime) + f"/xx-files-bw-{os.getpid()}")
    workdir.mkdir(mode=0o700, parents=True, exist_ok=True)
    home = workdir / "home"
    home.mkdir(exist_ok=True)
    _seed_home(home)
    from xx_testing.broadway_display import start as _start_broadway

    # A probed free display (pid % 50 collided between parallel runs).
    daemon, display = _start_broadway(run_dir=str(workdir))
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
                    message="xx-files smoke: broadway socket never appeared")
        driver = DRIVER.replace("SRC", repr(SRC)).replace("COMMON",
                                                          repr(COMMON))
        driver = driver.replace("HOME", repr(str(home)))
        env = dict(os.environ, GDK_BACKEND="broadway",
                   BROADWAY_DISPLAY=display, GSK_RENDERER="cairo",
                   XDG_RUNTIME_DIR=str(workdir),
                   HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                   DBUS_SESSION_BUS_ADDRESS=address)
        proc = subprocess.run(
            [sys.executable, "-c", driver],
            capture_output=True, text=True, timeout=60, env=env)
        assert "FAILURES:0" in proc.stdout, proc.stdout + proc.stderr
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=10)
        except Exception:
            daemon.kill()
        bus.terminate()
        try:
            bus.wait(timeout=10)
        except Exception:
            bus.kill()
