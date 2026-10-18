"""Broadway UX regressions on the REAL window: minimal states, the car
picker, self-clearing notes, whole labels, live theme watch."""
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
def check(cond, what):
    if not cond:
        failures.append(what)
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk
from xx_auto import main as main_mod
from xx_auto.main import AutoApp


def children(box):
    out = []
    child = box.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


def labels(widget):
    out = []
    if isinstance(widget, Gtk.Label):
        out.append(widget.get_label())
    child = widget.get_first_child()
    while child is not None:
        out.extend(labels(child))
        child = child.get_next_sibling()
    return out


app = AutoApp()


def _run(a):
    try:
        w = app.window
        # Never touch the host's user units from a test.
        w._apply_watch_unit = lambda on: None
        # Always black on: the suite config watch is armed anyway, so a
        # font or text-size change still reaches the screen live.
        import xx_ui.css as css
        check(css._monitor_key is not None, "theme watch not armed")

        # Nothing playing with both players installed: one title line,
        # no hint underneath.
        w.seed_demo("nothing_playing")
        check(w.title_label.get_label() == "Nothing playing", "idle title")
        check(not w.sub_label.get_visible(), "idle hint line shown")

        # Empty lists show nothing; a failed lookup is one line.
        w._render_favourites([], "")
        check(children(w._fav_body) == [], "empty favourites not empty")
        w._render_players_sheet()
        w._players_rows = []
        w._render_players_sheet()
        check(children(w._players_body) == [], "empty players not empty")
        w._render_favourites([], "boom")
        check(labels(w._fav_body) == ["Contacts are not available"],
              "contacts error copy: %r" % labels(w._fav_body))
        w._car_devices = []
        w._render_car_sheet()
        check(children(w._car_body) == [], "empty car list not empty")

        # The device row lives under auto-launch; the pick is marked.
        w.seed_demo("car")
        check(w._car_row.get_visible(), "car row hidden with auto-launch on")
        check(w._car_sub.get_label() == "Car Audio", "car sub shows name")
        rows = children(w._car_body)
        check([r.has_css_class("selected") for r in rows] == [True, False],
              "picked car not marked")
        w._switches["auto_launch"].set_active(False)
        check(not w._car_row.get_visible(), "car row shown with auto-launch off")
        w.settings.auto_launch = True
        w._sync_switches()
        check(w._switches["auto_launch"].get_active(), "switch not synced")

        # A failure note clears itself.
        w._set_note("Could not place call.")
        check(w.note_label.get_visible(), "note not shown")
        w._clear_note("Could not place call.")
        check(not w.note_label.get_visible(), "note did not clear")

        # Missing maps reads like the other missing tiles.
        w.seed_demo("nothing_installed")
        check(w.tile_maps._sub_label.get_label() == "not installed",
              "maps tile copy")
        check(w.tile_maps.has_css_class("missing"), "maps missing style")
        check(w.tile_maps.get_sensitive(), "maps tile must stay tappable")

        # Tile names and sheet headings never ellipsize.
        for tile in w._tiles.values():
            check(int(tile._title_label.get_ellipsize()) == 0,
                  "tile ellipsizes")
    except Exception:
        failures.append(traceback.format_exc())
    a.quit()
    return False


app._app.connect("activate", lambda a: GLib.timeout_add(150, _run, a))
app.run([])
print("FAILURES:" + str(len(failures)))
for item in failures:
    print(item)
sys.exit(1 if failures else 0)
"""


def test_drive_ux_states():
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    if shutil.which("dbus-daemon") is None:
        pytest.skip("dbus-daemon is missing")
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    workdir = Path(str(runtime) + f"/xx-auto-ux-{os.getpid()}")
    workdir.mkdir(mode=0o700, parents=True, exist_ok=True)
    home = workdir / "home"
    (home / ".config" / "xx-auto").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "xx-auto" / "settings.json").write_text(
        '{"always_ink": true}')
    bus = subprocess.Popen(
        ["dbus-daemon", "--session", "--print-address", "--nofork"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        env=dict(os.environ, XDG_RUNTIME_DIR=str(workdir)))
    daemon = None
    try:
        address = bus.stdout.readline().strip()
        assert address
        from xx_testing.broadway_wait import poll_until

        # Other suites run Broadway too: a taken display (or its port)
        # kills the daemon, so move on to the next number.
        for attempt in range(5):
            display_num = 40 + (os.getpid() + 11 * attempt) % 50
            display = f":{display_num}"
            daemon = subprocess.Popen(
                ["gtk4-broadwayd", display],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=dict(os.environ, XDG_RUNTIME_DIR=str(workdir)))
            socket_path = workdir / f"broadway{display_num + 1}.socket"
            if poll_until(lambda: socket_path.exists()
                          or daemon.poll() is not None, 15.0, 0.05) \
                    and socket_path.exists():
                break
            daemon.terminate()
            daemon.wait(timeout=10)
            daemon = None
        assert daemon is not None, "xx-auto ux: no broadway display"
        driver = DRIVER.replace("SRC", repr(SRC)).replace("COMMON",
                                                          repr(COMMON))
        env = dict(os.environ, GDK_BACKEND="broadway",
                   BROADWAY_DISPLAY=display, GSK_RENDERER="cairo",
                   XDG_RUNTIME_DIR=str(workdir),
                   HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                   DBUS_SESSION_BUS_ADDRESS=address,
                   PYTHONPATH=os.pathsep.join([SRC, COMMON]))
        env.pop("XX_WM_SESSION", None)
        proc = subprocess.run(
            [sys.executable, "-c", driver], capture_output=True, text=True,
            timeout=60, env=env)
        assert "FAILURES:0" in proc.stdout, proc.stdout + proc.stderr
        assert proc.returncode == 0, proc.stdout + proc.stderr
    finally:
        if daemon is not None:
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
        shutil.rmtree(workdir, ignore_errors=True)
