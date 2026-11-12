"""Real settings window under Broadway (skips cleanly without it).

Serves gtk4-broadwayd on a private display, then runs
settings_broadway_driver.py with the real SettingsWindow on a seeded
HOME: sections, switch persistence, engine/language changes, detail,
delete, the bare-HOME empty state, and a ~2 s main-loop pump.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("gi")

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
COMMON_DIR = ROOT.parent / "common"
DRIVER = Path(__file__).resolve().parent / "settings_broadway_driver.py"
SEED = ROOT / "tools" / "demo_seed.py"

pytestmark = pytest.mark.skipif(
    shutil.which("gtk4-broadwayd") is None,
    reason="gtk4-broadwayd not on PATH")


def _free_display() -> int:
    for candidate in range(17, 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", 8080 + candidate))
            except OSError:
                continue
            return candidate
    raise AssertionError("no free broadway display")


def test_real_settings_window_under_broadway(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    # The driver moves HOME, and without XDG_RUNTIME_DIR GTK looks for
    # the broadway socket under $HOME/.cache: share a private runtime dir.
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700)
    display = _free_display()
    daemon = subprocess.Popen(
        ["gtk4-broadwayd", f":{display}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, "XDG_RUNTIME_DIR": str(run_dir)})
    try:
        env = dict(os.environ)
        env["XDG_RUNTIME_DIR"] = str(run_dir)
        env["GDK_BACKEND"] = "broadway"
        env["BROADWAY_DISPLAY"] = f":{display}"
        env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get(
            "PYTHONPATH", "")
        env["COMMON_DIR"] = str(COMMON_DIR)
        for _ in range(100):
            with socket.socket(socket.AF_INET,
                               socket.SOCK_STREAM) as sock:
                sock.settimeout(0.1)
                try:
                    sock.connect(("127.0.0.1", 8080 + display))
                    break
                except OSError:
                    time.sleep(0.1)
        proc = subprocess.run(
            [sys.executable, str(DRIVER), str(SRC_DIR), str(home),
             str(SEED)],
            capture_output=True, text=True, timeout=120, cwd=str(ROOT),
            env=env)
        assert "SMOKE_OK" in proc.stdout, (
            f"rc={proc.returncode}\n--- stdout ---\n{proc.stdout}\n"
            f"--- stderr ---\n{proc.stderr}")
        assert proc.returncode == 0
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            daemon.kill()
