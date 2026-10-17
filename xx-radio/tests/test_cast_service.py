"""Casting through the real player service with fake speaker pieces.

Synchronous doubles (inline executor, immediate idle_add) so no main
loop runs here; the wire lives in test_cast_client.py.
"""

import json
import shutil

import pytest
from gi.repository import GLib

from xx_music.cast.client import CastError
from xx_music.fake_engine import FakeEngine
from xx_music.player.service import InlineExecutor, PlayerService
from xx_radio.main import SPEC

from player_fakes import FakeClock, FakeProxy, bake_ogg, fakesink, make_track


class FakeCastClient:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.loads: list = []
        self.seeks: list = []
        self.volumes: list = []
        self.plays = 0
        self.pauses = 0
        self.stops = 0
        self.attaches = 0
        self.opened = False
        self.closed = False
        self.playing = False
        self.fail_open = False
        self.fail_load = False
        self.on_media = None
        self.on_volume = None
        self.on_closed = None

    def open(self):
        if self.fail_open:
            raise CastError("unreachable")
        self.opened = True

    def ensure_session(self):
        if self.fail_open:
            raise CastError("unreachable")
        return "transport-1"

    def attach(self):
        self.attaches += 1
        self.opened = True
        self.playing = True
        return "transport-1"

    def load(self, url, mime, title, artist="", album="", art_url="",
             position_s=0.0, media_id=""):
        if self.fail_load:
            raise CastError("refused")
        self.loads.append({"url": url, "mime": mime, "title": title,
                           "artist": artist, "album": album,
                           "art": art_url, "position": position_s,
                           "media_id": media_id})
        self.playing = True

    def play(self):
        self.plays += 1
        self.playing = True

    def pause(self):
        self.pauses += 1
        self.playing = False

    def stop_media(self):
        self.stops += 1
        self.playing = False

    def seek(self, position_s):
        self.seeks.append(position_s)
        self.playing = True

    def set_volume(self, level):
        self.volumes.append(level)

    def refresh(self):
        pass

    def close(self):
        self.closed = True


class FakeServer:
    def __init__(self):
        self.lans: list = []
        self.items: list = []
        self.stops = 0
        self._port = 9

    @property
    def port(self):
        return self._port

    def start(self, lan_ip, allow_loopback=False):
        self.lans.append(lan_ip)
        self._port = 8765
        return self._port

    def set_item(self, track_id, path, mime=""):
        self.items.append((track_id, str(path), mime))
        return f"http://192.168.1.9:8765/t/token-{len(self.items)}"

    def clear(self):
        pass

    def stop(self):
        self.stops += 1


class NoopDiscovery:
    def refresh(self):
        pass


class FakeAvahi:
    available = True
    devices: list = []

    def refresh(self):
        pass


_SERVICES: list = []


@pytest.fixture(autouse=True)
def _stop_services():
    yield
    while _SERVICES:
        service = _SERVICES.pop()
        try:
            service.request_stop()
        except Exception:  # noqa: BLE001 - teardown must not fail
            pass


def _service(tmp_path, ids=("tr-001", "tr-002")):
    media = tmp_path / "media"
    media.mkdir(exist_ok=True)
    proxied = tmp_path / "proxied"
    proxied.mkdir(exist_ok=True)
    for track_id in ids:
        bake_ogg(media / f"{track_id}.ogg", freq=440)
        shutil.copy(media / f"{track_id}.ogg",
                    proxied / f"{track_id}.ogg")
    engine = FakeEngine(media_dir=media)
    proxy = FakeProxy({t: proxied / f"{t}.ogg" for t in ids})
    made: list = []
    servers: list = []

    def factory(host, port):
        client = FakeCastClient(host, port)
        made.append(client)
        return client

    def server_factory():
        server = FakeServer()
        servers.append(server)
        return server

    service = PlayerService(
        SPEC, engine, proxy, clock=FakeClock(),
        idle_add=lambda fn, *args: fn(*args),
        schedule=lambda ms, fn: 0,
        executor=InlineExecutor(), audio_sink=fakesink(),
        own_name=lambda conn, name, *a: 0,
        cast_client_factory=factory,
        cast_server_factory=server_factory,
        lan_address_fn=lambda: "192.168.1.9")
    _SERVICES.append(service)
    service._cast_discovery = NoopDiscovery()
    service._cast_avahi = FakeAvahi()
    service._cast_devices = [
        {"id": "dev-1", "name": "Living Room", "host": "192.168.1.40",
         "port": 8009, "model": "Chromecast", "group": False}]
    return service, made, servers


def _tracks(*ids):
    return [make_track(t) for t in ids]


def _cast(service):
    service.play(_tracks("tr-001", "tr-002"))
    service.seek_us(30_000_000)
    service.cast_to("dev-1")
    return service._cast_client


def test_handoff_sends_position_and_pauses_local(tmp_path):
    service, made, servers = _service(tmp_path)
    client = _cast(service)
    assert len(made) == 1
    assert made[0] is client
    assert len(client.loads) == 1
    sent = client.loads[0]
    assert sent["position"] == 30.0
    assert sent["media_id"] == "tr-001"
    assert sent["url"].startswith("http://192.168.1.9:8765/t/")
    assert sent["title"] == "tr-001"
    assert servers[0].lans == ["192.168.1.9"]
    assert service._cast_connected_id == "dev-1"
    assert service._cast_connected_name == "Living Room"
    state = json.loads(service.cast_state_json())
    assert state["connectedId"] == "dev-1"
    assert state["mediaId"] == "tr-001"
    assert service.status == "Playing"


def test_transport_drives_the_speaker(tmp_path):
    service, _made, _servers = _service(tmp_path)
    client = _cast(service)
    service.next()
    assert service.queue.current().id == "tr-002"
    assert client.loads[-1]["media_id"] == "tr-002"
    service.previous()
    assert client.loads[-1]["media_id"] == "tr-001"
    service.seek_us(61_500_000)
    assert client.seeks[-1] == 61.5
    service.pause()
    assert client.pauses
    assert service.status == "Paused"
    service.resume()
    assert client.plays
    assert service.status == "Playing"
    service._set_prop("org.mpris.MediaPlayer2.Player", "Volume",
                      GLib.Variant("d", 0.5))
    assert client.volumes[-1] == 0.5
    assert service.snapshot_dict()["cast"]["connectedId"] == "dev-1"


def test_bring_back_hands_position_to_phone(tmp_path):
    service, _made, _servers = _service(tmp_path)
    _cast(service)
    service._on_cast_media("tr-001", 90_000, 200_000, True)
    service.stop_casting()
    assert service._cast_client is None
    assert service._position_us == 90_000_000
    assert service.status == "Playing"
    assert service.queue.index == 0


def test_finished_speaker_advances_the_queue(tmp_path):
    service, _made, _servers = _service(tmp_path)
    client = _cast(service)
    service._on_cast_media("tr-001", 199_000, 200_000, True)
    assert service.queue.current().id == "tr-001"
    service._on_cast_media("tr-001", 199_500, 200_000, False)
    assert service.queue.current().id == "tr-002"
    assert client.loads[-1]["media_id"] == "tr-002"


def test_speaker_loss_pauses_locally_and_says_so(tmp_path):
    service, _made, servers = _service(tmp_path)
    _cast(service)
    service._cast_lost_at = service._clock()
    service._cast_lost_count = 2
    service._on_cast_closed("boom")
    assert service._cast_client is None
    assert servers[0].stops >= 1
    assert service.status == "Paused"
    assert "speaker" in service._problem["text"].lower()


def test_first_drop_reattaches_instead_of_giving_up(tmp_path):
    service, made, _servers = _service(tmp_path)
    first = _cast(service)
    service._on_cast_closed("blip")
    assert len(made) == 2
    assert made[1].attaches == 1
    assert service._cast_client is made[1]
    assert first is not made[1]


def test_takeover_by_another_sender_pauses(tmp_path):
    service, _made, _servers = _service(tmp_path)
    _cast(service)
    service._on_cast_media("something-else", 10_000, 200_000, True)
    assert service._cast_client is None
    assert service.status == "Paused"
    assert "took over" in service._problem["text"]


def test_cast_needs_a_known_device_and_a_song(tmp_path):
    service, _made, _servers = _service(tmp_path)
    with pytest.raises(ValueError):
        service.cast_to("nope")
    with pytest.raises(ValueError):
        service.cast_to("dev-1")


def test_cast_dbus_surface(tmp_path):
    service, _made, _servers = _service(tmp_path)

    class Params:
        def __init__(self, values=()):
            self._values = tuple(values)

        def unpack(self):
            return self._values

    class Invocation:
        def __init__(self):
            self.reply = None
            self.error = None

        def return_value(self, parameters):
            self.reply = parameters

        def return_dbus_error(self, name, message):
            self.error = (name, message)

    def call(method, *args, iface=None):
        invocation = Invocation()
        service._on_player1_call(
            None, None, service.spec.player_object_path,
            iface or service.spec.player_interface, method,
            Params(args), invocation)
        return invocation

    devices = json.loads(call("ListCastDevices").reply.unpack()[0])
    assert devices[0]["id"] == "dev-1"
    assert call("CastTo", "nope").error[0].endswith("InvalidArgs")
    service.play(_tracks("tr-001"))
    assert call("CastTo", "dev-1").error is None
    state = json.loads(call("CastState").reply.unpack()[0])
    assert state["connectedId"] == "dev-1"
    props = call("Get", service.spec.player_interface, "CastState",
                 iface="org.freedesktop.DBus.Properties")
    assert json.loads(props.reply.unpack()[0])["connectedId"] == "dev-1"
    assert call("StopCasting").error is None
    assert service._cast_client is None


def test_failed_connect_reports_an_error(tmp_path):
    service, made, _servers = _service(tmp_path)
    service.play(_tracks("tr-001", "tr-002"))

    def failing(host, port):
        client = FakeCastClient(host, port)
        client.fail_open = True
        made.append(client)
        return client

    service._cast_client_factory = failing
    service.cast_to("dev-1")
    assert service._cast_client is None
    assert "Living Room" in (service._cast_error or "")
