"""Start a private gtk4-broadwayd on a display nobody else holds.

Parallel runs (test suites, snap lanes) used to derive the display
from ``pid % 50``: two runs landed on the same number, the second
daemon could not bind its HTTP port (8080 + display) or socket and
exited, and GTK then failed with "couldn't be initialized" or the
socket never appeared. :func:`start` probes instead: a display is
free when its socket in the runtime dir is absent or dead and its
default HTTP port binds; the daemon gets its own free HTTP port too,
and a daemon that exits before its socket appears is retried on
another display.

Pure stdlib (no gi), so parent-side test code and ``scripts/snap.py``
can both use it.
"""

from __future__ import annotations

import os
import random
import shutil
import socket
import subprocess
import time

#: Display numbers to probe. Below 5 sits the usual X/Wayland range;
#: broadwayd's default HTTP port is 8080 + display.
FIRST_DISPLAY = 5
LAST_DISPLAY = 99

#: How long a started daemon gets to create its socket.
STARTUP_TIMEOUT_S = 15.0


def runtime_dir(env: dict | None = None) -> str:
    env = os.environ if env is None else env
    return env.get('XDG_RUNTIME_DIR') or f'/run/user/{os.getuid()}'


def socket_path(number: int, run_dir: str) -> str:
    """The Unix socket broadwayd creates for ``:number``."""
    return os.path.join(run_dir, f'broadway{number + 1}.socket')


def _port_free(port: int) -> bool:
    for family, address in ((socket.AF_INET, '0.0.0.0'),
                            (socket.AF_INET6, '::')):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((address, port))
        except OSError as error:
            if family == socket.AF_INET6 and error.errno in (
                    getattr(os, 'EAFNOSUPPORT', 97), 99):
                continue  # no IPv6 here: nothing to collide with
            return False
    return True


def free_tcp_port() -> int:
    """A TCP port the kernel just handed out (free a moment ago)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def _socket_live(path: str) -> bool:
    if not os.path.exists(path):
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1)
            sock.connect(path)
    except OSError:
        return False
    return True


def display_free(number: int, run_dir: str) -> bool:
    """True when ``:number`` has no live socket in ``run_dir`` and its
    default HTTP port (8080 + number) binds."""
    return (not _socket_live(socket_path(number, run_dir))
            and _port_free(8080 + number))


def start(run_dir: str | None = None, env: dict | None = None,
          first: int = FIRST_DISPLAY, last: int = LAST_DISPLAY,
          attempts: int = 8,
          timeout_s: float = STARTUP_TIMEOUT_S) -> tuple:
    """Start ``gtk4-broadwayd`` on a free display.

    ``run_dir`` is the XDG_RUNTIME_DIR the daemon (and its clients)
    use; None means the environment's. Returns ``(proc, ':N')`` once
    the daemon's socket exists. Raises ``RuntimeError`` when no
    attempt came up (``FileNotFoundError`` without the binary).
    """
    binary = shutil.which('gtk4-broadwayd')
    if binary is None:
        raise FileNotFoundError('gtk4-broadwayd')
    base_env = dict(os.environ if env is None else env)
    run_dir = run_dir or runtime_dir(base_env)
    base_env['XDG_RUNTIME_DIR'] = run_dir
    numbers = list(range(first, last + 1))
    random.shuffle(numbers)  # parallel starters rarely probe alike
    tried = 0
    for number in numbers:
        if tried >= attempts:
            break
        if not display_free(number, run_dir):
            continue
        tried += 1
        path = socket_path(number, run_dir)
        try:
            os.unlink(path)  # a dead socket left by a killed daemon
        except OSError:
            pass
        proc = subprocess.Popen(
            [binary, '--port', str(free_tcp_port()), f':{number}'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=base_env)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break  # lost a race for the display: try another
            if os.path.exists(path):
                return proc, f':{number}'
            time.sleep(0.05)
        stop(proc)
    raise RuntimeError(
        f'no broadway display came up after {tried} attempts')


def stop(proc) -> None:
    """Terminate a daemon from :func:`start` (kill if it lingers)."""
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
