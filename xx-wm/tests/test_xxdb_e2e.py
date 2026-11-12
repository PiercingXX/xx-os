"""xxdb end-to-end loopback: real TLS daemon + client over 127.0.0.1.

Covers pair -> shell echo -> screenshot (stubbed) with mutual TLS, the
unpaired-refused path, and pairing through a man-in-the-middle. Needs
`openssl` for certificates.
"""
import hashlib
import json
import shutil
import socket
import ssl
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import xxdb
from test_xxdb_rpc import _load_host_cli
from xxdb_daemon import XxdbDaemon

needs_openssl = pytest.mark.skipif(shutil.which('openssl') is None,
                                   reason='needs openssl for certificates')

_FAKE_PNG = (b'\x89PNG\r\n\x1a\n' + b'stub-screenshot-bytes')


def _make_cert(directory: Path, name: str):
    key_path = directory / f'{name}.key.pem'
    cert_path = directory / f'{name}.cert.pem'
    subprocess.run(
        ['openssl', 'req', '-x509', '-newkey', 'rsa:2048',
         '-keyout', str(key_path), '-out', str(cert_path),
         '-days', '2', '-nodes', '-subj', f'/CN={name}'],
        check=True, capture_output=True, timeout=120)
    der = ssl.PEM_cert_to_DER_cert(cert_path.read_text(encoding='utf-8'))
    return key_path, cert_path, cert_path.read_text(encoding='utf-8'), \
        hashlib.sha256(der).hexdigest(), der


class _Peer:
    def __init__(self, host, port, key_path=None, cert_path=None):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        if cert_path is not None:
            context.load_cert_chain(str(cert_path), str(key_path))
        raw = socket.create_connection((host, port), timeout=10)
        self.sock = context.wrap_socket(raw, server_hostname=host)
        self.sock.settimeout(10)
        der = self.sock.getpeercert(binary_form=True)
        self.server_fp = hashlib.sha256(bytes(der)).hexdigest()
        self._file = self.sock.makefile('rwb')

    def request(self, payload: dict) -> dict:
        self._file.write((json.dumps(payload) + '\n').encode())
        self._file.flush()
        line = self._file.readline()
        assert line, 'phone closed the connection'
        return json.loads(line.decode())

    def close(self):
        try:
            self._file.close()
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


@pytest.fixture
def loopback_daemon(tmp_path, monkeypatch):
    monkeypatch.setattr(xxdb, 'interface_entries',
                        lambda: [('lo', False, ['127.0.0.1'], True)])
    home = tmp_path / 'home'
    home.mkdir()
    daemon = XxdbDaemon(
        cert_dir=tmp_path / 'phone-certs',
        trust_store=xxdb.TrustStore(path=tmp_path / 'trusted.json'),
        allow_loopback=True,
        screenshot_provider=lambda: _FAKE_PNG,
        shell_log_path=tmp_path / 'shell.log',
        ipc_forwarder=lambda verb: None,
        input_injector=lambda action, params: (True, 'ok'),
        sudo_check=lambda: False,
        shell_restarter=lambda: 999,
    )
    daemon._home = str(home)
    addrs, port = daemon.start()
    assert addrs == ['127.0.0.1']
    assert 37000 <= port <= 37999
    yield daemon, port, home
    daemon.stop()


@needs_openssl
def test_e2e_pair_shell_screenshot(tmp_path, loopback_daemon):
    daemon, port, _home = loopback_daemon
    _key, cert_path, host_pem, host_fp, _der = _make_cert(tmp_path, 'host')

    stranger = _Peer('127.0.0.1', port)
    try:
        response = stranger.request({'id': 1, 'cmd': 'shell',
                                     'argv': ['echo', 'nope']})
        assert response['ok'] is False
        assert response['error'].startswith('not-paired')
    finally:
        stranger.close()

    code, _expires = daemon.pairing.issue()
    mac = xxdb.pairing_mac(code, daemon.phone_fp, host_fp)
    # The pairing connection carries no client cert (the phone has nothing
    # to verify it against yet); the HMAC binds the embedded host cert.
    first = _Peer('127.0.0.1', port)
    try:
        assert first.server_fp == daemon.phone_fp
        response = first.request({'id': 1, 'cmd': 'pair',
                                  'host_cert_pem': host_pem,
                                  'host_name': 'e2e-laptop', 'mac': mac})
        assert response == {'id': 1, 'ok': True,
                            'phone_fp': daemon.phone_fp,
                            'host_fp': response['host_fp']}
        assert daemon.trust.contains(response['host_fp'])
    finally:
        first.close()

    second = _Peer('127.0.0.1', port, tmp_path / 'host.key.pem', cert_path)
    try:
        response = second.request({'id': 2, 'cmd': 'hello'})
        assert response['ok'] is True
        response = second.request({'id': 3, 'cmd': 'shell',
                                   'argv': ['echo', 'hello-xxdb']})
        assert response['ok'] is True
        assert response['stdout'] == 'hello-xxdb\n'
        response = second.request({'id': 4, 'cmd': 'screenshot'})
        assert response['ok'] is True
        import base64
        assert base64.b64decode(response['data']) == _FAKE_PNG
    finally:
        second.close()


@needs_openssl
def test_e2e_pair_burn_and_logout(tmp_path, loopback_daemon):
    daemon, port, _home = loopback_daemon
    _key, cert_path, host_pem, host_fp, _der = _make_cert(tmp_path, 'host2')
    code, _expires = daemon.pairing.issue()
    wrong = '000000' if code != '000000' else '111111'
    peer = _Peer('127.0.0.1', port)
    try:
        for attempt in range(3):
            response = peer.request(
                {'id': attempt, 'cmd': 'pair',
                 'host_cert_pem': host_pem, 'host_name': 'x',
                 'mac': xxdb.pairing_mac(wrong, daemon.phone_fp,
                                         host_fp)})
            assert response['ok'] is False
        assert response['error'] == 'pairing code burned'
        response = peer.request(
            {'id': 99, 'cmd': 'pair',
             'host_cert_pem': host_pem, 'host_name': 'x',
             'mac': xxdb.pairing_mac(code, daemon.phone_fp, host_fp)})
        assert response['ok'] is False
    finally:
        peer.close()


# --- pairing through a man-in-the-middle --------------------------------

class _Mitm:
    """Terminates TLS with its own certificate and relays plaintext lines
    to the real phone, recording everything the host sends."""

    def __init__(self, cert_dir: Path, upstream_port: int) -> None:
        key, cert, _pem, self.fp, _der = _make_cert(cert_dir, 'mitm')
        self._ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self._ctx.load_cert_chain(str(cert), str(key))
        self._upstream = upstream_port
        self.listener = socket.create_server(('127.0.0.1', 0))
        self.port = self.listener.getsockname()[1]
        self.captured = bytearray()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        conn, _addr = self.listener.accept()
        conn.settimeout(10)
        try:
            near = self._ctx.wrap_socket(conn, server_side=True)
            up_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            up_ctx.check_hostname = False
            up_ctx.verify_mode = ssl.CERT_NONE
            far = up_ctx.wrap_socket(
                socket.create_connection(('127.0.0.1', self._upstream),
                                         timeout=10),
                server_hostname='phone')
            near_file, far_file = near.makefile('rwb'), far.makefile('rwb')
            while True:
                line = near_file.readline()
                if not line:
                    break
                self.captured.extend(line)
                far_file.write(line)
                far_file.flush()
                reply = far_file.readline()
                if not reply:
                    break
                near_file.write(reply)
                near_file.flush()
        except (OSError, ssl.SSLError):
            pass
        finally:
            conn.close()
            self.listener.close()


def _pair_args(port, code, fingerprint):
    return SimpleNamespace(hostport=f'127.0.0.1:{port}', code=code,
                           fingerprint=[fingerprint])


@needs_openssl
def test_pairing_refuses_a_man_in_the_middle_before_sending(
        tmp_path, loopback_daemon, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'laptop'))
    daemon, port, _home = loopback_daemon
    code, _expires = daemon.pairing.issue()
    mitm = _Mitm(tmp_path, port)
    cli = _load_host_cli()
    with pytest.raises(cli.ClientError, match='fingerprint mismatch'):
        cli.cmd_pair(_pair_args(mitm.port, code,
                                xxdb.fingerprint_prefix(daemon.phone_fp)))
    mitm.thread.join(5)
    assert bytes(mitm.captured) == b''
    assert daemon.trust.list() == []


@needs_openssl
def test_pairing_hmac_fails_through_a_relay_and_never_carries_the_code(
        tmp_path, loopback_daemon, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'laptop'))
    daemon, port, _home = loopback_daemon
    code, _expires = daemon.pairing.issue()
    mitm = _Mitm(tmp_path, port)
    cli = _load_host_cli()
    monkeypatch.setattr(cli, 'check_phone_fingerprint',
                        lambda shown, observed: None)
    assert cli.cmd_pair(_pair_args(mitm.port, code, '0' * 16)) == 1
    mitm.thread.join(5)
    sent = bytes(mitm.captured).decode()
    request = json.loads(sent.splitlines()[0])
    assert request['cmd'] == 'pair'
    assert 'code' not in request
    assert code not in sent
    assert daemon.trust.list() == []
    assert daemon.pairing.tries == 1


@needs_openssl
def test_pairing_pins_the_observed_certificate(tmp_path, loopback_daemon,
                                               monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'laptop'))
    daemon, port, _home = loopback_daemon
    real_pair = daemon._cmd_pair

    def _lying_pair(session, request_id, message):
        for response in real_pair(session, request_id, message):
            yield dict(response, phone_fp='ff' * 32)

    daemon._cmd_pair = _lying_pair
    code, _expires = daemon.pairing.issue()
    cli = _load_host_cli()
    assert cli.cmd_pair(_pair_args(
        port, code, xxdb.fingerprint_prefix(daemon.phone_fp))) == 0
    phones = cli.load_phones()
    assert phones['127.0.0.1']['phone_fp'] == daemon.phone_fp
    assert len(daemon.trust.list()) == 1


def test_cli_fingerprint_check():
    cli = _load_host_cli()
    observed = '0123456789abcdef' + '9' * 48
    cli.check_phone_fingerprint('0123 4567 89AB cdef', observed)
    with pytest.raises(cli.ClientError, match='mismatch'):
        cli.check_phone_fingerprint('0123 4567 89ab cdee', observed)
    with pytest.raises(cli.ClientError):
        cli.check_phone_fingerprint('0123', observed)


