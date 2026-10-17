"""The LAN speaker server: tokens, single item, lifetime. Loopback only."""

import socket
import urllib.error
import urllib.request

import pytest

from xx_music.cast.server import (CastHttpServer, is_cgnat,
                                  is_skipped_interface, pick_lan_ipv4)

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


@pytest.fixture()
def server(tmp_path):
    owned = CastHttpServer()
    owned.start("127.0.0.1", allow_loopback=True)
    yield owned
    owned.stop()


def _write(path, data: bytes):
    path.write_bytes(data)
    return path


def _get(url, start=None, end=None, method="GET"):
    request = urllib.request.Request(url, method=method)
    if start is not None:
        request.add_header(
            "Range", f"bytes={start}-{'' if end is None else end}")
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, dict(response.headers), response.read()


def test_serves_ranges_and_head(server, tmp_path):
    data = bytes(range(256)) * 4
    url = server.set_item("tr-001",
                          _write(tmp_path / "tr-001", data), "audio/ogg")
    assert _get(url)[2] == data
    status, headers, body = _get(url, 10, 29)
    assert status == 206
    assert body == data[10:30]
    assert headers["Content-Range"] == f"bytes 10-29/{len(data)}"
    status, _, body = _get(url, method="HEAD")
    assert status == 200
    assert body == b""


def test_token_required_and_no_listing(server, tmp_path):
    url = server.set_item("tr-001",
                          _write(tmp_path / "tr-001", b"abc"),
                          "audio/mpeg")
    root = url.rsplit("/t/", 1)[0] + "/"
    for bad in (root, root + "t/", url + "x", url.rsplit("/", 1)[0]):
        with pytest.raises(urllib.error.HTTPError) as caught:
            _get(bad)
        assert caught.value.code == 404


def test_only_the_current_item_is_served(server, tmp_path):
    first = server.set_item("tr-001",
                            _write(tmp_path / "tr-001", b"one"),
                            "audio/mpeg")
    assert _get(first)[2] == b"one"
    second = server.set_item("tr-002",
                             _write(tmp_path / "tr-002", b"two"),
                             "audio/mpeg")
    assert first != second
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(first)
    assert caught.value.code == 404
    assert _get(second)[2] == b"two"
    server.clear()
    with pytest.raises(urllib.error.HTTPError) as caught:
        _get(second)
    assert caught.value.code == 404


def test_stopped_server_refuses_connections(tmp_path):
    owned = CastHttpServer()
    owned.start("127.0.0.1", allow_loopback=True)
    url = owned.set_item("tr-001",
                         _write(tmp_path / "tr-001", b"abc"),
                         "audio/mpeg")
    assert _get(url)[2] == b"abc"
    owned.stop()
    with pytest.raises(Exception):
        _get(url)


def test_bind_rules():
    owned = CastHttpServer()
    with pytest.raises(ValueError):
        owned.start("0.0.0.0", allow_loopback=True)
    with pytest.raises(ValueError):
        owned.start("127.0.0.1")
    with pytest.raises(ValueError):
        owned.start("not-an-ip", allow_loopback=True)


def test_lan_helpers():
    assert is_cgnat("100.64.0.9")
    assert is_cgnat("100.127.9.9")
    assert not is_cgnat("192.168.1.9")
    assert is_skipped_interface("tun0")
    assert is_skipped_interface("tailscale0")
    assert not is_skipped_interface("wlan0")
    rows = [("tailscale0", "100.64.0.2"), ("wlan0", "192.168.1.9"),
            ("eth0", "192.168.1.10")]
    assert pick_lan_ipv4(rows) == "192.168.1.9"
    assert pick_lan_ipv4([("tun0", "10.9.0.2"),
                          ("tailscale0", "100.64.0.2")]) is None
