"""Player service units: dispatch, sequencing, errors, signals, persistence.

Synchronous doubles (inline executor, immediate idle_add, recording
scheduler and bus) so no main loop runs here; the live round trip over a
private bus lives in test_player_bus.py.
"""

import json
import shutil

import pytest
from gi.repository import Gio, GLib

from xx_music import engine as e
from xx_music.appspec import AppSpec
from xx_music.fake_engine import FakeEngine
from xx_music.player import actions as actions_mod
from xx_music.player import api as api_mod
from xx_music.player import mpris as mpris_mod
from xx_music.player.service import InlineExecutor, PlayerService
from xx_radio.main import SPEC

from player_fakes import FakeClock, FakeProxy, bake_ogg, fakesink, make_track


class FakeParams:
    def __init__(self, values=()):
        self._values = tuple(values)

    def unpack(self):
        return self._values


class FakeInvocation:
    def __init__(self):
        self.reply = None
        self.error = None

    def return_value(self, parameters):
        self.reply = parameters

    def return_dbus_error(self, name, message):
        self.error = (name, message)


class FakeConnection:
    def __init__(self):
        self.objects = []
        self.signals = []
        self.calls = []
        self.owned = []

    def register_object(self, path, iface, handler):
        self.objects.append((path, iface.name, handler))
        return len(self.objects)

    def emit_signal(self, dest, path, iface, signal, params):
        self.signals.append((dest, path, iface, signal, params))
        return True

    def call(self, *args):
        self.calls.append(args)
        return 0


def _derived_spec():
    return AppSpec(
        app_id="org.example.Player", name="Example",
        dir_name="example-player", mpris_suffix="example_player",
        engine_factory=lambda _ctx: FakeEngine(),
        player_actions=SPEC.player_actions)


def _media(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    for i in range(1, 5):
        bake_ogg(media / f"tr-{i:03d}.ogg", freq=400 + i * 60)
    return media


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


def _service(spec, engine, proxy, clock=None):
    clock = clock or FakeClock()
    scheduled: list = []
    service = PlayerService(
        spec, engine, proxy, clock=clock,
        idle_add=lambda fn, *args: fn(*args),
        schedule=lambda ms, fn: scheduled.append((ms, fn)) or 0,
        executor=InlineExecutor(), audio_sink=fakesink(),
        own_name=lambda conn, name, *a: service_conn_owned(conn, name))
    _SERVICES.append(service)
    return service, scheduled


def service_conn_owned(conn, name):
    conn.owned.append(name)
    return len(conn.owned)


def _attached(spec, tmp_path, ids=("tr-001", "tr-002")):
    media = _media(tmp_path)
    proxied = tmp_path / "proxied"
    proxied.mkdir()
    for track_id in ids:
        shutil.copy(media / f"{track_id}.ogg", proxied / f"{track_id}.ogg")
    engine = FakeEngine(media_dir=media)
    proxy = FakeProxy({t: proxied / f"{t}.ogg" for t in ids})
    clock = FakeClock()
    service, scheduled = _service(spec, engine, proxy, clock)
    conn = FakeConnection()
    service.attach(conn)
    return service, scheduled, conn, engine, proxy, clock


def _call(service, method, *args):
    invocation = FakeInvocation()
    service._on_player1_call(
        None, None, service.spec.player_object_path,
        service.spec.player_interface, method, FakeParams(args), invocation)
    return invocation


def _tracks(*ids):
    return [make_track(t) for t in ids]


# -- introspection -----------------------------------------------------------------


def test_player1_xml_matches_the_spec():
    node = Gio.DBusNodeInfo.new_for_xml(api_mod.player_xml(SPEC))
    assert len(node.interfaces) == 1
    iface = node.interfaces[0]
    assert iface.name == "io.piercingxx.XXRadio.Player1"
    methods = {m.name: m for m in iface.methods}
    assert {m.name for m in iface.methods} == {
        "Play", "StartRadio", "Enqueue", "PlayAt", "Remove", "Move",
        "SetShuffle", "SetRepeat", "SetSleep", "Rate", "Download",
        "RemoveDownload", "Snapshot", "Queue"}
    assert [a.signature for a in methods["Play"].in_args] == ["s", "i", "s"]
    assert [a.signature for a in methods["Enqueue"].in_args] == ["s", "b"]
    assert [a.signature for a in methods["Move"].in_args] == ["i", "i"]
    assert [a.signature for a in methods["SetShuffle"].in_args] == ["b"]
    assert [a.signature for a in methods["SetRepeat"].in_args] == ["s"]
    assert [a.signature for a in methods["SetSleep"].in_args] == ["i"]
    assert [a.signature for a in methods["Rate"].in_args] == ["s", "s"]
    assert [a.signature for a in methods["Snapshot"].out_args] == ["s"]
    assert [a.signature for a in methods["Queue"].out_args] == \
        ["u", "s", "i"]
    signals = {s.name: s for s in iface.signals}
    assert {a.signature for a in signals["Changed"].args} == {"s"}
    assert {a.signature for a in signals["QueueChanged"].args} == {"u"}
    assert {a.signature for a in signals["DownloadsChanged"].args} == {"u"}
    assert {a.signature for a in signals["Problem"].args} == {"s"}


def test_mpris_and_actions_xml():
    node = Gio.DBusNodeInfo.new_for_xml(mpris_mod.MPRIS_XML)
    ifaces = {i.name: i for i in node.interfaces}
    assert set(ifaces) == {"org.mpris.MediaPlayer2",
                           "org.mpris.MediaPlayer2.Player",
                           "org.freedesktop.DBus.Properties"}
    player = ifaces["org.mpris.MediaPlayer2.Player"]
    assert {m.name for m in player.methods} >= {
        "Next", "Previous", "Pause", "PlayPause", "Play", "Stop",
        "Seek", "SetPosition", "OpenUri"}
    assert {p.name for p in player.properties} >= {
        "PlaybackStatus", "LoopStatus", "Shuffle", "Metadata",
        "Position", "Volume"}
    assert {s.name for s in player.signals} == {"Seeked"}
    actions = Gio.DBusNodeInfo.new_for_xml(actions_mod.ACTIONS_XML)
    methods = {m.name: m for m in actions.interfaces[0].methods}
    assert [a.signature for a in methods["ListActions"].out_args] == \
        ["a(sssb)"]
    assert [a.signature for a in methods["ActivateAction"].in_args] == ["s"]


def test_derived_app_never_collides(tmp_path):
    other = _derived_spec()
    assert other.player_interface == "org.example.Player.Player1"
    assert other.player_object_path == "/org/example/Player/Player"
    assert other.mpris_bus_name == "org.mpris.MediaPlayer2.example_player"
    service, _, conn, _, _, _ = _attached(other, tmp_path)
    paths = {path for path, _, _ in conn.objects}
    assert other.player_object_path in paths
    assert "/io/piercingxx/XXRadio/Player" not in paths
    assert set(conn.owned) == {other.player_bus_name, other.mpris_bus_name}
    node = Gio.DBusNodeInfo.new_for_xml(api_mod.player_xml(other))
    assert node.interfaces[0].name == other.player_interface


def test_attach_registers_all_paths_and_names(tmp_path):
    service, _, conn, _, _, _ = _attached(SPEC, tmp_path)
    by_path = {}
    for path, name, _handler in conn.objects:
        by_path.setdefault(path, set()).add(name)
    assert by_path[SPEC.player_object_path] == {SPEC.player_interface}
    assert by_path["/org/mpris/MediaPlayer2"] >= {
        "org.mpris.MediaPlayer2", "org.mpris.MediaPlayer2.Player",
        "org.freedesktop.DBus.Properties",
        "io.piercingxx.XXAuto.Actions1"}
    assert set(conn.owned) == {SPEC.player_bus_name, SPEC.mpris_bus_name}
    assert SPEC.player_bus_name != SPEC.app_id


# -- dispatch ----------------------------------------------------------------------------


def test_play_uses_proxy_uris_only(tmp_path):
    service, _, conn, engine, proxy, _ = _attached(SPEC, tmp_path)
    invocation = _call(service, "Play", api_mod.tracks_to_json(
        _tracks("tr-001", "tr-002")), 0, "{}")
    assert invocation.error is None
    assert service.status == "Playing"
    assert service.queue.current().id == "tr-001"
    assert proxy.resolve_calls == ["tr-001", "tr-002"]
    assert engine.calls.count("resolve") == 2
    assert service.player.uri == proxy.files["tr-001"].as_uri()
    assert "media" not in service.player.uri


def test_queue_and_snapshot_round_trip(tmp_path):
    service, _, _, _, _, _ = _attached(SPEC, tmp_path)
    _call(service, "Play", api_mod.tracks_to_json(_tracks("tr-001")), 0,
          json.dumps({"type": "album", "title": "Album"}))
    invocation = _call(service, "Queue")
    version, payload, index = invocation.reply.unpack()
    assert (version, index) == (service.queue.version, 0)
    assert [t["id"] for t in json.loads(payload)] == ["tr-001"]
    snap = _call(service, "Snapshot")
    state = json.loads(snap.reply.unpack()[0])
    assert state["status"] == "Playing"
    assert state["track"]["id"] == "tr-001"
    assert state["origin"] == {"type": "album", "title": "Album"}
    assert state["problem"] is None


def test_bad_arguments_are_invalid_args(tmp_path):
    service, _, _, _, _, _ = _attached(SPEC, tmp_path)
    assert _call(service, "Play", "nope", 0, "{}").error[0].endswith(
        "InvalidArgs")
    assert _call(service, "PlayAt", 9).error[0].endswith("InvalidArgs")
    assert _call(service, "SetRepeat", "everything").error[0].endswith(
        "InvalidArgs")
    assert _call(service, "SetSleep", -5).error[0].endswith("InvalidArgs")
    assert _call(service, "Rate", "tr-001", "adore").error[0].endswith(
        "InvalidArgs")
    assert _call(service, "Download", "{}").error[0].endswith("InvalidArgs")
    unknown = FakeInvocation()
    service._on_player1_call(None, None, "", "", "B-Side", FakeParams(()),
                             unknown)
    assert unknown.error[0].endswith("UnknownMethod")


def test_enqueue_move_remove_and_shuffle_repeat(tmp_path):
    service, _, _, _, _, _ = _attached(SPEC, tmp_path)
    _call(service, "Play", api_mod.tracks_to_json(_tracks("tr-001")), 0, "{}")
    _call(service, "Enqueue", api_mod.tracks_to_json(
        _tracks("tr-002", "tr-001")), True)
    assert [t.id for t in service.queue.tracks] == ["tr-001", "tr-002"]
    _call(service, "Move", 1, 0)
    assert service.queue.tracks[0].id == "tr-002"
    _call(service, "SetShuffle", True)
    assert service.queue.shuffle
    _call(service, "SetRepeat", "all")
    assert service.queue.repeat == "all"
    _call(service, "Remove", 0)
    assert len(service.queue.tracks) == 1


# -- sequencing -----------------------------------------------------------------------------


def test_eos_advances_wraps_and_stops(tmp_path):
    service, _, _, _, _, _ = _attached(SPEC, tmp_path)
    service.play(_tracks("tr-001", "tr-002"))
    service._on_eos()
    assert service.queue.current().id == "tr-002"
    service._on_eos()
    assert service.status == "Stopped"
    service.set_repeat("all")
    service.play(_tracks("tr-001", "tr-002"))
    service.play_at(1)
    service._on_eos()
    assert service.queue.current().id == "tr-001"
    service.set_repeat("one")
    service._on_eos()
    assert service.queue.current().id == "tr-001"


def test_gapless_hands_next_uri_to_playbin(tmp_path):
    service, _, _, _, _, _ = _attached(SPEC, tmp_path)
    service.play(_tracks("tr-001", "tr-002"))
    next_uri = service._prefetched.get("tr-002")
    assert next_uri
    service._on_about_to_finish()
    assert service.player._next_uri == next_uri


def test_sleep_fade_applies_to_playbin(tmp_path):
    service, _, _, _, _, clock = _attached(SPEC, tmp_path)
    service.play(_tracks("tr-001"))
    service.set_sleep(60)
    clock.advance(55)
    service._tick()
    volume = service.player._playbin.get_property("volume")
    assert volume == pytest.approx(0.5)
    snapshot = service.snapshot_dict()
    assert snapshot["sleep"]["remaining_s"] == pytest.approx(5.0)


def test_gone_song_drops_and_problem_when_empty(tmp_path):
    service, _, conn, engine, proxy, _ = _attached(SPEC, tmp_path)
    proxy.fail["tr-001"] = e.EngineError(e.ErrorKind.NOT_FOUND, "gone")
    service.play(_tracks("tr-001", "tr-002"))
    assert [t.id for t in service.queue.tracks] == ["tr-002"]
    assert service.queue.current().id == "tr-002"
    proxy.fail["tr-002"] = e.EngineError(e.ErrorKind.NOT_FOUND, "gone")
    service.play(_tracks("tr-002"))
    assert service.status == "Stopped"
    problems = [s for s in conn.signals if s[3] == "Problem"]
    assert problems
    _dest, _path, _iface, _sig, params = problems[-1]
    assert params.unpack() == ("not-found", e.ERROR_TEXT[
        e.ErrorKind.NOT_FOUND])


def test_retry_then_skip_keeps_the_song(tmp_path):
    service, scheduled, _, _, proxy, _ = _attached(SPEC, tmp_path)
    proxy.fail["tr-001"] = e.EngineError(e.ErrorKind.TIMEOUT, "slow")
    service.play(_tracks("tr-001", "tr-002"))
    assert service.queue.current().id == "tr-001"
    retries = [fn for ms, fn in scheduled if ms in (500, 1000)]
    assert retries, "a retry must be scheduled"
    proxy.fail["tr-001"] = e.EngineError(e.ErrorKind.TIMEOUT, "slow")
    scheduled.clear()
    retries[0]()
    assert service.queue.current().id == "tr-001"


def test_pause_kinds_show_state_without_walking(tmp_path):
    service, _, _, _, proxy, _ = _attached(SPEC, tmp_path)
    proxy.fail["tr-001"] = e.EngineError(e.ErrorKind.HELPER_MISSING, "setup")
    service.play(_tracks("tr-001", "tr-002"))
    assert service.status == "Paused"
    assert service.queue.current().id == "tr-001"
    assert service._problem["kind"] == "helper-missing"


# -- radio, ratings, offline -----------------------------------------------------------------------


def test_start_radio_extends_from_seed(tmp_path):
    service, _, _, engine, _, _ = _attached(SPEC, tmp_path)
    service.start_radio(make_track("tr-001"))
    assert service.queue.origin == {"type": "radio", "seed": "tr-001"}
    assert "radio" in engine.calls
    assert len(service.queue.tracks) > 1
    ids = [t.id for t in service.queue.tracks]
    assert ids.count("tr-001") == 1


def test_dislike_leaves_queue_and_radio(tmp_path):
    service, _, conn, engine, _, _ = _attached(SPEC, tmp_path)
    service.play(_tracks("tr-001", "tr-002", "tr-003"))
    service.rate("tr-002", "dislike")
    assert engine.ratings.get("tr-002") == e.Rating.DISLIKE
    assert "tr-002" not in service.queue.ids()
    assert "tr-002" in service._disliked
    changed = [s for s in conn.signals
               if s[2] == "org.freedesktop.DBus.Properties"]
    assert changed, "xx-auto must re-read actions after a rating"


def test_offline_mix_keeps_cached_songs(tmp_path):
    service, _, _, _, proxy, _ = _attached(SPEC, tmp_path)
    service.play(_tracks("tr-001", "tr-002"))
    proxy.cached = {"tr-002"}
    service._switch_to_offline_mix()
    assert service._offline
    assert [t.id for t in service.queue.tracks] == ["tr-002"]


def test_offline_mix_with_nothing_cached_pauses(tmp_path):
    service, _, _, _, proxy, _ = _attached(SPEC, tmp_path)
    service.play(_tracks("tr-001"))
    proxy.cached = set()
    service._switch_to_offline_mix()
    assert service.status == "Paused"
    assert service._problem["kind"] == "offline"


# -- signals, persistence, idle ------------------------------------------------------------------------------


def test_changed_coalesces_to_four_per_second(tmp_path):
    service, scheduled, conn, _, _, _ = _attached(SPEC, tmp_path)
    service.play(_tracks("tr-001"))
    first = len([s for s in conn.signals if s[3] == "Changed"])
    assert first == 1
    service._announce()
    service._announce()
    changed = [s for s in conn.signals if s[3] == "Changed"]
    assert len(changed) == 1
    cooldowns = [fn for ms, fn in scheduled if ms == 250]
    assert cooldowns
    for fn in cooldowns:
        fn()
    assert len([s for s in conn.signals if s[3] == "Changed"]) == 2


def test_queue_file_restores_paused(tmp_path):
    service, _, _, _, _, _ = _attached(SPEC, tmp_path)
    service.play(_tracks("tr-001", "tr-002"))
    queue_path = service.paths.queue_path()
    assert queue_path.is_file()
    engine = FakeEngine(media_dir=tmp_path / "media")
    proxy = FakeProxy({})
    clock = FakeClock()
    revived, _ = _service(SPEC, engine, proxy, clock)
    assert [t.id for t in revived.queue.tracks] == ["tr-001", "tr-002"]
    assert revived.status == "Paused"


def test_check_idle_matrix(tmp_path):
    service, _, _, _, _, clock = _attached(SPEC, tmp_path)
    service.play(_tracks("tr-001"))
    assert service.check_idle(False, clock() + 10) is None
    assert service.check_idle(True, clock() + 9999) is None
    service.pause()
    assert service.check_idle(False, clock() + 60) is None
    assert service.check_idle(False, clock() + 30 * 60) == "save-exit"
    service.stop()
    assert service.check_idle(False, clock() + 59) is None
    assert service.check_idle(False, clock() + 60) == "exit"


def test_main_without_proxy_exits_cleanly(monkeypatch, tmp_path):
    import xx_music.player.service as service_mod
    monkeypatch.setattr(service_mod, "build_proxy",
                        lambda spec: (_ for _ in ()).throw(
                            ImportError("no streamproxy")))
    with pytest.raises(SystemExit, match="radio_m2"):
        service_mod.main(_derived_spec())


def test_dbus_probe_calls_use_variant_reply_types(tmp_path):
    service, _, conn, _, _, _ = _attached(SPEC, tmp_path)
    service._idle_poll()
    service._raise_window()
    kinds = [args[5] for args in conn.calls]
    assert kinds
    for reply_type in kinds:
        assert isinstance(reply_type, GLib.VariantType), reply_type
