"""Broadway UI test: the REAL window, offline, on a seeded cache.

Seeds a temp HOME with tools/demo_seed.py, spawns gtk4-broadwayd on a free
display, then runs the REAL window (tests/broadway_probe_app.py) in a
FRESH interpreter with no API base, no keyring and no player service: it
walks Library, Book, a damaged Book, Series, Collections and Now Playing
from the cache alone and checks labels + 48 px targets on icon controls,
at text scale 1 and 2. Fails on a nonzero exit or any FAIL:/CRITICAL:/
ERROR: line. Skips cleanly when gtk4-broadwayd is missing. Never kills
daemons it did not start.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent-test-bus")

DRIVER = Path(__file__).parent / "broadway_probe_app.py"
SEED = Path(__file__).parent.parent / "tools" / "demo_seed.py"


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


def _start_broadway(tmp_path: Path):
    broadwayd = shutil.which("gtk4-broadwayd")
    if broadwayd is None:
        pytest.skip("gtk4-broadwayd missing")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    daemon_env = dict(os.environ)
    daemon_env["XDG_RUNTIME_DIR"] = str(run_dir)
    for number in range(5, 100):
        proc = subprocess.Popen(
            [broadwayd, f":{number}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=daemon_env)
        if _wait_for_socket(run_dir, proc):
            return proc, run_dir, number
        proc.terminate()
        proc.wait(timeout=10)
    pytest.skip("could not start gtk4-broadwayd")


def _probe(tmp_path: Path, scale: int) -> str:
    home = tmp_path / "home"
    (home / "config" / "xx-wm").mkdir(parents=True)
    (home / "config" / "xx-wm" / "config.json").write_text(
        '{"theme": "amoled", "text_size_scale": ' + str(scale) + "}")
    repo_root = Path(__file__).parent.parent
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / "config")
    env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/nonexistent-test-bus"
    env["PYTHONPATH"] = (str(repo_root / "src") + os.pathsep
                         + str(repo_root.parent / "common") + os.pathsep
                         + env.get("PYTHONPATH", ""))
    seeded = subprocess.run([sys.executable, str(SEED), str(home)],
                            capture_output=True, text=True, timeout=60,
                            env=env)
    assert seeded.returncode == 0, seeded.stderr
    proc, run_dir, display = _start_broadway(tmp_path)
    try:
        env["XDG_RUNTIME_DIR"] = str(run_dir)
        env["GDK_BACKEND"] = "broadway"
        env["BROADWAY_DISPLAY"] = f":{display}"
        completed = subprocess.run(
            [sys.executable, str(DRIVER)],
            capture_output=True, text=True, timeout=120, env=env)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    output = (completed.stdout or "") + (completed.stderr or "")
    print(output)
    assert completed.returncode == 0, output
    for marker in ("FAIL:", "CRITICAL:", "ERROR:"):
        assert marker not in output
    return output


def test_offline_window_walks_every_screen_from_cache(tmp_path):
    output = _probe(tmp_path, 1)
    assert "SIZE:360x720" in output


def test_text_scale_two_still_fits_with_labelled_targets(tmp_path):
    output = _probe(tmp_path, 2)
    assert "WIDTH:360" in output
