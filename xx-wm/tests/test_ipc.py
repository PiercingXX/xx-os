"""IPC server hardening: runtime-dir auto-creation, socket 0600, bounded
reads (silent client cannot stall the loop), and handler-error isolation.
The GLib watch callback is driven manually against a fake GLib, so no main
loop and no real PyGObject are needed."""
import logging
import os
import socket
import stat
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


class _FakeGLib:
    IOCondition = types.SimpleNamespace(IN=1, HUP=16, ERR=8)

    def __init__(self):
        self.pending_idles = []
        self.live_sources = set()
        self.removed_sources = []
        self.callbacks = {}
        self._next_source_id = 1

    def _add(self, entry):
        source_id = self._next_source_id
        self._next_source_id += 1
        self.live_sources.add(source_id)
        self.callbacks[source_id] = entry
        return source_id

    def io_add_watch(self, fd, cond, cb, *args):
        return self._add(('io', fd, cb, args))

    def timeout_add(self, ms, cb, *args):
        return self._add(('timeout', ms, cb, args))

    def fire(self, source_id):
        """Run a watch/timer callback; a False return removes it, like GLib."""
        _kind, first, cb, args = self.callbacks[source_id]
        keep = cb(first, None, *args) if _kind == 'io' else cb(*args)
        if not keep:
            self.live_sources.discard(source_id)
            self.callbacks.pop(source_id, None)
        return keep

    def sources(self, kind):
        return [sid for sid, entry in self.callbacks.items()
                if entry[0] == kind and sid in self.live_sources]

    def idle_add(self, cb, *args):
        self.pending_idles.append((cb, args))
        return 1

    def source_remove(self, source_id):
        assert source_id in self.live_sources, 'removing a dead source'
        self.live_sources.discard(source_id)
        self.callbacks.pop(source_id, None)
        self.removed_sources.append(source_id)

    def drain_idles(self):
        while self.pending_idles:
            cb, args = self.pending_idles.pop(0)
            cb(*args)


_GLIB = _FakeGLib()


def _install_fake_gi():
    glib_mod = types.ModuleType('gi.repository.GLib')
    glib_mod.IOCondition = _GLIB.IOCondition
    glib_mod.io_add_watch = _GLIB.io_add_watch
    glib_mod.timeout_add = _GLIB.timeout_add
    glib_mod.idle_add = _GLIB.idle_add
    glib_mod.source_remove = _GLIB.source_remove

    gi_mod = types.ModuleType('gi')
    gi_mod.require_version = lambda *_a, **_k: None
    gi_rep = types.ModuleType('gi.repository')
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib_mod


_install_fake_gi()

import ipc  # noqa: E402  (needs fake gi modules installed first)


@pytest.fixture(autouse=True)
def _glib(monkeypatch):
    _GLIB.pending_idles.clear()
    _GLIB.live_sources.clear()
    _GLIB.removed_sources.clear()
    _GLIB.callbacks.clear()
    # Pin the seam: whichever fake gi won collection, ipc talks to _GLIB.
    monkeypatch.setattr(ipc, 'GLib', _GLIB)
    return _GLIB


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    target = tmp_path / 'runtime'
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(target))
    return target


@pytest.fixture
def make_server(runtime):
    created = []

    def _make(handler):
        srv = ipc.IPCServer(handler)
        created.append(srv)
        return srv

    yield _make
    for srv in created:
        srv.stop()


def _connect_send(path, payload=None):
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    conn.connect(str(path))
    if payload is not None:
        conn.sendall(payload)
    return conn


def _pump(server):
    assert server._on_incoming(server._sock.fileno(), None, server._sock) is True
    _GLIB.drain_idles()


class TestSocketSetup:
    def test_missing_runtime_dir_created_before_bind(self, make_server, runtime):
        assert not runtime.exists()
        srv = make_server(lambda cmd: None)
        try:
            assert runtime.is_dir()
            assert (runtime / 'xx-wm.sock').exists()
        finally:
            srv.stop()

    def test_fallback_dir_created_when_xdg_unset(self, monkeypatch, tmp_path, make_server):
        monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        expected = tmp_path / 'home' / '.local' / 'share' / 'xx-wm' / 'xx-wm.sock'
        srv = make_server(lambda cmd: None)
        try:
            assert expected.exists()
        finally:
            srv.stop()

    def test_socket_mode_is_0600(self, make_server, runtime):
        make_server(lambda cmd: None)
        mode = stat.S_IMODE(os.stat(runtime / 'xx-wm.sock').st_mode)
        assert mode == 0o600


class TestSocketPathResolution:
    def test_unset_runtime_dir_uses_home_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        expected = tmp_path / 'home' / '.local' / 'share' / 'xx-wm' / 'xx-wm.sock'
        assert ipc._socket_path() == str(expected)

    def test_empty_runtime_dir_uses_home_fallback(self, monkeypatch, tmp_path):
        monkeypatch.setenv('XDG_RUNTIME_DIR', '')
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        expected = tmp_path / 'home' / '.local' / 'share' / 'xx-wm' / 'xx-wm.sock'
        assert ipc._socket_path() == str(expected)

    def test_client_template_shares_resolver(self):
        template = (Path(__file__).parent.parent / 'launcher' / 'data'
                    / 'xx-wm-ipc.in').read_text(encoding='utf-8')
        assert 'XDG_RUNTIME_DIR' in template
        assert os.path.join('.local', 'share', 'xx-wm') in template
        assert 'xx-wm.sock' in template

    def test_client_template_is_cheap_and_loud(self):
        template = (Path(__file__).parent.parent / 'launcher' / 'data'
                    / 'xx-wm-ipc.in').read_text(encoding='utf-8')
        assert '-S' in template and '-E' in template
        assert 'SystemExit(1)' in template or 'exit 1' in template


def _client_python_body():
    template = (Path(__file__).parent.parent / 'launcher' / 'data'
                / 'xx-wm-ipc.in').read_text(encoding='utf-8')
    start = template.index("<<'PYEOF'") + len("<<'PYEOF'")
    end = template.rindex('PYEOF')
    return template[start:end]


class TestIpcClient:
    def test_client_delivers_to_live_server(self, make_server, runtime, tmp_path):
        import subprocess
        handled = []
        server = make_server(handled.append)
        body_file = tmp_path / 'ipc_body.py'
        body_file.write_text(_client_python_body(), encoding='utf-8')
        env = dict(os.environ, XDG_RUNTIME_DIR=str(runtime))
        proc = subprocess.run(
            [sys.executable, '-S', '-E', str(body_file), 'lock'],
            capture_output=True, text=True, env=env, timeout=30)
        assert proc.returncode == 0, proc.stderr
        _pump(server)
        assert handled == ['lock']

    def test_client_exits_nonzero_when_unreachable(self, tmp_path):
        import subprocess
        body_file = tmp_path / 'ipc_body.py'
        body_file.write_text(_client_python_body(), encoding='utf-8')
        env = dict(os.environ, XDG_RUNTIME_DIR=str(tmp_path / 'no-such-runtime'))
        proc = subprocess.run(
            [sys.executable, '-S', '-E', str(body_file), 'lock'],
            capture_output=True, text=True, env=env, timeout=30)
        assert proc.returncode != 0

    def test_client_exits_nonzero_without_command(self, tmp_path):
        import subprocess
        body_file = tmp_path / 'ipc_body.py'
        body_file.write_text(_client_python_body(), encoding='utf-8')
        env = dict(os.environ)
        proc = subprocess.run(
            [sys.executable, '-S', '-E', str(body_file)],
            capture_output=True, text=True, env=env, timeout=30)
        assert proc.returncode != 0


class TestSecondInstance:
    def test_second_server_does_not_steal_socket(
        self, make_server, runtime, caplog,
    ):
        first_handled = []
        second_handled = []
        first = make_server(first_handled.append)
        assert ipc._socket_is_live(ipc._socket_path())
        with caplog.at_level(logging.ERROR, logger='piercing.ipc'):
            second = make_server(second_handled.append)
        assert second._sock is None
        client = _connect_send(runtime / 'xx-wm.sock', b'lock\n')
        client.close()
        # Two liveness probes (the assert above and the second server's
        # own start check) queued silent connections ahead of the real
        # one; the first pumps absorb them, the last delivers.
        for _ in range(2):
            assert first._on_incoming(first._sock.fileno(), None, first._sock) is True
            _GLIB.drain_idles()
        assert first_handled == []
        assert first._on_incoming(first._sock.fileno(), None, first._sock) is True
        _GLIB.drain_idles()
        assert first_handled == ['lock']
        assert second_handled == []

    def test_bind_succeeds_after_first_stops(self, runtime):
        first = ipc.IPCServer(lambda cmd: None)
        try:
            assert ipc._socket_is_live(ipc._socket_path())
        finally:
            first.stop()
        second = ipc.IPCServer(lambda cmd: None)
        try:
            assert second._sock is not None
        finally:
            second.stop()


class TestWatchLifecycle:
    def test_stop_removes_io_watch_source(self, make_server, runtime):
        server = make_server(lambda cmd: None)
        assert _GLIB.live_sources
        server.stop()
        assert not _GLIB.live_sources

    def test_stop_is_idempotent_source_removed_once(self, make_server, runtime):
        server = make_server(lambda cmd: None)
        server.stop()
        server.stop()
        assert len(_GLIB.removed_sources) == 1


class TestDispatchLoop:
    def test_silent_client_does_not_hang_server(self, make_server, runtime):
        handled = []
        server = make_server(handled.append)

        silent = _connect_send(runtime / 'xx-wm.sock')
        try:
            started = time.monotonic()
            assert server._on_incoming(server._sock.fileno(), None, server._sock) is True
            elapsed = time.monotonic() - started
            # The old blocking recv waited out its 250 ms timeout here.
            assert elapsed < 0.05
        finally:
            silent.close()
        _GLIB.drain_idles()
        assert handled == []
        # The closed silent client finishes through its watch.
        for sid in _GLIB.sources('io')[1:]:
            _GLIB.fire(sid)
        _GLIB.drain_idles()
        assert handled == []

        client = _connect_send(runtime / 'xx-wm.sock', b'lock\n')
        client.close()
        _pump(server)
        assert handled == ['lock']

    def test_handler_exception_logged_and_server_survives(
        self, make_server, runtime, caplog,
    ):
        handled = []

        def handler(command):
            if command == 'boom':
                raise RuntimeError('kaboom')
            handled.append(command)

        server = make_server(handler)

        client = _connect_send(runtime / 'xx-wm.sock', b'boom\n')
        client.close()
        with caplog.at_level(logging.ERROR, logger='piercing.ipc'):
            _pump(server)
        assert handled == []
        assert any(
            record.levelname == 'ERROR' and 'boom' in record.getMessage()
            for record in caplog.records
        )

        client = _connect_send(runtime / 'xx-wm.sock', b'welcome\n')
        client.close()
        _pump(server)
        assert handled == ['welcome']


class TestNonBlockingReads:
    def test_late_line_arrives_through_the_conn_watch(self, make_server,
                                                      runtime):
        handled = []
        server = make_server(handled.append)
        client = _connect_send(runtime / 'xx-wm.sock')
        assert server._on_incoming(server._sock.fileno(), None,
                                   server._sock) is True
        watches = _GLIB.sources('io')[1:]
        assert len(watches) == 1
        assert _GLIB.sources('timeout')
        client.sendall(b'sha')
        assert _GLIB.fire(watches[0]) is True      # half a line: keep going
        client.sendall(b'de.show\n')
        assert _GLIB.fire(watches[0]) is False     # done: watch removed
        client.close()
        _GLIB.drain_idles()
        assert handled == ['shade.show']
        assert not _GLIB.sources('timeout')
        assert server._conns == {}

    def test_partial_line_read_at_accept_is_kept(self, make_server, runtime):
        handled = []
        server = make_server(handled.append)
        client = _connect_send(runtime / 'xx-wm.sock', b'pag')
        server._on_incoming(server._sock.fileno(), None, server._sock)
        (watch,) = _GLIB.sources('io')[1:]
        client.sendall(b'e.home\n')
        assert _GLIB.fire(watch) is False
        client.close()
        _GLIB.drain_idles()
        assert handled == ['page.home']

    def test_stalled_client_is_dropped_on_timeout(self, make_server, runtime):
        handled = []
        server = make_server(handled.append)
        client = _connect_send(runtime / 'xx-wm.sock', b'lo')
        try:
            server._on_incoming(server._sock.fileno(), None, server._sock)
            (timer,) = _GLIB.sources('timeout')
            assert _GLIB.fire(timer) is False
            assert server._conns == {}
            assert _GLIB.sources('io') == [server._watch_id]
        finally:
            client.close()
        _GLIB.drain_idles()
        assert handled == []

    def test_stop_closes_pending_connections(self, make_server, runtime):
        server = make_server(lambda cmd: None)
        client = _connect_send(runtime / 'xx-wm.sock')
        try:
            server._on_incoming(server._sock.fileno(), None, server._sock)
            assert server._conns
            server.stop()
            assert server._conns == {}
            assert not _GLIB.live_sources
        finally:
            client.close()

    def test_only_the_first_line_is_the_command(self, make_server, runtime):
        handled = []
        server = make_server(handled.append)
        client = _connect_send(runtime / 'xx-wm.sock', b'lock\nwelcome\n')
        client.close()
        _pump(server)
        assert handled == ['lock']


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
