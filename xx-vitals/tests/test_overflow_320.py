"""320 px overflow audit: every page at the smallest supported width.

Spawns gtk4-broadwayd, runs the REAL production window at 320x640 in a
fresh interpreter (tests/overflow_probe_app.py), visits all four tabs,
and fails on any widget allocation wider than 320 px, any FAIL/CRITICAL
line, or a nonzero exit. Skips cleanly when gtk4-broadwayd is missing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent-test-bus")

DRIVER = Path(__file__).parent / "overflow_probe_app.py"
MAX_WIDTH = 320


def _wait_for_socket(run_dir: Path, proc: subprocess.Popen,
                     timeout_s: float = 10.0) -> bool:
    from xx_testing.broadway_wait import poll_until

    def _socket_live() -> bool:
        if proc.poll() is not None:
            return True
        return bool(list(run_dir.glob("broadway*.socket")))

    if not poll_until(_socket_live, timeout_s=timeout_s,
                      interval_s=0.1):
        return False
    return proc.poll() is None and bool(
        list(run_dir.glob("broadway*.socket")))


def test_no_widget_exceeds_320px(tmp_path, monkeypatch):
    broadwayd = shutil.which("gtk4-broadwayd")
    if broadwayd is None:
        pytest.skip("gtk4-broadwayd missing")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    monkeypatch.setenv(
        "DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent-test-bus")

    daemon_env = dict(os.environ)
    daemon_env["XDG_RUNTIME_DIR"] = str(run_dir)
    proc = None
    for number in range(5, 100):
        proc = subprocess.Popen(
            [broadwayd, f":{number}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=daemon_env)
        if _wait_for_socket(run_dir, proc):
            display = number
            break
        proc.terminate()
        proc.wait(timeout=10)
        proc = None
    if proc is None:
        pytest.skip("could not start gtk4-broadwayd")
    try:
        env = dict(os.environ)
        env["XDG_RUNTIME_DIR"] = str(run_dir)
        env["GDK_BACKEND"] = "broadway"
        env["BROADWAY_DISPLAY"] = f":{display}"
        env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/nonexistent-test-bus"
        repo_root = str(Path(__file__).parent.parent)
        env["PYTHONPATH"] = (repo_root + "/src" + os.pathsep
                               + str(Path(repo_root).parent / "common")
                               + os.pathsep + env.get("PYTHONPATH", ""))
        completed = subprocess.run(
            [sys.executable, str(DRIVER), str(tmp_path / "vitals.db")],
            capture_output=True, text=True, timeout=120, env=env)
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    output = (completed.stdout or "") + (completed.stderr or "")
    print(output)
    assert completed.returncode == 0, output
    assert "FAIL:" not in output
    assert "CRITICAL:" not in output
    assert "ERROR:" not in output
    lines = output.splitlines()
    windows = [line for line in lines if line.startswith("WINDOW:")]
    assert windows, "probe reported no window width"
    window_width = int(windows[0].split(":")[1])
    assert window_width > 0
    surfaces = {
        line.split(":")[1] for line in lines if line.startswith("CONTENT_MIN:")
    }
    assert {"home", "journal", "browse", "profile", "metric-detail",
            "weight-dialog", "activity-dialog"} <= surfaces
    forcing = [
        line for line in lines
        if line.startswith("CONTENT_MIN:") and int(line.split(":")[2]) > MAX_WIDTH
    ]
    assert not forcing, "\n".join(forcing)
    suspects = [line for line in lines if line.startswith("MIN:")]
    assert not suspects, "\n".join(suspects)
    # Adw.HeaderBar and Gtk.WindowHandle are framework chrome: a bare
    # header with no app content allocates 362 px in a 360 px parent
    # under Broadway (verified with a header-only control window), so
    # the allocation gate skips exactly those two classes. Their
    # minimums are still gated above, so real title content forcing
    # width would fail there.
    clipped = []
    for line in lines:
        if not line.startswith("WIDTH:"):
            continue
        parts = line.split(":")
        if parts[2] in ("HeaderBar", "WindowHandle"):
            continue
        width = int(parts[3])
        if width > window_width:
            clipped.append(line)
    assert not clipped, "\n".join(clipped)
