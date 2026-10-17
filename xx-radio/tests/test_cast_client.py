"""The V2 client against a fake TLS receiver. Loopback only."""

import json
import socket
import ssl
import struct
import subprocess
import threading

import pytest

from xx_music.cast import proto
from xx_music.cast.client import CastClient

_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex


@pytest.fixture(autouse=True)
def _loopback_only(monkeypatch):
    def guard(sock, *args):
        target = args[0] if args else None
        host = target[0] if isinstance(target, tuple) else ""
        if host in ("127.0.0.1", "::1", "localhost"):
            return _REAL_CONNECT(sock, *args)
        raise AssertionError("tests must not use the network")

    def guard_ex(sock, *args):
        target = args[0] if args else None
        host = target[0] if isinstance(target, tuple) else ""
        if host in ("127.0.0.1", "::1", "localhost"):
            return _REAL_CONNECT_EX(sock, *args)
        raise AssertionError("tests must not use the network")

    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard_ex)


def _certificate(tmp_path):
    key, cert = tmp_path / "key.pem", tmp_path / "cert.pem"
    proc = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048",
         "-keyout", str(key), "-out", str(cert), "-days", "1",
         "-nodes", "-subj", "/CN=cast-test"],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return key, cert


class FakeReceiver:
    """A minimal TLS speaker: answers status, records loads."""

    def __init__(self, cert, key):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert), str(key))
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        raw.bind(("127.0.0.1", 0))
        raw.listen(5)
        raw.settimeout(0.5)
        self.port = raw.getsockname()[1]
        self._raw = raw
        self._context = context
        self._stop = threading.Event()
        self._conns: list = []
        self._served: list = []
        self.received: list = []
        self.loads: list = []
        self.level = 0.8
        self.state = "PLAYING"
        self.at = 0.0
        self.session = 7
        self.transport = "transport-1"
        self._lock = threading.Lock()
        self.worker = threading.Thread(target=self._accept,
                                       daemon=True,
                                       name="fake-cast")
        self.worker.start()

    def close(self):
        self._stop.set()
        try:
            self._raw.close()
        except OSError:
            pass
        for conn in list(self._conns):
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        self.worker.join(timeout=5)
        for served in list(self._served):
            if served.is_alive():
                served.join(timeout=5)

    def _accept(self):
        while not self._stop.is_set():
            try:
                conn, _addr = self._raw.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            try:
                sock = self._context.wrap_socket(conn, server_side=True)
            except (ssl.SSLError, OSError):
                try:
                    conn.close()
                except OSError:
                    pass
                continue
            self._conns.append(sock)
            worker = threading.Thread(target=self._serve, args=(sock,),
                                      daemon=True)
            self._served.append(worker)
            worker.start()

    def _read_frame(self, sock):
        size_raw = b""
        while len(size_raw) < 4:
            piece = sock.recv(4 - len(size_raw))
            if not piece:
                raise ConnectionError("closed")
            size_raw += piece
        (size,) = struct.unpack(">I", size_raw)
        body = b""
        while len(body) < size:
            piece = sock.recv(size - len(body))
            if not piece:
                raise ConnectionError("closed")
            body += piece
        return body

    def _send(self, sock, namespace, payload: dict):
        body = proto.encode("receiver-0", "sender-0", namespace,
                            json.dumps(payload))
        sock.sendall(struct.pack(">I", len(body)) + body)

    def _receiver_status(self, request_id=0):
        return {"type": "RECEIVER_STATUS", "requestId": request_id,
                "status": {
                    "applications": [{"transportId": self.transport,
                                      "appId": proto.DEFAULT_RECEIVER}],
                    "volume": {"level": self.level, "muted": False}}}

    def _media_status(self, request_id=0, media_id="tr-001", url=""):
        media = {"duration": 200.0,
                 "contentId": url or "http://127.0.0.1/t/x",
                 "customData": {"id": media_id}}
        row = {"mediaSessionId": self.session,
               "playerState": self.state, "currentTime": self.at,
               "media": media}
        return {"type": "MEDIA_STATUS", "requestId": request_id,
                "status": [row]}

    def _serve(self, sock):
        media_id, url = "tr-001", ""
        try:
            while not self._stop.is_set():
                try:
                    body = self._read_frame(sock)
                except (ConnectionError, OSError, ssl.SSLError):
                    return
                parsed = proto.decode(body)
                if parsed is None:
                    continue
                _src, _dst, namespace, payload = parsed
                try:
                    message = json.loads(payload)
                except ValueError:
                    continue
                kind = message.get("type", "")
                request_id = message.get("requestId", 0)
                with self._lock:
                    self.received.append((namespace, kind))
                if namespace == proto.NS_HEARTBEAT and kind == "PING":
                    self._send(sock, namespace, {"type": "PONG"})
                elif namespace == proto.NS_RECEIVER and \
                        kind == "GET_STATUS":
                    self._send(sock, namespace,
                               self._receiver_status(request_id))
                elif namespace == proto.NS_RECEIVER and kind == "LAUNCH":
                    self._send(sock, namespace,
                               self._receiver_status(request_id))
                elif namespace == proto.NS_RECEIVER and \
                        kind == "SET_VOLUME":
                    volume = message.get("volume", {})
                    with self._lock:
                        if "level" in volume:
                            self.level = float(volume["level"])
                    self._send(sock, namespace,
                               self._receiver_status(request_id))
                elif namespace == proto.NS_MEDIA and kind == "LOAD":
                    media = message.get("media", {})
                    media_id = str(
                        (message.get("customData") or {}).get("id")
                        or "tr-001")
                    url = str(media.get("contentId") or "")
                    with self._lock:
                        self.loads.append(message)
                        self.state = "PLAYING"
                        self.at = float(message.get("currentTime", 0.0))
                    self._send(sock, namespace,
                               self._media_status(request_id, media_id,
                                                  url))
                elif namespace == proto.NS_MEDIA and kind == "PLAY":
                    with self._lock:
                        self.state = "PLAYING"
                    self._send(sock, namespace,
                               self._media_status(request_id, media_id,
                                                  url))
                elif namespace == proto.NS_MEDIA and kind == "PAUSE":
                    with self._lock:
                        self.state = "PAUSED"
                    self._send(sock, namespace,
                               self._media_status(request_id, media_id,
                                                  url))
                elif namespace == proto.NS_MEDIA and kind == "STOP":
                    with self._lock:
                        self.state = "IDLE"
                    self._send(sock, namespace,
                               self._media_status(request_id, media_id,
                                                  url))
                elif namespace == proto.NS_MEDIA and kind == "SEEK":
                    with self._lock:
                        self.at = float(message.get("currentTime", 0.0))
                        self.state = "PLAYING"
                    self._send(sock, namespace,
                               self._media_status(request_id, media_id,
                                                  url))
                elif namespace == proto.NS_MEDIA and \
                        kind == "GET_STATUS":
                    self._send(sock, namespace,
                               self._media_status(request_id, media_id,
                                                  url))
        finally:
            try:
                sock.close()
            except OSError:
                pass


@pytest.fixture()
def receiver(tmp_path):
    key, cert = _certificate(tmp_path)
    fake = FakeReceiver(cert, key)
    yield fake
    fake.close()


def _wait_for(predicate, timeout_s=10.0):
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception:  # noqa: BLE001 - keep polling
            pass
        time.sleep(0.05)
    assert predicate(), "timed out waiting for the fake receiver"


def test_load_and_transport_controls(receiver):
    media: list = []
    client = CastClient("127.0.0.1", receiver.port,
                        on_media=lambda *a: media.append(a))
    try:
        client.open()
        assert client.ensure_session() == "transport-1"
        client.load("http://127.0.0.1:9/t/secret", "audio/ogg",
                    "Glass Harbor", "Lumen Drive", "Tidewater",
                    "http://art/1", 30.0, "tr-001")
        _wait_for(lambda: len(receiver.loads) == 1)
        sent = receiver.loads[0]
        assert sent["media"]["contentId"] == \
            "http://127.0.0.1:9/t/secret"
        assert sent["media"]["metadata"]["title"] == "Glass Harbor"
        assert sent["currentTime"] == 30.0
        _wait_for(lambda: client.playing is True)
        assert client.content_id == "tr-001"
        client.pause()
        _wait_for(lambda: client.playing is False)
        client.seek(61.5)
        _wait_for(lambda: client.position_ms == 61500)
        client.set_volume(0.5)
        _wait_for(lambda: client.volume == 0.5)
        _wait_for(lambda: "SEEK" in [k for _n, k in receiver.received])
        _wait_for(lambda: "SET_VOLUME" in
                  [k for _n, k in receiver.received])
        kinds = [kind for _ns, kind in receiver.received]
        for want in ("LOAD", "PAUSE", "SEEK", "SET_VOLUME"):
            assert want in kinds, kinds
    finally:
        client.close()


def test_receiver_death_fires_closed_once(receiver):
    fired: list = []
    done = threading.Event()
    client = CastClient("127.0.0.1", receiver.port,
                        on_closed=lambda reason: (
                            fired.append(reason), done.set()))
    try:
        client.open()
        client.ensure_session()
        receiver.close()
        assert done.wait(timeout=10)
        assert len(fired) == 1
    finally:
        client.close()


def test_dead_device_fails_fast():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    client = CastClient("127.0.0.1", port, timeout_s=0.5)
    try:
        with pytest.raises(Exception):
            client.open()
    finally:
        client.close()
