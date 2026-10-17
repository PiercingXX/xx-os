"""Keyring tests: the Secret Service probe fails fast on a bus without one,
and the memory ladder still works. Service tests run on a private bus,
never the host's."""
import shutil
import subprocess
import time

import pytest

from xx_auth import keystore
from xx_auth.keystore import MemoryKeyring, SecretServiceKeyring


@pytest.fixture(scope="module")
def private_bus(tmp_path_factory):
    if shutil.which("dbus-daemon") is None:
        pytest.skip("dbus-daemon is missing")
    runtime = tmp_path_factory.mktemp("dbus-runtime")
    proc = subprocess.Popen(
        ["dbus-daemon", "--session",
         "--address=unix:path=" + str(runtime / "bus.sock"),
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


def test_no_service_fails_fast(private_bus, monkeypatch):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", private_bus)
    start = time.monotonic()
    assert keystore.service_present() is False
    assert time.monotonic() - start < 10
    keyring = SecretServiceKeyring()
    assert keyring.available() is False
    with pytest.raises(ValueError):
        keyring.load_key()


def test_missing_bus_is_not_a_service(monkeypatch, tmp_path):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=" + str(tmp_path / "nope.sock"))
    assert keystore.service_present() is False


def test_memory_keyring_round_trip():
    keyring = MemoryKeyring()
    assert keyring.available() is True
    key = keystore.load_or_create_key(keyring)
    assert keystore.load_or_create_key(keyring) == key
