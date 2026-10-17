"""Shared test setup: the xx_calculator package is importable headless."""
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))


import pytest  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_xdg_config(tmp_path_factory, monkeypatch):
    """Never read the developer's real ~/.config/xx-wm theme."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path_factory.mktemp("xdg")))


@pytest.fixture(scope="module")
def private_bus(tmp_path_factory):
    """Private session bus (never the host bus). Kills only its daemon."""
    if shutil.which("dbus-daemon") is None:
        pytest.skip("dbus-daemon is missing")
    runtime = tmp_path_factory.mktemp("dbus-runtime")
    socket_path = str(runtime / "private-bus.sock")
    proc = subprocess.Popen(
        ["dbus-daemon", "--session",
         "--address=unix:path=" + socket_path,
         "--print-address", "--nofork"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    try:
        address = proc.stdout.readline().strip()
        if not address or proc.poll() is not None:
            raise RuntimeError("dbus-daemon exited before printing")
        yield address
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
