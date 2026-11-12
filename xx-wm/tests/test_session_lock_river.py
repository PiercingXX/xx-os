"""The lock survives a shell kill, on a real river.

A headless river (its own runtime dir, nothing shown on the host) and
session_lock.SessionLock in a child process: the child locks and is
killed with SIGKILL; a second client must then take over the session
that river kept locked (river logs the takeover) and unlock it. Skips
without river, the Gtk4SessionLock typelib or dbus-run-session.
"""
import os
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

HERE = Path(__file__).parent
CLIENT = HERE / 'session_lock_client.py'
PRELOAD = '/usr/lib/libgtk4-layer-shell.so.0'


def _requirements() -> str | None:
    for tool in ('river', 'dbus-run-session'):
        if shutil.which(tool) is None:
            return f'needs {tool}'
    if not os.path.exists(PRELOAD):
        return 'needs libgtk4-layer-shell'
    try:
        import gi
        gi.require_version('Gtk4SessionLock', '1.0')
    except (ImportError, ValueError):
        return 'needs the Gtk4SessionLock typelib'
    return None


pytestmark = pytest.mark.skipif(_requirements() is not None,
                                reason=_requirements() or '')


def _wait_line(proc, wanted: str, timeout: float = 15.0) -> list[str]:
    seen = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([proc.stdout], [], [], 0.2)
        if not ready:
            if proc.poll() is not None:
                break
            continue
        line = proc.stdout.readline()
        if not line:
            break
        seen.append(line.strip())
        if line.strip() == wanted:
            return seen
    raise AssertionError(f'no {wanted!r} from the lock client; saw {seen}')


@pytest.fixture
def river():
    runtime = tempfile.mkdtemp(prefix='xxlk', dir='/tmp')
    os.chmod(runtime, 0o700)
    log_path = Path(runtime) / 'river.log'
    env = {k: v for k, v in os.environ.items()
           if k not in ('WAYLAND_DISPLAY', 'DISPLAY', 'XXWM_SOCKET')}
    env.update(XDG_RUNTIME_DIR=runtime, WLR_BACKENDS='headless',
               WLR_HEADLESS_OUTPUTS='2', WLR_RENDERER='pixman',
               WLR_LIBINPUT_NO_DEVICES='1')
    with open(log_path, 'w') as log:
        proc = subprocess.Popen(
            ['river', '-no-xwayland', '-c', 'exec sleep 600'],
            env=env, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True)
    try:
        deadline = time.monotonic() + 10
        socket_name = None
        while time.monotonic() < deadline and socket_name is None:
            names = [n for n in os.listdir(runtime)
                     if n.startswith('wayland-') and not n.endswith('.lock')]
            socket_name = names[0] if names else None
            time.sleep(0.1)
        if socket_name is None:
            pytest.skip('river did not start: '
                        + log_path.read_text(errors='replace')[-400:])
        client_env = dict(env, WAYLAND_DISPLAY=socket_name,
                          LD_PRELOAD=PRELOAD, GTK_A11Y='none',
                          NO_AT_BRIDGE='1', GDK_BACKEND='wayland')
        client_env.pop('WLR_BACKENDS')
        yield client_env, log_path
    finally:
        os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
        shutil.rmtree(runtime, ignore_errors=True)


def _client(env, mode):
    return subprocess.Popen(
        ['dbus-run-session', '--', sys.executable, str(CLIENT), mode],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, start_new_session=True)


def test_lock_survives_a_kill_and_the_next_client_takes_over(river):
    env, log_path = river
    first = _client(env, 'hold')
    try:
        seen = _wait_line(first, 'locked')
        assert 'backend session-lock' in seen and 'ui placed' in seen
    finally:
        os.killpg(first.pid, signal.SIGKILL)
        first.wait(timeout=5)
    assert 'unlocked' not in seen

    second = _client(env, 'cycle')
    try:
        _wait_line(second, 'locked')
        _wait_line(second, 'unlocked')
    finally:
        if second.poll() is None:
            os.killpg(second.pid, signal.SIGKILL)
        second.wait(timeout=5)
    log = log_path.read_text(errors='replace')
    assert 'already locked session' in log, log[-800:]
