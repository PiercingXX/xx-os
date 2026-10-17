"""Broadway smoke test: the REAL window pumps without exceptions.

Spawns gtk4-broadwayd on a free display, then runs the REAL production
window (tests/broadway_probe_app.py) with the fake engine in a FRESH
interpreter, and fails on a nonzero exit or any FAIL:/CRITICAL:/ERROR:
line. Skips cleanly when gtk4-broadwayd is missing. Never kills daemons it
did not start.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

DRIVER = Path(__file__).parent / "broadway_probe_app.py"


def _wait_for_socket(run_dir: Path, proc: subprocess.Popen,
                     timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        if list(run_dir.glob("broadway*.socket")):
            return True
        time.sleep(0.1)
    return False


def test_shell_window_pumps_without_exceptions(tmp_path):
    broadwayd = shutil.which("gtk4-broadwayd")
    if broadwayd is None:
        pytest.skip("gtk4-broadwayd missing")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()

    daemon_env = dict(os.environ)
    daemon_env["XDG_RUNTIME_DIR"] = str(run_dir)
    proc = None
    display = None
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
        env["HOME"] = str(home)
        for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME",
                    "XDG_STATE_HOME"):
            env[var] = str(home / var.lower())
        env["XX_RADIO_ENGINE"] = "fake"
        repo_root = Path(__file__).parent.parent
        env["PYTHONPATH"] = os.pathsep.join([
            str(repo_root / "src"), str(repo_root.parent / "common"),
            env.get("PYTHONPATH", "")])
        completed = subprocess.run(
            [sys.executable, str(DRIVER)],
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
    assert "SIZE:360x720" in output
    assert "ENGINE:fake" in output
    for label in ("Home", "Search", "Library", "Settings", "HOME",
                  "SETTINGS", "QUICK PICKS", "Playback engine"):
        assert f"LABEL:{label}" in output
