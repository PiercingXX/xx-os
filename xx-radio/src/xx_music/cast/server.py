"""Serve the current cast item to a speaker on the LAN.

Only stdlib. One token path is valid at a time; setting a new item
invalidates the last one. The server binds a single LAN address and
runs only for the length of a cast session.
"""

from __future__ import annotations

import ipaddress
import secrets
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def is_cgnat(ip: str) -> bool:
    parts = (ip or "").strip().split(".")
    if len(parts) != 4:
        return False
    try:
        first, second = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return first == 100 and 64 <= second <= 127


def is_skipped_interface(name: str) -> bool:
    lowered = (name or "").lower()
    return (lowered.startswith(("tun", "ppp", "rmnet", "dummy"))
            or "tailscale" in lowered or "vpn" in lowered
            or "wg" in lowered)


def pick_lan_ipv4(rows: list[tuple[str, str]]) -> str | None:
    scored: list[tuple[int, str]] = []
    for name, ip in rows or []:
        if not ip or is_cgnat(ip):
            continue
        if is_skipped_interface(name):
            continue
        try:
            parsed = ipaddress.ip_address(ip.strip())
        except ValueError:
            continue
        if not isinstance(parsed, ipaddress.IPv4Address):
            continue
        if parsed.is_loopback or parsed.is_link_local or \
                parsed.is_multicast:
            continue
        lowered = (name or "").lower()
        if lowered.startswith(("wlan", "wifi")):
            score = 0
        elif lowered.startswith(("eth", "en")):
            score = 1
        elif lowered.startswith(("ap", "swlan")):
            score = 2
        else:
            score = 3
        scored.append((score, ip.strip()))
    if not scored:
        return None
    return sorted(scored)[0][1]


def lan_address(preferred: str | None = None) -> str | None:
    choice = (preferred or "").strip()
    if choice and not is_cgnat(choice):
        try:
            parsed = ipaddress.ip_address(choice)
        except ValueError:
            pass
        else:
            if isinstance(parsed, ipaddress.IPv4Address) and \
                    not parsed.is_loopback:
                return choice
    rows: list[tuple[str, str]] = []
    try:
        for _index, name in socket.if_nameindex():
            try:
                for info in socket.getaddrinfo(
                        None, 0, socket.AF_INET, socket.SOCK_STREAM):
                    _family, _kind, _proto, _canon, sockaddr = info
                    _ = sockaddr
            except OSError:
                continue
            _ = name
    except OSError:
        pass
    try:
        hostname = socket.gethostname()
        for family, _kind, _proto, _canon, sockaddr in socket.getaddrinfo(
                hostname, None, socket.AF_INET, socket.SOCK_STREAM):
            if family != socket.AF_INET:
                continue
            rows.append(("", sockaddr[0]))
    except OSError:
        pass
    seen: dict[str, str] = {}
    for name, ip in rows:
        seen.setdefault(ip, name)
    return pick_lan_ipv4([(name, ip) for ip, name in seen.items()])


def sniff_type(path: Path) -> str:
    try:
        with open(path, "rb") as handle:
            head = handle.read(12)
    except OSError:
        return "audio/mpeg"
    if len(head) >= 4 and head[0] == 0x1A and head[1] == 0x45:
        return "audio/webm"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return "audio/mp4"
    if len(head) >= 4 and head[0:4] == b"OggS":
        return "audio/ogg"
    if len(head) >= 3 and head[0:3] == b"ID3":
        return "audio/mpeg"
    return "audio/mpeg"


def _check_bind_ip(lan_ip: str, allow_loopback: bool) -> str:
    ip = (lan_ip or "").strip()
    if not ip:
        raise ValueError("a LAN address is required")
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError as exc:
        raise ValueError(f"bad LAN address: {ip!r}") from exc
    if not isinstance(parsed, ipaddress.IPv4Address):
        raise ValueError("the speaker needs an IPv4 LAN address")
    if ip == "0.0.0.0" or parsed.is_multicast or parsed.is_unspecified:
        raise ValueError("refusing a wildcard bind for cast")
    if parsed.is_loopback and not allow_loopback:
        raise ValueError("refusing a loopback bind for cast")
    if parsed.is_link_local:
        raise ValueError("refusing a link-local bind for cast")
    return ip


def _parse_token(target: str) -> str:
    path = urllib.parse.urlparse(target).path
    head, _, token = path.partition("/t/")
    if head != "" or not token or "/" in token:
        return ""
    return token


class CastHttpServer:
    """One-item HTTP server for a speaker. Created per cast session."""

    def __init__(self) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._lan_ip = ""
        self._port = 0
        self._token = ""
        self._track_id = ""
        self._path: Path | None = None
        self._mime = ""

    @property
    def port(self) -> int:
        return self._port

    @property
    def active(self) -> bool:
        return self._server is not None

    @property
    def current_track_id(self) -> str:
        with self._lock:
            return self._track_id

    def start(self, lan_ip: str, allow_loopback: bool = False) -> int:
        if self._server is not None:
            return self._port
        ip = _check_bind_ip(lan_ip, allow_loopback)
        server = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802
                server._serve(self, head_only=False)

            def do_HEAD(self) -> None:  # noqa: N802
                server._serve(self, head_only=True)

        httpd = ThreadingHTTPServer((ip, 0), _Handler)
        httpd.daemon_threads = True
        self._server = httpd
        self._lan_ip = ip
        self._port = httpd.server_address[1]
        worker = threading.Thread(target=httpd.serve_forever,
                                  kwargs={"poll_interval": 0.05},
                                  daemon=True, name="xx-cast-http")
        worker.start()
        self._thread = worker
        return self._port

    def set_item(self, track_id: str, path: Path | str,
                 mime: str = "") -> str:
        if self._server is None:
            raise RuntimeError("server is not started")
        if not track_id or "/" in track_id or "\\" in track_id \
                or ".." in track_id:
            raise ValueError("bad track id")
        file_path = Path(path)
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._token = token
            self._track_id = track_id
            self._path = file_path
            self._mime = mime or sniff_type(file_path)
        return f"http://{self._lan_ip}:{self._port}/t/{token}"

    def clear(self) -> None:
        with self._lock:
            self._token = ""
            self._track_id = ""
            self._path = None
            self._mime = ""

    def stop(self) -> None:
        server, worker = self._server, self._thread
        self._server = None
        self._thread = None
        self._port = 0
        self.clear()
        if server is not None:
            server.shutdown()
            server.server_close()
        if worker is not None:
            worker.join(timeout=5)

    # -- internals ------------------------------------------------------
    def _serve(self, handler: BaseHTTPRequestHandler,
               head_only: bool) -> None:
        token = _parse_token(handler.path)
        with self._lock:
            current = self._token
            path = self._path
            mime = self._mime or "audio/mpeg"
        if not token or token != current or path is None:
            self._send_text(handler, 404, "not found")
            return
        if not path.is_file():
            self._send_text(handler, 404, "not cached")
            return
        total = path.stat().st_size
        first, last = self._wanted(handler.headers.get("Range"), total)
        if first is None:
            handler.send_response(416)
            handler.send_header("Content-Range", f"bytes */{total}")
            handler.send_header("Content-Length", "0")
            handler.send_header("Connection", "close")
            handler.end_headers()
            return
        length = last - first + 1
        ranged = handler.headers.get("Range") is not None
        handler.send_response(206 if ranged else 200)
        handler.send_header("Content-Type", mime)
        handler.send_header("Accept-Ranges", "bytes")
        if ranged:
            handler.send_header("Content-Range",
                                f"bytes {first}-{last}/{total}")
        handler.send_header("Content-Length", str(length))
        handler.send_header("Connection", "close")
        handler.end_headers()
        if head_only or length <= 0:
            return
        try:
            with open(path, "rb") as handle:
                handle.seek(first)
                remaining = length
                while remaining > 0:
                    piece = handle.read(min(65536, remaining))
                    if not piece:
                        break
                    handler.wfile.write(piece)
                    remaining -= len(piece)
        except (OSError, ValueError):
            pass

    @staticmethod
    def _wanted(value: str | None, total: int
                ) -> tuple[int | None, int]:
        if total <= 0:
            return 0, -1
        if not value or not value.strip().lower().startswith("bytes="):
            return 0, total - 1
        spec = value.strip()[6:].strip()
        if spec.startswith("-"):
            try:
                count = int(spec[1:])
            except ValueError:
                return 0, total - 1
            count = min(max(count, 1), total)
            return total - count, total - 1
        first_text, sep, last_text = spec.partition("-")
        if not sep:
            return 0, total - 1
        try:
            first = int(first_text)
        except ValueError:
            return 0, total - 1
        if first >= total:
            return None, total - 1
        if not last_text:
            return max(first, 0), total - 1
        try:
            last = int(last_text)
        except ValueError:
            return max(first, 0), total - 1
        return max(first, 0), min(max(last, first), total - 1)

    @staticmethod
    def _send_text(handler: BaseHTTPRequestHandler, code: int,
                   text: str) -> None:
        body = text.encode()
        handler.send_response(code)
        handler.send_header("Content-Type", "text/plain")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        if handler.command != "HEAD":
            try:
                handler.wfile.write(body)
            except (OSError, ValueError):
                pass
