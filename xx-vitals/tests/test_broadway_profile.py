"""Broadway smoke test: the REAL window over a seeded database.

Spawns gtk4-broadwayd on a free display, then runs the REAL production shell
(tests/broadway_probe_app.py) in a FRESH interpreter against seeded rows with
Android-style millisecond ids, pumps the main loop for ~3 s, and fails on a
nonzero exit or any FAIL:/CRITICAL:/ERROR: line. A fresh interpreter is
deliberate: it mirrors a real launch and is immune to whatever the test
runner imported first. Skips cleanly when gtk4-broadwayd is missing.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("DBUS_SESSION_BUS_ADDRESS", "unix:path=/nonexistent-test-bus")

DRIVER = Path(__file__).parent / "broadway_probe_app.py"


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


def test_profile_window_pumps_without_exceptions(tmp_path, monkeypatch):
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
    assert "LABEL:NAS host: http://127.0.0.1:9" in output
    assert "LABEL:Sign-in: signed in" in output
    assert "LABEL:Queue: 2 queued" in output
