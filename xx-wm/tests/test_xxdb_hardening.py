"""xxdb hardening: pairing without the code on the wire, main-loop hops,
pre-auth limits, off means off, lock and trust rules, per-session
streams, PTY lifecycle. Loopback sockets and temp certs only.
"""
import json
import re
import shutil
import socket
import ssl
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import xxdb
import xxdb_controller
import xxdb_daemon
from test_xxdb_e2e import _make_cert
from test_xxdb_rpc import _fake_config, _load_host_cli
from xxdb_daemon import XxdbDaemon

REPO = Path(__file__).parent.parent

needs_openssl = pytest.mark.skipif(shutil.which('openssl') is None,
                                   reason='needs openssl for certificates')


class _Conn:
    def __init__(self, port, key_path=None, cert_path=None, timeout=10):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        if cert_path is not None:
            context.load_cert_chain(str(cert_path), str(key_path))
        raw = socket.create_connection(('127.0.0.1', port), timeout=timeout)
        self.sock = context.wrap_socket(raw, server_hostname='127.0.0.1')
        self.sock.settimeout(timeout)
        self._buf = b''

    def send(self, payload: dict) -> None:
        self.sock.sendall((json.dumps(payload) + '\n').encode())

    def read(self):
        """Next message; None once the phone closed the connection."""
        while b'\n' not in self._buf:
            try:
                chunk = self.sock.recv(65536)
            except (ConnectionError, ssl.SSLError):
                return None
            if not chunk:
                return None
            self._buf += chunk
        line, self._buf = self._buf.split(b'\n', 1)
        return json.loads(line)

    def request(self, payload: dict):
        self.send(payload)
        return self.read()

    def read_until(self, pred, timeout=10.0):
        """(matched, messages) reading until pred(message) holds."""
        seen = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.sock.settimeout(max(0.05, deadline - time.monotonic()))
            try:
                message = self.read()
            except (TimeoutError, socket.timeout):
                break
            if message is None:
                break
            seen.append(message)
            if pred(message):
                return True, seen
        return False, seen

    def closed_within(self, timeout: float) -> bool:
        self.sock.settimeout(timeout)
        try:
            while True:
                if self.read() is None:
                    return True
        except (TimeoutError, socket.timeout):
            return False

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _alive(pid: int) -> bool:
    try:
        stat = Path(f'/proc/{pid}/stat').read_text()
    except OSError:
        return False
    return stat.rsplit(')', 1)[1].split()[0] != 'Z'


def _wait_for(pred, timeout=5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


@pytest.fixture(scope='module')
def host_cert(tmp_path_factory):
    if shutil.which('openssl') is None:
        pytest.skip('needs openssl for certificates')
    return _make_cert(tmp_path_factory.mktemp('hostcert'), 'host')


@pytest.fixture
def make_daemon(tmp_path, monkeypatch):
    monkeypatch.setattr(xxdb, 'interface_entries',
                        lambda: [('lo', False, ['127.0.0.1'], True)])
    monkeypatch.setenv('SHELL', '/bin/sh')
    started = []

    def _make(**overrides):
        home = tmp_path / 'home'
        home.mkdir(exist_ok=True)
        kwargs = dict(
            cert_dir=tmp_path / 'phone-certs',
            trust_store=xxdb.TrustStore(path=tmp_path / 'trusted.json'),
            allow_loopback=True,
            screenshot_provider=lambda: b'png',
            shell_log_path=tmp_path / 'shell.log',
            ipc_forwarder=lambda verb: None,
            input_injector=lambda action, params: (True, 'ok'),
            sudo_check=lambda: False,
            shell_restarter=lambda: 999,
        )
        kwargs.update(overrides)
        daemon = XxdbDaemon(**kwargs)
        daemon._home = str(home)
        _addrs, port = daemon.start()
        started.append(daemon)
        return daemon, port

    yield _make
    for daemon in started:
        daemon.stop()


def _trust(daemon, host_cert) -> str:
    _key, _cert, pem, fingerprint, _der = host_cert
    daemon.trust.add(fingerprint, 'laptop', pem, now=time.time())
    daemon._trust_host_cert(pem)
    return fingerprint


def _trusted_conn(daemon, port, host_cert) -> _Conn:
    _trust(daemon, host_cert)
    return _Conn(port, host_cert[0], host_cert[1])


def _shell_pid(conn, request_id) -> int:
    conn.send({'id': request_id, 'cmd': 'shell', 'input': 'echo PID=$$\n'})
    ok, seen = conn.read_until(
        lambda m: re.search(r'PID=\d+', m.get('data', '')) is not None)
    assert ok, seen
    return int(re.search(r'PID=(\d+)', seen[-1]['data']).group(1))


# --- main-loop hop, connection bookkeeping ---------------------------------

def test_connection_callbacks_hop_to_the_main_loop(tmp_path):
    queued = []
    main = threading.current_thread()
    notified = []
    ctrl = xxdb_controller.XxdbController(
        _fake_config(), post=queued.append, home=tmp_path,
        on_notify=lambda title, body: notified.append(
            threading.current_thread()))
    ctrl.enabled = True
    ctrl.daemon = SimpleNamespace(port=1, addrs=['10.0.0.2'])
    callbacks = ctrl.daemon_callbacks()
    for fire in (lambda: callbacks['on_connect']('laptop'),
                 lambda: ctrl._on_client_open('desk')):
        worker = threading.Thread(target=fire)
        worker.start()
        worker.join()
    assert notified == []
    assert ctrl.active_clients == 0
    for fn in list(queued):
        fn()
    assert notified == [main, main]
    assert ctrl.active_clients == 2


def test_default_factory_wires_the_hopping_callbacks(tmp_path):
    queued = []
    ctrl = xxdb_controller.XxdbController(_fake_config(), post=queued.append,
                                          home=tmp_path)
    daemon = xxdb_controller._default_daemon_factory(ctrl)
    worker = threading.Thread(target=daemon.on_connect, args=('laptop',))
    worker.start()
    worker.join()
    assert len(queued) == 1
    assert daemon.is_locked == ctrl.locked_now
    assert daemon.trust is ctrl.trust


def test_late_callbacks_from_a_stopped_daemon_change_nothing(tmp_path):
    queued = []
    ctrl = xxdb_controller.XxdbController(_fake_config(), post=queued.append,
                                          home=tmp_path)
    ctrl.enabled = True
    ctrl.daemon = SimpleNamespace(port=1, addrs=['x'], stop=lambda: None)
    callbacks = ctrl.daemon_callbacks()
    ctrl.request_disable()
    callbacks['on_connect']('laptop')
    for fn in list(queued):
        fn()
    assert ctrl.active_clients == 0


@needs_openssl
def test_failing_on_connect_still_releases_the_connection(
        make_daemon, host_cert):
    def _boom(_name):
        raise RuntimeError('notification failed')

    daemon, port = make_daemon(on_connect=_boom)
    conn = _trusted_conn(daemon, port, host_cert)
    assert conn.request({'id': 1, 'cmd': 'hello'})['ok'] is True
    conn.close()
    assert _wait_for(lambda: daemon._open == 0 and daemon._unauth == 0
                     and daemon.live_sessions() == 0)


@needs_openssl
def test_connect_disconnect_balance_and_strangers_are_not_announced(
        make_daemon, host_cert):
    events = []
    daemon, port = make_daemon(on_connect=lambda n: events.append(('+', n)),
                               on_disconnect=lambda n: events.append(('-', n)),
                               on_activity=lambda: events.append('activity'))
    stranger = _Conn(port)
    assert stranger.request({'id': 1, 'cmd': 'hello'})['ok'] is False
    stranger.close()
    assert events == [], 'a stranger neither shows up nor keeps xxdb awake'
    conn = _trusted_conn(daemon, port, host_cert)
    assert conn.request({'id': 1, 'cmd': 'hello'})['ok'] is True
    conn.close()
    assert _wait_for(lambda: len(events) == 3)
    assert events == [('+', 'laptop'), 'activity', ('-', 'laptop')]


# --- pre-auth limits -------------------------------------------------------

def test_frame_reader_never_rescans_a_partial_line():
    class _CountingBuf(bytearray):
        scanned = 0

        def find(self, sub, start=0, *rest):
            _CountingBuf.scanned += len(self) - start
            return super().find(sub, start, *rest)

    reader = xxdb.FrameReader()
    reader._buf = _CountingBuf()
    for _ in range(2000):
        reader.feed(b'x' * 32)
    assert _CountingBuf.scanned <= 2000 * 32 * 2


def test_frame_reader_cap_follows_max_line():
    reader = xxdb.FrameReader(xxdb.PREAUTH_MAX_LINE_BYTES)
    with pytest.raises(xxdb.FrameError):
        reader.feed(b'x' * (xxdb.PREAUTH_MAX_LINE_BYTES + 1))
    reader = xxdb.FrameReader(xxdb.PREAUTH_MAX_LINE_BYTES)
    reader.feed(b'{"id": 1, "pad": "' + b'y' * 1000)
    reader.max_line = xxdb.MAX_LINE_BYTES
    assert reader.feed(b'"}\n')[0]['id'] == 1


@needs_openssl
def test_unauthenticated_frame_cap_is_64k(make_daemon):
    _daemon, port = make_daemon()
    conn = _Conn(port, timeout=5)
    try:
        conn.sock.sendall(b'x' * (70 * 1024))
    except OSError:
        pass
    assert conn.closed_within(5)


@needs_openssl
def test_tls_handshake_runs_per_connection_with_a_timeout(
        make_daemon, monkeypatch):
    monkeypatch.setattr(xxdb, 'HANDSHAKE_TIMEOUT_S', 1, raising=False)
    _daemon, port = make_daemon()
    stalled = socket.create_connection(('127.0.0.1', port), timeout=5)
    try:
        conn = _Conn(port, timeout=5)
        assert conn.request({'id': 1, 'cmd': 'hello'})['error'].startswith(
            'not-paired')
        conn.close()
        stalled.settimeout(5)
        try:
            assert stalled.recv(1) == b''
        except ConnectionResetError:
            pass
    finally:
        stalled.close()


@needs_openssl
def test_unauthenticated_connection_lifetime(make_daemon, monkeypatch):
    monkeypatch.setattr(xxdb, 'UNAUTH_LIFETIME_S', 0.5, raising=False)
    _daemon, port = make_daemon()
    conn = _Conn(port, timeout=5)
    assert conn.closed_within(5)


@needs_openssl
def test_unauthenticated_connection_cap(make_daemon):
    daemon, port = make_daemon()
    cap = getattr(xxdb, 'MAX_UNAUTH_CONNECTIONS', 4)
    held = [_Conn(port, timeout=5) for _ in range(cap)]
    try:
        try:
            extra = _Conn(port, timeout=5)
        except (ssl.SSLError, ConnectionError):
            refused = True
        else:
            try:
                refused = extra.read() is None
            except (TimeoutError, socket.timeout):
                refused = False
            extra.close()
        assert refused
        held.pop().close()
        assert _wait_for(lambda: daemon._unauth < cap)
        again = _Conn(port, timeout=5)
        assert again.request({'id': 1, 'cmd': 'hello'})['ok'] is False
        again.close()
    finally:
        for conn in held:
            conn.close()


# --- off means off ---------------------------------------------------------

@needs_openssl
def test_stop_ends_live_sessions_and_shells(make_daemon, host_cert):
    daemon, port = make_daemon()
    conn = _trusted_conn(daemon, port, host_cert)
    started = conn.request({'id': 7, 'cmd': 'shell', 'interactive': True})
    assert started['stream'] == 'pty-started'
    pid = _shell_pid(conn, 7)
    assert _alive(pid)
    daemon.stop()
    assert conn.closed_within(5)
    assert _wait_for(lambda: not _alive(pid))
    assert _wait_for(lambda: daemon.live_sessions() == 0)


def test_disable_while_starting_wins(tmp_path):
    queued = []
    release = threading.Event()
    stopped = []

    class _SlowDaemon:
        port, addrs = 37001, ['127.0.0.1']

        def start(self):
            release.wait(5)

        def stop(self):
            stopped.append(True)

    ctrl = xxdb_controller.XxdbController(
        _fake_config(), post=queued.append, home=tmp_path,
        daemon_factory=lambda _ctrl: _SlowDaemon())
    ctrl.request_enable()
    assert ctrl.starting is True
    ctrl.request_disable()
    release.set()
    assert _wait_for(lambda: queued)
    for fn in list(queued):
        fn()
    assert ctrl.enabled is False
    assert ctrl.daemon is None
    assert _wait_for(lambda: stopped == [True])


# --- lock, trust, tokens, forget ------------------------------------------

def _rpc_daemon(tmp_path, **overrides):
    kwargs = dict(cert_dir=tmp_path / 'certs',
                  trust_store=xxdb.TrustStore(path=tmp_path / 'trusted.json'),
                  shell_runner=lambda argv: (0, 'out', ''))
    kwargs.update(overrides)
    daemon = XxdbDaemon(**kwargs)
    daemon.phone_fp = 'ab' * 32
    home = tmp_path / 'home'
    home.mkdir(exist_ok=True)
    daemon._home = str(home)
    return daemon


def _rpc_session(daemon):
    session = xxdb_daemon._Session('127.0.0.1')
    daemon.trust.add('cd' * 32, 'laptop', 'PEM', now=time.time())
    session.tls_fp = 'cd' * 32
    return session


def test_locked_phone_answers_only_hello(tmp_path):
    allow = [False]
    daemon = _rpc_daemon(tmp_path, is_locked=lambda: True,
                         allow_while_locked=lambda: allow[0])
    session = _rpc_session(daemon)
    [hello] = daemon.handle_request(session, {'id': 1, 'cmd': 'hello'})
    assert hello['ok'] is True
    for message in ({'id': 2, 'cmd': 'shell', 'argv': ['id']},
                    {'id': 3, 'cmd': 'screenshot'},
                    {'id': 4, 'cmd': 'pair'}):
        [response] = daemon.handle_request(session, message)
        assert response['ok'] is False
        assert response['error'].startswith('locked'), message
    allow[0] = True
    [response] = daemon.handle_request(session, {'id': 5, 'cmd': 'shell',
                                                 'argv': ['id']})
    assert response['ok'] is True


def test_lock_check_fails_closed(tmp_path):
    def _broken():
        raise RuntimeError('no shell')

    daemon = _rpc_daemon(tmp_path, is_locked=_broken)
    [response] = daemon.handle_request(_rpc_session(daemon),
                                       {'id': 1, 'cmd': 'shell',
                                        'argv': ['id']})
    assert response['error'].startswith('locked')


def test_controller_asks_the_lock_state_on_the_main_loop(tmp_path,
                                                         monkeypatch):
    queued = []
    asked_on = []
    ctrl = xxdb_controller.XxdbController(_fake_config(), post=queued.append,
                                          home=tmp_path)

    def _probe():
        asked_on.append(threading.current_thread())
        return True

    ctrl.lock_probe = _probe
    answer = []
    worker = threading.Thread(target=lambda: answer.append(ctrl.locked_now()))
    worker.start()
    assert _wait_for(lambda: queued)
    queued.pop()()
    worker.join(5)
    assert answer == [True]
    assert asked_on == [threading.current_thread()]
    monkeypatch.setattr(xxdb_controller, 'LOCK_PROBE_TIMEOUT_S', 0.1)
    ctrl.lock_probe = lambda: False
    worker = threading.Thread(target=lambda: answer.append(ctrl.locked_now()))
    worker.start()
    worker.join(5)
    assert answer[-1] is True, 'no answer from the main loop counts as locked'
    assert not xxdb_controller.XxdbController(
        _fake_config(xxdb_allow_while_locked=False), home=tmp_path
    ).allow_while_locked()


def test_shell_wires_the_lock_probe():
    text = (REPO / 'launcher' / 'src' / 'main.py').read_text()
    assert 'ctrl.lock_probe = self._shell.is_locked' in text


def test_system_token_is_bound_to_the_resolved_path(tmp_path):
    daemon = _rpc_daemon(tmp_path)
    session = _rpc_session(daemon)
    outside = tmp_path / 'sys'
    outside.mkdir()
    (outside / 'real').mkdir()
    (outside / 'link').symlink_to(outside / 'real')
    payload = 'eA=='
    shown = str(outside / 'link' / 'a.txt')
    [ask] = daemon.handle_request(session, {'id': 1, 'cmd': 'push',
                                            'path': shown, 'data': payload,
                                            'system': True})
    token = ask['token']
    [(_t, label)] = daemon.system_access.pending()
    assert label == f'Write {outside / "real" / "a.txt"}'
    [again] = daemon.handle_request(session, {'id': 2, 'cmd': 'push',
                                              'path': shown, 'data': payload,
                                              'system': True, 'token': token})
    assert again['error'] == 'needs-confirm' and again['token'] == token
    assert len(daemon.system_access.pending()) == 1
    daemon.system_access.confirm(token, True)
    other = str(outside / 'b.txt')
    [refused] = daemon.handle_request(session, {'id': 3, 'cmd': 'push',
                                                'path': other, 'data': payload,
                                                'system': True,
                                                'token': token})
    assert refused['ok'] is False
    assert not Path(other).exists()
    [as_pull] = daemon.handle_request(session, {'id': 4, 'cmd': 'pull',
                                                'path': shown, 'system': True,
                                                'token': token})
    assert as_pull['ok'] is False
    [done] = daemon.handle_request(session, {'id': 5, 'cmd': 'push',
                                             'path': shown, 'data': payload,
                                             'system': True, 'token': token})
    assert done['ok'] is True
    assert done['path'] == str(outside / 'real' / 'a.txt')


def test_cli_sends_the_confirmed_token_back():
    cli = _load_host_cli()
    parser = cli.build_parser()
    args = parser.parse_args(['push', 'phone', 'a', '/etc/x', '--system',
                              '--token', 'abc123'])
    assert cli._with_system({'cmd': 'push'}, args) == {
        'cmd': 'push', 'system': True, 'token': 'abc123'}
    args = parser.parse_args(['pull', 'phone', '/etc/x', 'b', '--system'])
    assert cli._with_system({'cmd': 'pull'}, args) == {
        'cmd': 'pull', 'system': True}


def test_trust_expiry_reaches_the_daemon(tmp_path):
    now = [time.time()]
    trust = xxdb.TrustStore(path=tmp_path / 'trusted.json',
                            max_age_s=xxdb.trust_max_age_s(7),
                            clock=lambda: now[0])
    daemon = _rpc_daemon(tmp_path, trust_store=trust)
    session = _rpc_session(daemon)
    [ok] = daemon.handle_request(session, {'id': 1, 'cmd': 'hello'})
    assert ok['ok'] is True
    now[0] += 8 * 86400
    [refused] = daemon.handle_request(session, {'id': 2, 'cmd': 'hello'})
    assert refused['error'].startswith('not-paired')


def test_controller_trust_expiry_comes_from_config(tmp_path):
    ctrl = xxdb_controller.XxdbController(_fake_config(), home=tmp_path)
    assert ctrl.trust.max_age_s == xxdb.DEFAULT_TRUST_DAYS * 86400
    config = _fake_config(xxdb_trust_days=30)
    ctrl = xxdb_controller.XxdbController(
        config, home=tmp_path,
        daemon_factory=lambda _c: SimpleNamespace(start=lambda: None))
    assert ctrl.trust.max_age_s == 30 * 86400
    config.data['xxdb_trust_days'] = 2
    ctrl.request_enable()
    assert ctrl.trust.max_age_s == 2 * 86400


@needs_openssl
def test_forget_host_drops_its_live_sessions(tmp_path, make_daemon,
                                             host_cert):
    daemon, port = make_daemon()
    conn = _trusted_conn(daemon, port, host_cert)
    assert conn.request({'id': 1, 'cmd': 'hello'})['ok'] is True
    ctrl = xxdb_controller.XxdbController(_fake_config(), home=tmp_path)
    ctrl.trust = daemon.trust
    ctrl.daemon = daemon
    assert ctrl.forget_host(host_cert[3]) is True
    assert conn.closed_within(5)


def test_settings_row_says_paired_means_full_control():
    text = (REPO / 'launcher' / 'src' / 'settings_page.py').read_text()
    assert "'A paired computer has full control of the phone'" in text


def test_docs_cover_the_new_keys():
    text = (REPO / 'docs' / 'config.md').read_text()
    for needle in ('`xxdb_trust_days`', '`xxdb_allow_while_locked`',
                   'full control of the phone'):
        assert needle in text


# --- streams and PTYs ------------------------------------------------------

def _output_has(text):
    return lambda m: (m.get('stream') == 'pty-output'
                      and text in m.get('data', ''))


@needs_openssl
def test_two_shells_with_the_same_id_stay_apart(make_daemon, host_cert):
    daemon, port = make_daemon()
    first = _trusted_conn(daemon, port, host_cert)
    second = _Conn(port, host_cert[0], host_cert[1])
    try:
        for conn in (first, second):
            conn.send({'id': 1, 'cmd': 'shell', 'interactive': True})
            ok, seen = conn.read_until(
                lambda m: m.get('stream') == 'pty-started'
                or m.get('ok') is False)
            assert ok and seen[-1]['ok'] is True, seen
        first.send({'id': 1, 'cmd': 'shell', 'input': 'echo A-$((40+2))\n'})
        ok, seen_first = first.read_until(_output_has('A-42'))
        assert ok, seen_first
        second.send({'id': 1, 'cmd': 'shell', 'input': 'echo B-$((50+1))\n'})
        ok, seen_second = second.read_until(_output_has('B-51'))
        assert ok, seen_second
        _ok, tail = first.read_until(lambda m: False, timeout=0.5)
        first_text = ''.join(m.get('data', '') for m in seen_first + tail)
        second_text = ''.join(m.get('data', '') for m in seen_second)
        assert 'B-51' not in first_text
        assert 'A-42' not in second_text
    finally:
        first.close()
        second.close()


def test_client_allocates_stream_ids(monkeypatch):
    import io
    cli = _load_host_cli()
    client = cli.XxdbClient('phone', 1, 'ab' * 32, Path('k'), Path('c'))
    client._next_id = 5
    sent = []
    monkeypatch.setattr(client, '_send', sent.append)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(''))
    cli._interactive_shell(client)
    assert sent[0]['id'] == 5
    assert client.new_id() == 6


@needs_openssl
def test_finished_shell_is_reaped_and_reports_done(make_daemon, host_cert):
    daemon, port = make_daemon()
    conn = _trusted_conn(daemon, port, host_cert)
    try:
        conn.send({'id': 'x', 'cmd': 'shell', 'interactive': True})
        ok, _seen = conn.read_until(lambda m: m.get('stream') == 'pty-started')
        assert ok
        pid = _shell_pid(conn, 'x')
        conn.send({'id': 'x', 'cmd': 'shell', 'input': 'exit 3\n'})
        ok, seen = conn.read_until(lambda m: m.get('stream') == 'pty-done')
        assert ok, seen
        assert seen[-1]['returncode'] == 3
        assert not _alive(pid)
        assert _wait_for(lambda: all(not s.ptys for s in daemon._sessions))
    finally:
        conn.close()


@needs_openssl
def test_dropped_connection_kills_the_remote_shell(make_daemon, host_cert):
    daemon, port = make_daemon()
    conn = _trusted_conn(daemon, port, host_cert)
    conn.send({'id': 1, 'cmd': 'shell', 'interactive': True})
    ok, _seen = conn.read_until(lambda m: m.get('stream') == 'pty-started')
    assert ok
    shell_pid = _shell_pid(conn, 1)
    conn.send({'id': 1, 'cmd': 'shell',
               'input': 'sleep 300 & echo BG=$!\n'})
    ok, seen = conn.read_until(
        lambda m: re.search(r'BG=\d+', m.get('data', '')) is not None)
    assert ok, seen
    bg_pid = int(re.search(r'BG=(\d+)', seen[-1]['data']).group(1))
    assert _alive(shell_pid) and _alive(bg_pid)
    conn.close()
    assert _wait_for(lambda: not _alive(shell_pid))
    assert _wait_for(lambda: not _alive(bg_pid))

