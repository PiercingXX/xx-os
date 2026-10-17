"""The GUI's PlayerClient drives the real service over a private bus."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("dbus-run-session") is None,
    reason="dbus-run-session is missing")

DRIVER = Path(__file__).parent / "player_client_driver.py"


def test_client_methods_and_state_signal():
    env = dict(os.environ)
    env["SRC_DIR"] = str(Path(__file__).parent.parent / "src")
    env["XX_AUDIOBOOK_AUDIOSINK"] = "fakesink"
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(DRIVER)],
        capture_output=True, text=True, timeout=90, env=env)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    assert "PASS" in proc.stdout
