"""mTLS interop: client presents its cert; stub requires it.

Covers the LocalSend 1.18.2 lesson without LocalSend: a local stub
HTTPS server uses CERT_REQUIRED + a self-signed cert (trusting the
test client cert), so every xx-share request without a client cert
fails with TLSV13_ALERT_CERTIFICATE_REQUIRED and every request with
the cert succeeds, including a two-file send whose SHA-256 matches.
"""
import hashlib
import json
import secrets
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from certs import cert_fingerprint, ensure_certpair
from protocol import DeviceInfo


class _StubState:
    def __init__(self, info):
        self.info = info
        self.sessions = {}
        self.uploads = {}
        self.lock = threading.Lock()


class _StubHandler(BaseHTTPRequestHandler):
    server_version = "stub/1"

    def log_message(self, *args):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode())

    def do_GET(self):
        if urlparse(self.path).path == "/api/localsend/v2/info":
            self._json(200, self.server.state.info.to_dict())
        else:
            self._json(404, {"message": "unknown"})

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/localsend/v2/register":
            self._read_json()
            self._json(200, self.server.state.info.to_register_dict())
        elif parsed.path == "/api/localsend/v2/prepare-upload":
            body = self._read_json()
            files = body.get("files", {})
            session_id = secrets.token_urlsafe(8)
            tokens = {fid: secrets.token_urlsafe(16) for fid in files}
            with self.server.state.lock:
                self.server.state.sessions[session_id] = dict(tokens)
            self._json(200, {"sessionId": session_id, "files": tokens})
        elif parsed.path == "/api/localsend/v2/upload":
            query = parse_qs(parsed.query)
            session_id = query.get("sessionId", [""])[0]
            file_id = query.get("fileId", [""])[0]
            token = query.get("token", [""])[0]
            with self.server.state.lock:
                expected = self.server.state.sessions.get(session_id, {}).get(
                    file_id)
            if expected is None or token != expected:
                self._json(403, {"message": "bad token"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            data = self.rfile.read(length) if length else b""
            with self.server.state.lock:
                self.server.state.uploads[(session_id, file_id)] = data
            self._json(200, {})
        elif parsed.path == "/api/localsend/v2/cancel":
            self._json(200, {})
        else:
            self._json(404, {"message": "unknown"})


@pytest.fixture()
def mtls_pair(tmp_path):
    stub_cert, stub_key = ensure_certpair(tmp_path / "stub-certs")
    cli_cert, cli_key = ensure_certpair(tmp_path / "cli-certs")
    return (stub_cert, stub_key), (cli_cert, cli_key)


@pytest.fixture()
def stub(mtls_pair):
    (stub_cert, stub_key), (cli_cert, _cli_key) = mtls_pair
    stub_fp = cert_fingerprint(stub_cert)
    info = DeviceInfo(alias="stub", fingerprint=stub_fp, port=0,
                      device_type="desktop")
    state = _StubState(info)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler,
                                 bind_and_activate=False)
    server.state = state  # type: ignore[attr-defined]
    server.allow_reuse_address = True
    server.daemon_threads = True
    server.server_bind()
    server.server_activate()
    port = server.socket.getsockname()[1]
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(stub_cert), str(stub_key))
    context.load_verify_locations(cafile=str(cli_cert))
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = False
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    state.info = DeviceInfo(alias="stub", fingerprint=stub_fp, port=port,
                            device_type="desktop")
    yield server, state, port
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def _client_ctx(cert=None, key=None):
    from client import _client_context

    return _client_context(cert, key)


def test_client_context_loads_chain(mtls_pair):
    (_stub, _sk), (cli_cert, cli_key) = mtls_pair
    context = _client_ctx(str(cli_cert), str(cli_key))
    assert context.verify_mode == ssl.CERT_NONE
    plain = _client_ctx()
    assert plain.verify_mode == ssl.CERT_NONE


def test_info_requires_client_cert(stub, mtls_pair):
    from client import fetch_info

    (_stub, _sk), (cli_cert, cli_key) = mtls_pair
    _server, _state, port = stub
    # TLS 1.3 sends the client-cert refusal after the handshake, so the
    # client sees it as an alert or as a reset, depending on timing.
    with pytest.raises((ssl.SSLError, ConnectionResetError, BrokenPipeError)):
        fetch_info("127.0.0.1", port, timeout=5.0)
    body = fetch_info("127.0.0.1", port, timeout=5.0,
                      cert_path=str(cli_cert), key_path=str(cli_key))
    assert json.loads(body.decode())["alias"] == "stub"


def test_register_requires_client_cert(stub, mtls_pair):
    from client import register_with

    (_stub, _sk), (cli_cert, cli_key) = mtls_pair
    _server, state, port = stub
    own = DeviceInfo(alias="me", fingerprint="aa" * 32)
    with pytest.raises((ssl.SSLError, OSError)):
        register_with("127.0.0.1", port, own, timeout=5.0)
    register_with("127.0.0.1", port, own, timeout=5.0,
                  cert_path=str(cli_cert), key_path=str(cli_key))


def test_two_file_send_sha256_matches(stub, mtls_pair, tmp_path):
    from client import send_files

    (_stub, _sk), (cli_cert, cli_key) = mtls_pair
    _server, state, port = stub
    text = tmp_path / "note.txt"
    text.write_bytes(b"hello localsend interop\n")
    binary = tmp_path / "blob.bin"
    payload = bytes(index % 251 for index in range(3 * 1024 * 1024))
    binary.write_bytes(payload)
    peer = DeviceInfo(alias="stub", fingerprint=state.info.fingerprint,
                      port=port)
    sender = DeviceInfo(alias="me", fingerprint=cert_fingerprint(cli_cert))
    send_files(peer, "127.0.0.1", sender, [text, binary],
               cert_path=str(cli_cert), key_path=str(cli_key))
    with state.lock:
        assert len(state.sessions) == 1
        session_id = next(iter(state.sessions))
        got_text = state.uploads[(session_id, "file-0")]
        got_bin = state.uploads[(session_id, "file-1")]
    assert hashlib.sha256(got_text).hexdigest() == hashlib.sha256(
        text.read_bytes()).hexdigest()
    assert hashlib.sha256(got_bin).hexdigest() == hashlib.sha256(
        payload).hexdigest()


def test_cancel_requires_client_cert(stub, mtls_pair):
    from client import cancel_session

    (_stub, _sk), (cli_cert, cli_key) = mtls_pair
    _server, _state, port = stub
    with pytest.raises((ssl.SSLError, OSError)):
        cancel_session("127.0.0.1", port, "nope", timeout=5.0)
    cancel_session("127.0.0.1", port, "nope", timeout=5.0,
                   cert_path=str(cli_cert), key_path=str(cli_key))


def test_verified_request_pins_fingerprint(stub, mtls_pair):
    from client import FingerprintMismatch, verified_request
    from protocol import PATH_INFO

    (_stub, _sk), (cli_cert, cli_key) = mtls_pair
    _server, state, port = stub
    status, _raw = verified_request("127.0.0.1", port,
                                    state.info.fingerprint, "GET", PATH_INFO,
                                    timeout=5.0,
                                    cert_path=str(cli_cert),
                                    key_path=str(cli_key))
    assert status == 200
    with pytest.raises(FingerprintMismatch):
        verified_request("127.0.0.1", port, "00" * 32, "GET", PATH_INFO,
                         timeout=5.0,
                         cert_path=str(cli_cert), key_path=str(cli_key))
