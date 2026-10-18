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
from xx_weather.main import WeatherApp
os.environ["XX_WEATHER_SCENARIO"] = "current"
app = WeatherApp()
GLib.timeout_add(1200, lambda: (app._app.quit(), False)[1])
code = app.run([])
print("FAILURES:" + str(len(failures)))
for item in failures:
    print(item)
sys.exit(1 if failures else 0)
"""

# The forecast's hourly strip at large text: every time label keeps its
# words ("10 PM" was ellipsized to "10 ..." at text-size-scale 1.3),
# probed at 1.5.
HOURLY_DRIVER = """\
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
from gi.repository import Adw, GLib, Gtk
from xx_weather.main import WeatherApp
os.environ["XX_WEATHER_SCENARIO"] = "current"
app = WeatherApp()

def labels(widget):
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Label):
            yield child
        yield from labels(child)
        child = child.get_next_sibling()

def find_strip(widget):
    if widget.has_css_class("xx-weather-hours-strip"):
        return widget
    child = widget.get_first_child()
    while child is not None:
        found = find_strip(child)
        if found is not None:
            return found
        child = child.get_next_sibling()
    return None

def check():
    strip = find_strip(app.window.win)
    if strip is None:
        failures.append("no hourly strip")
    else:
        times = [lab for lab in labels(strip)
                 if lab.get_text().endswith(("AM", "PM"))]
        print("HOURS:" + ",".join(lab.get_text() for lab in times))
        for lab in times:
            if lab.get_layout().is_ellipsized():
                failures.append("hour label cut: " + lab.get_text())
    app._app.quit()
    return False

GLib.timeout_add(1200, check)
code = app.run([])
print("FAILURES:" + str(len(failures)))
for item in failures:
    print(item)
sys.exit(1 if failures else 0)
"""

# A phone user's walk: in Celsius the cold-alert chips read in °C, the
# Places list marks the place the forecast shows, and the back gesture
# (Escape) leaves a side tab for the forecast.
UX_DRIVER = """\
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
from gi.repository import Adw, Gdk, GLib, Gtk
from xx_weather.main import WeatherApp
os.environ["XX_WEATHER_SCENARIO"] = "places-c"
os.environ["XX_WEATHER_PAGE"] = "settings"
app = WeatherApp()

def walk(widget):
    child = widget.get_first_child()
    while child is not None:
        yield child
        yield from walk(child)
        child = child.get_next_sibling()

def texts(widget):
    return [w.get_text() for w in walk(widget) if isinstance(w, Gtk.Label)]

def check():
    win = app.window
    win._on_alerts_enabled(True)
    win._with_triggers(win.store.selected().zip, temp_enabled=True)
    chips = [t for t in texts(win.alerts_box) if t.endswith("\u00b0")]
    print("CHIPS:" + ",".join(chips))
    if chips != ["-7\u00b0", "-2\u00b0", "0\u00b0", "4\u00b0"]:
        failures.append("cold chips not in Celsius: " + repr(chips))
    if any("\u00b0F" in t for t in texts(win.alerts_box)):
        failures.append("a \u00b0F label in Celsius")
    marks = [w for w in walk(win.places_body)
             if w.has_css_class("xx-weather-shown")]
    if len(marks) != 1:
        failures.append("shown-place marks: %d" % len(marks))
    if not win._on_key(None, Gdk.KEY_Escape, 0, 0) \
            or win.page != "forecast":
        failures.append("Escape on settings did not go to the forecast")
    if win._on_key(None, Gdk.KEY_Escape, 0, 0):
        failures.append("Escape on the forecast was swallowed")
    app._app.quit()
    return False

GLib.timeout_add(1200, check)
code = app.run([])
print("FAILURES:" + str(len(failures)))
for item in failures:
    print(item)
sys.exit(1 if failures else 0)
"""


def test_celsius_chips_shown_place_and_back():
    _run(UX_DRIVER, "ux", "")


def test_broadway_window_builds():
    _run(DRIVER, "smoke", "")


def test_hourly_times_fit_at_large_text():
    done = _run(HOURLY_DRIVER, "hourly", '{"text_size_scale": 1.5}')
    hours = done.stdout.split("HOURS:", 1)[1].splitlines()[0]
    assert "PM" in hours or "AM" in hours, done.stdout


def _run(driver_code: str, name: str, config: str):
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    if shutil.which("dbus-daemon") is None:
        pytest.skip("dbus-daemon is missing")
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    workdir = Path(str(runtime) + f"/xx-weather-bw-{name}-{os.getpid()}")
    workdir.mkdir(mode=0o700, parents=True, exist_ok=True)
    home = workdir / "home"
    home.mkdir(exist_ok=True)
    if config:
        (home / ".config" / "xx-wm").mkdir(parents=True, exist_ok=True)
        (home / ".config" / "xx-wm" / "config.json").write_text(config)
    from xx_testing.broadway_display import start as _start_broadway

    # A probed free display (pid % 50 collided between parallel runs).
    daemon, display = _start_broadway(run_dir=str(workdir))
    driver = workdir / "driver.py"
    try:
        code = driver_code.replace("SRC", repr(SRC)).replace(
            "COMMON", repr(COMMON))
        driver.write_text(code, encoding="utf-8")
        env = dict(os.environ,
                   GDK_BACKEND="broadway",
                   BROADWAY_DISPLAY=display,
                   XDG_RUNTIME_DIR=str(workdir),
                   HOME=str(home),
                   XDG_CONFIG_HOME=str(home / ".config"),
                   XDG_DATA_HOME=str(home / ".local" / "share"),
                   XDG_CACHE_HOME=str(home / ".cache"),
                   DBUS_SESSION_BUS_ADDRESS="",
                   GSETTINGS_BACKEND="memory")
        done = subprocess.run(
            [sys.executable, str(driver)],
            capture_output=True, text=True, timeout=60, env=env,
            cwd=str(workdir),
        )
        assert "FAILURES:0" in done.stdout, \
            f"stdout={done.stdout}\nstderr={done.stderr}"
        return done
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=10)
        except Exception:
            daemon.kill()
