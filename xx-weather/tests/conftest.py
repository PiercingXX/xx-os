"""Shared test setup: the xx_weather package is importable headless."""
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "common"))


import pytest  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """Never read the developer's real HOME or config."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for key, sub in (("XDG_CONFIG_HOME", ".config"),
                     ("XDG_DATA_HOME", ".local/share"),
                     ("XDG_CACHE_HOME", ".cache")):
        path = home / sub
        path.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(key, str(path))
    monkeypatch.delenv("XX_WEATHER_SCENARIO", raising=False)
    monkeypatch.delenv("XX_WEATHER_PAGE", raising=False)
    monkeypatch.delenv("XX_WEATHER_OFFLINE", raising=False)


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


def make_store():
    from xx_weather.store import Store

    return Store()
