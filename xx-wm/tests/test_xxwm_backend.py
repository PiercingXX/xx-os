"""The xxwm backend against a real Unix socket standing in for xxwm."""
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import xxwm_backend  # noqa: E402
from toplevel_manager import ToplevelManager  # noqa: E402


class FakeXxwm:
    """Accepts connections; `subscribe` gets the queued snapshots, any
    other line is recorded and answered `ok`."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.requests: list[str] = []
        self.snapshots: list[list] = []
        self.subscribed = threading.Event()
        self._sub: socket.socket | None = None
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(path)
        self._srv.listen(8)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            line = conn.makefile('r').readline().strip()
            if line == 'subscribe':
                self._sub = conn
                self.subscribed.set()
                continue
            self.requests.append(line)
            conn.sendall(b'ok\n')
            conn.close()

    def push(self, items: list) -> None:
        assert self._sub is not None
        self._sub.sendall(json.dumps(items).encode() + b'\n')

    def close(self) -> None:
        self._srv.close()
        if self._sub is not None:
            self._sub.close()


def _wait(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _win(ident, app, title='', focused=False):
    return {'id': ident, 'app_id': app, 'title': title, 'workspace': 1,
            'focused': focused, 'visible': True, 'floating': False}


def test_socket_path_follows_xxwm(monkeypatch):
    monkeypatch.delenv('XXWM_SOCKET', raising=False)
    monkeypatch.setenv('XDG_RUNTIME_DIR', '/run/user/7')
    monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-2')
    assert xxwm_backend.socket_path() == '/run/user/7/xxwm-wayland-2.sock'
    monkeypatch.setenv('XXWM_SOCKET', '/tmp/x.sock')
    assert xxwm_backend.socket_path() == '/tmp/x.sock'


def test_snapshot_diff_emits_adds_changes_removals_and_focus():
    backend = xxwm_backend.XxwmBackend(path='/nonexistent')
    events, states = [], []
    backend._manager_cb = lambda h, a, t: events.append((h, a, t))
    backend.connect_state(lambda h, on: states.append((h, on)))
    backend.apply_snapshot([_win('a', 'kitty', '~', True), _win('b', 'foot')])
    assert events == [('a', 'kitty', '~'), ('b', 'foot', '')]
    assert states == [('a', True)]
    events.clear(), states.clear()
    backend.apply_snapshot([_win('b', 'foot', 'vim', True)])
    assert events == [('b', 'foot', 'vim'), ('a', None, None)]
    assert states == [('b', True)]
    events.clear()
    backend.apply_snapshot([_win('b', 'foot', 'vim', True)])
    assert events == []


def test_garbage_items_are_ignored():
    backend = xxwm_backend.XxwmBackend(path='/nonexistent')
    events = []
    backend._manager_cb = lambda h, a, t: events.append(h)
    backend.apply_snapshot([None, 3, {'id': ''}, {'id': 7}, _win('ok', 'x')])
    assert events == ['ok']


def test_manager_lists_windows_and_routes_activate_and_close(tmp_path):
    path = str(tmp_path / 'xxwm.sock')
    fake = FakeXxwm(path)
    manager = ToplevelManager(backend=xxwm_backend.XxwmBackend(path=path))
    try:
        assert manager.available
        assert fake.subscribed.wait(3)
        fake.push([_win('id1', 'kitty', 'one'), _win('id2', 'org.gnome.Calculator', 'calc', True)])
        assert _wait(lambda: len(manager.list()) == 2)
        assert manager.activated_app_id() == 'org.gnome.Calculator'
        manager.activate('id1')
        manager.close('id2')
        assert _wait(lambda: sorted(fake.requests) == ['close id2', 'focus id1'])
        fake.push([_win('id1', 'kitty', 'one', True)])
        assert _wait(lambda: [t.handle for t in manager.list()] == ['id1'])
        assert _wait(lambda: manager.activated_app_id() == 'kitty')
    finally:
        manager._backend.shutdown()
        fake.close()


def test_the_shell_itself_is_filtered(tmp_path):
    path = str(tmp_path / 'xxwm.sock')
    fake = FakeXxwm(path)
    manager = ToplevelManager(backend=xxwm_backend.XxwmBackend(path=path))
    try:
        assert fake.subscribed.wait(3)
        fake.push([_win('s', 'io.piercingxx.XXWM'), _win('k', 'kitty')])
        assert _wait(lambda: [t.app_id for t in manager.list()] == ['kitty'])
    finally:
        manager._backend.shutdown()
        fake.close()


def test_default_backend_prefers_xxwm_when_its_socket_exists(tmp_path, monkeypatch):
    import toplevel_manager
    path = tmp_path / 'xxwm.sock'
    monkeypatch.setenv('XXWM_SOCKET', str(path))
    assert not isinstance(toplevel_manager._make_default_backend(), xxwm_backend.XxwmBackend)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(path))
    try:
        backend = toplevel_manager._make_default_backend()
        assert isinstance(backend, xxwm_backend.XxwmBackend)
    finally:
        srv.close()
        os.unlink(path)
