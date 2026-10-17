"""Pick-window Broadway smoke test: the real GUI half of Pick.

Builds the real ``ContactsApp`` pick-mode window (what the headless
service launches as ``xx-contacts --pick``) against a seeded temp
database with a stubbed ``reply_pick_async``, activates the first row
through the real ``ContactPickerPage``, and asserts the captured
reply plus window close. Runs against ``gtk4-broadwayd`` in a
subprocess so this suite's own process never pins the backend first.
"""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

_DRIVER = str(Path(__file__).parent / "broadway_pick_driver.py")


def _socket_path(display: str) -> str:
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return os.path.join(runtime, f"broadway{int(display[1:]) + 1}.socket")


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _display_live(display: str) -> bool:
    path = _socket_path(display)
    if not os.path.exists(path):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(2)
            sock.connect(path)
    except OSError:
        return False
    return True


@pytest.fixture(scope="module")
def broadway_env():
    import random

    if shutil.which("gtk4-broadwayd") is None:
        pytest.skip("gtk4-broadwayd is missing")
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        os.makedirs(runtime, mode=0o700, exist_ok=True)
    except OSError:
        pytest.skip("no writable runtime dir for broadway")
    numbers = list(range(41, 80))
    random.shuffle(numbers)
    for number in numbers:
        display = f":{number}"
        if _display_live(display):
            continue
        try:
            os.unlink(_socket_path(display))
        except OSError:
            pass
        proc = subprocess.Popen(
            ["gtk4-broadwayd", "--port", str(_free_tcp_port()), display],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(200):
            if os.path.exists(_socket_path(display)):
                break
            time.sleep(0.05)
        else:
            proc.terminate()
            continue
        env = dict(os.environ)
        env["GDK_BACKEND"] = "broadway"
        env["BROADWAY_DISPLAY"] = display
        try:
            yield env
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()
        return
    pytest.skip("no free broadway display")


def test_pick_window_reports_choice_and_closes(broadway_env, tmp_path):
    base = str(tmp_path / "pickseed")
    os.makedirs(base, exist_ok=True)
    from broadway_run import run_driver

    completed = run_driver(
        [sys.executable, _DRIVER, base],
        env=broadway_env, timeout=120,
    )
    assert "Traceback" not in (completed.stderr or ""), (
        completed.stderr[-3000:])
    assert "CRITICAL" not in (completed.stderr or ""), (
        completed.stderr[-3000:])
    assert completed.returncode == 0, (
        f"driver failed: {(completed.stderr or '')[-3000:]}"
        f"{(completed.stdout or '')[-3000:]}")
    markers = {}
    for line in (completed.stdout or "").splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            markers[key.strip()] = value.strip()
    assert markers.get("PAGES_OK") == "True", markers
    assert markers.get("EXCEPTIONS") == "0", markers
    assert markers.get("WINDOW") == "True", markers
    assert markers.get("PAGE") == "ContactPickerPage", markers
    assert markers.get("ROWS") == "1", markers
    assert "+1 555 0100" in markers.get("REPLY", ""), markers
    assert "broadway-token" in markers.get("REPLY", ""), markers
    assert markers.get("CLOSED") == "True", markers
