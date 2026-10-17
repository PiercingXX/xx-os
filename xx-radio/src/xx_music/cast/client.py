"""A small Cast V2 sender: TLS to port 8009, no new dependencies.

Trust: receivers serve self-signed certificates, so this socket skips
verification, like every Cast sender. The relaxed context is built for
this connection only and is never installed globally.
"""

from __future__ import annotations

import socket
import ssl
import struct
import threading

from xx_music.cast import proto

CONNECT_TIMEOUT_S = 3.0
TRANSPORT_WAIT_S = 1.2
LAUNCH_WAIT_S = 8.0
HEARTBEAT_S = 5.0
POLL_S = 1.0


class CastError(Exception):
    pass


def _tls_socket(host: str, port: int, timeout_s: float) -> socket.socket:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    raw = socket.create_connection((host, int(port)),
                                   timeout=timeout_s)
    try:
        wrapped = context.wrap_socket(raw, server_hostname=host)
    except Exception:
        raw.close()
        raise
    wrapped.settimeout(10.0)
    return wrapped


def _send_frame(sock: socket.socket, lock: threading.Lock,
                body: bytes) -> None:
    with lock:
        sock.sendall(struct.pack(">I", len(body)) + body)


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < count:
        piece = sock.recv(count - len(chunks))
        if not piece:
            raise ConnectionError("speaker closed the connection")
        chunks.extend(piece)
    return bytes(chunks)


class CastClient:
    """One speaker connection. Blocking calls run on worker threads."""

    def __init__(self, host: str, port: int = 8009,
                 on_media=None, on_volume=None, on_closed=None,
                 socket_factory=None,
                 timeout_s: float = CONNECT_TIMEOUT_S) -> None:
        self._host = host
        self._port = int(port or 8009)
        self.on_media = on_media
        self.on_volume = on_volume
        self.on_closed = on_closed
        self._socket_factory = socket_factory or _tls_socket
        self._timeout_s = float(timeout_s)
        self._sock: socket.socket | None = None
        self._write_lock = threading.Lock()
        self._req = 1
        self._req_lock = threading.Lock()
        self._stop = threading.Event()
        self._closed_fired = False
        self._threads: list[threading.Thread] = []
        self._transport_id = proto.RECEIVER_ID
        self._last_transport = ""
        self._transport_event = threading.Event()
        self.playing = False
        self.volume = 1.0
        self.muted = False
        self.media_session_id = 0
        self.content_id = ""
        self.position_ms = 0
        self.duration_ms = 0

    # -- setup ----------------------------------------------------------
    def _next_id(self) -> int:
        with self._req_lock:
            value = self._req
            self._req += 1
            return value

    def open(self) -> None:
        self.close()
        self._stop.clear()
        self._closed_fired = False
        self._transport_event.clear()
        sock = self._socket_factory(self._host, self._port,
                                    self._timeout_s)
        self._sock = sock
        for target, name in ((self._read_loop, "xx-cast-read"),
                             (self._heartbeat_loop, "xx-cast-beat"),
                             (self._poll_loop, "xx-cast-poll")):
            worker = threading.Thread(target=target, daemon=True,
                                      name=name)
            self._threads.append(worker)
            worker.start()
        self._send(proto.NS_CONNECTION, proto.RECEIVER_ID,
                   proto.connect_payload())

    def ensure_session(self,
                       transport_wait: float = TRANSPORT_WAIT_S,
                       launch_wait: float = LAUNCH_WAIT_S) -> str:
        self._send(proto.NS_RECEIVER, proto.RECEIVER_ID,
                   proto.receiver_get_status(self._next_id()))
        transport = self._wait_transport(transport_wait)
        if transport is None:
            self._send(proto.NS_RECEIVER, proto.RECEIVER_ID,
                       proto.receiver_launch(self._next_id()))
            transport = self._wait_transport(launch_wait)
        if transport is None:
            raise CastError("speaker did not start a media receiver")
        self._transport_id = transport
        self._send(proto.NS_CONNECTION, self._transport_id,
                   proto.connect_payload())
        return transport

    def attach(self, timeout_s: float = 4.0) -> str:
        self.open()
        self._send(proto.NS_RECEIVER, proto.RECEIVER_ID,
                   proto.receiver_get_status(self._next_id()))
        transport = self._wait_transport(timeout_s)
        if transport is None:
            raise CastError("speaker is not running a media receiver")
        self._transport_id = transport
        self._send(proto.NS_CONNECTION, self._transport_id,
                   proto.connect_payload())
        self.playing = True
        self._send(proto.NS_RECEIVER, proto.RECEIVER_ID,
                   proto.receiver_get_status(self._next_id()))
        return transport

    def _wait_transport(self, timeout_s: float) -> str | None:
        if self._last_transport and \
                self._last_transport != proto.RECEIVER_ID:
            return self._last_transport
        self._transport_event.wait(timeout=max(0.0, float(timeout_s)))
        if self._last_transport and \
                self._last_transport != proto.RECEIVER_ID:
            return self._last_transport
        return None

    def close(self) -> None:
        self._stop.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001 - close is best effort
                pass
        current = threading.current_thread()
        for worker in self._threads:
            if worker is not current and worker.is_alive():
                worker.join(timeout=2.0)
        self._threads = []
        self._transport_id = proto.RECEIVER_ID

    # -- media ----------------------------------------------------------
    def load(self, url: str, mime: str, title: str, artist: str = "",
             album: str = "", art_url: str = "",
             position_s: float = 0.0, media_id: str = "") -> None:
        if not self._sock:
            self.open()
        self.ensure_session()
        payload = proto.media_load(
            self._next_id(), url, mime or "audio/mpeg", title,
            artist, album, art_url, position_s, True, media_id)
        if len(payload.encode("utf-8")) > proto.MAX_MESSAGE_BYTES:
            raise CastError("track details are too large to send")
        self._send(proto.NS_MEDIA, self._transport_id, payload)
        self.playing = True
        if media_id:
            self.content_id = media_id
        self.position_ms = int(max(0.0, float(position_s)) * 1000.0)
        self._send(proto.NS_MEDIA, self._transport_id,
                   proto.media_get_status(self._next_id(),
                                          self.media_session_id))
        self._emit_media()

    def play(self) -> None:
        self._media_command("PLAY")

    def pause(self) -> None:
        self._media_command("PAUSE")

    def stop_media(self) -> None:
        self._media_command("STOP")

    def _media_command(self, kind: str) -> None:
        if self._transport_id == proto.RECEIVER_ID:
            return
        if kind == "PLAY":
            payload = proto.media_play(self._next_id(),
                                       self.media_session_id)
        elif kind == "PAUSE":
            payload = proto.media_pause(self._next_id(),
                                        self.media_session_id)
        else:
            payload = proto.media_stop(self._next_id(),
                                       self.media_session_id)
        try:
            self._send(proto.NS_MEDIA, self._transport_id, payload)
        except CastError:
            return
        if kind == "PLAY":
            self.playing = True
        elif kind in ("PAUSE", "STOP"):
            self.playing = False
        self._emit_media()

    def seek(self, position_s: float) -> None:
        if self._transport_id == proto.RECEIVER_ID:
            return
        at = max(0.0, float(position_s))
        self.position_ms = int(at * 1000.0)
        try:
            self._send(proto.NS_MEDIA, self._transport_id,
                       proto.media_seek(self._next_id(),
                                        self.media_session_id, at))
        except CastError:
            return
        self.playing = True
        self._emit_media()

    def set_volume(self, level: float) -> None:
        self.volume = min(1.0, max(0.0, float(level)))
        try:
            self._send(proto.NS_RECEIVER, proto.RECEIVER_ID,
                       proto.receiver_set_volume(self._next_id(),
                                                 self.volume, None))
        except CastError:
            return
        self._emit_volume()

    def set_muted(self, muted: bool) -> None:
        self.muted = bool(muted)
        try:
            self._send(proto.NS_RECEIVER, proto.RECEIVER_ID,
                       proto.receiver_set_volume(self._next_id(), None,
                                                 self.muted))
        except CastError:
            return
        self._emit_volume()

    def refresh(self) -> None:
        if self._transport_id == proto.RECEIVER_ID:
            return
        try:
            self._send(proto.NS_MEDIA, self._transport_id,
                       proto.media_get_status(self._next_id(),
                                              self.media_session_id))
        except CastError:
            pass

    # -- wire -----------------------------------------------------------
    def _send(self, namespace: str, dest: str, payload: str) -> None:
        sock = self._sock
        if sock is None:
            raise CastError("not connected")
        body = proto.encode(proto.SENDER_ID, dest, namespace, payload)
        try:
            _send_frame(sock, self._write_lock, body)
        except Exception as exc:
            self._fire_closed(f"write failed: {exc}")
            raise CastError(str(exc)) from exc

    def _read_loop(self) -> None:
        sock = self._sock
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                size_raw = _recv_exact(sock, 4)
                (size,) = struct.unpack(">I", size_raw)
                if size <= 0 or size > 64 * 1024:
                    break
                body = _recv_exact(sock, size)
            except socket.timeout:
                continue
            except Exception:
                break
            parsed = proto.decode(body)
            if parsed is None:
                continue
            _source, _dest, namespace, payload = parsed
            self._handle(namespace, payload)
        self._fire_closed("speaker went away")

    def _handle(self, namespace: str, payload: str) -> None:
        if namespace == proto.NS_HEARTBEAT and "PING" in payload:
            try:
                self._send(proto.NS_HEARTBEAT, proto.RECEIVER_ID,
                           proto.pong_payload())
            except CastError:
                pass
            return
        if namespace == proto.NS_RECEIVER:
            info = proto.parse_receiver_status(payload)
            if "level" in info:
                self.volume = min(1.0, max(0.0, float(info["level"])))
            if "muted" in info:
                self.muted = bool(info["muted"])
            if info.get("level") is not None or \
                    info.get("muted") is not None:
                self._emit_volume()
            transport = info.get("transportId", "")
            if transport and transport != proto.RECEIVER_ID:
                self._last_transport = transport
                self._transport_event.set()
            return
        if namespace == proto.NS_MEDIA or "MEDIA_STATUS" in payload:
            info = proto.parse_media_status(payload)
            if not info and "MEDIA_STATUS" not in payload:
                return
            if "mediaSessionId" in info:
                self.media_session_id = int(info["mediaSessionId"])
            if "positionMs" in info:
                self.position_ms = int(info["positionMs"])
            if "durationMs" in info:
                self.duration_ms = int(info["durationMs"])
            if "mediaId" in info and info["mediaId"]:
                self.content_id = str(info["mediaId"])
            if "playing" in info:
                self.playing = bool(info["playing"])
            elif proto.is_finished_payload(payload):
                self.playing = False
            elif '"PLAYING"' in payload:
                self.playing = True
            self._emit_media()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(HEARTBEAT_S):
            try:
                self._send(proto.NS_HEARTBEAT, proto.RECEIVER_ID,
                           proto.ping_payload())
            except CastError:
                break

    def _poll_loop(self) -> None:
        while not self._stop.wait(POLL_S):
            if self._transport_id != proto.RECEIVER_ID:
                self.refresh()

    def _fire_closed(self, reason: str) -> None:
        if self._stop.is_set() and self._closed_fired:
            return
        if self._closed_fired:
            return
        self._closed_fired = True
        self._stop.set()
        callback = self.on_closed
        if callback is not None:
            try:
                callback(reason)
            except Exception:  # noqa: BLE001 - UI callbacks never break IO
                pass

    def _emit_media(self) -> None:
        callback = self.on_media
        if callback is not None:
            try:
                callback(self.content_id or None, self.position_ms,
                         self.duration_ms, self.playing)
            except Exception:  # noqa: BLE001 - UI callbacks never break IO
                pass

    def _emit_volume(self) -> None:
        callback = self.on_volume
        if callback is not None:
            try:
                callback(self.volume, self.muted)
            except Exception:  # noqa: BLE001 - UI callbacks never break IO
                pass
