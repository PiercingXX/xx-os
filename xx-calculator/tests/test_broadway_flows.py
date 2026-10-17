"""Broadway flows: scripted Play-edition behaviors through the real window.

Preview, post-equals continuation, editable errors, history tap rules,
INV/DEG toggles, backspace hold, and landscape all run against the real
CalcWindow under broadway + a private bus.
"""
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
def check(name, cond, extra=""):
    if not cond:
        failures.append(f"{name} FAILED {extra}")
sys.excepthook = lambda k, v, tb: failures.append("".join(traceback.format_exception(k, v, tb)))
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib
from xx_calculator.main import CalcApp
app = CalcApp()
def _flows():
    try:
        w = app.window
        w.builder.clear(); w.builder.raw("2+3×4"); w._on_edited()
        check("preview", w.result_label.get_text() == "14", w.result_label.get_text())
        w._on_equals()
        check("equals-bright", w._bright and w.result_label.get_text() == "14")
        check("history-stored", len(w.store.list()) == 1 and w.store.list()[0].result == "14")
        w._digit("5")()
        check("digit-after-equals-clears", w.builder.expression == "5" and not w._bright)
        w._on_equals()
        w._on_plus()
        check("operator-continues", w.builder.expression == "5+")
        w.builder.clear(); w.builder.raw("5÷0"); w._on_edited(); w._on_equals()
        check("divide-error", w.result_label.get_text() == "Can't divide by 0", w.result_label.get_text())
        check("error-keeps-expr", w.builder.expression == "5÷0")
        w.refresh()
        check("error-survives-refresh", w.result_label.get_text() == "Can't divide by 0", w.result_label.get_text())
        w.builder.backspace(); w.builder.raw("1"); w._on_edited()
        check("error-clears-on-edit", w.result_label.get_text() == "5", w.result_label.get_text())
        w.builder.clear(); w._on_edited()
        w.store.clear()
        w.store.add("1+2", "3", 1000)
        w.store.add("4×5", "20", 2000)
        w.show("history")
        row = w.hist_list.get_row_at_index(0)
        w._on_history_row(w.hist_list, row)
        check("history-tap-appends-at-empty", w.builder.expression == "20", w.builder.expression)
        w.builder.clear(); w.builder.raw("2+"); w._on_edited()
        w._on_history_row(w.hist_list, row)
        check("history-tap-appends-at-operator", w.builder.expression == "2+20", w.builder.expression)
        w.builder.clear(); w.builder.raw("2"); w._on_edited()
        w._on_history_row(w.hist_list, row)
        check("history-tap-seeds-after-value", w.builder.expression == "20", w.builder.expression)
        w.builder.clear(); w._on_edited()
        w._on_inv()
        check("inv-labels", w.sin_btn.get_label() == "asin" and w.ln_btn.get_label() == "eˣ")
        check("inv-class", "xx-calc-inv-on" in w.inv_btn.get_css_classes() if hasattr(w.inv_btn, "get_css_classes") else True)
        w._on_inv()
        check("inv-off", w.sin_btn.get_label() == "sin")
        w._on_deg()
        check("deg-label", w.deg_btn.get_label() == "RAD")
        w._on_deg()
        w.builder.clear(); w.builder.raw("999"); w._on_edited()
        w._on_backspace_hold()
        check("hold-clears", w.builder.expression == "")
        w.set_landscape(True)
        check("landscape-science", w._science_expanded)
        w.set_landscape(False)
        check("portrait-back", not w._landscape)
        w._on_copy()
        w._on_paste_text("6*7")
        check("paste", w.builder.expression == "6×7", w.builder.expression)
    except Exception:
        failures.append(traceback.format_exc())
    return False
app._app.connect("activate", lambda a: GLib.timeout_add(200, _flows))
GLib.timeout_add(2500, lambda: (app._app.quit(), False)[1])
code = app.run([])
print("FAILURES:" + str(len(failures)))
for item in failures:
    print(item)
sys.exit(1 if failures else 0)
"""


def test_broadway_flows():
    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    if shutil.which("dbus-daemon") is None:
        pytest.skip("dbus-daemon is missing")
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    workdir = Path(str(runtime) + f"/xx-calc-bwf-{os.getpid()}")
    workdir.mkdir(mode=0o700, parents=True, exist_ok=True)
    home = workdir / "home"
    home.mkdir(exist_ok=True)
    display = f":{50 + os.getpid() % 40}"
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
                    message="xx-calculator flows: broadway socket never appeared")
        driver = DRIVER.replace("SRC", repr(SRC)).replace("COMMON",
                                                          repr(COMMON))
        env = dict(os.environ, GDK_BACKEND="broadway",
                   BROADWAY_DISPLAY=display, GSK_RENDERER="cairo",
                   XDG_RUNTIME_DIR=str(workdir),
                   HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"),
                   XDG_DATA_HOME=str(home / ".local" / "share"),
                   DBUS_SESSION_BUS_ADDRESS=address,
                   PYTHONPATH=os.pathsep.join([SRC, COMMON]))
        proc = subprocess.run(
            [sys.executable, "-c", driver], capture_output=True, text=True,
            timeout=90, env=env)
        assert "FAILURES:0" in proc.stdout, proc.stdout + proc.stderr
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
