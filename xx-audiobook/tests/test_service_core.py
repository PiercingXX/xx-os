"""Player service core: open/play/sync/sleep/conflict against fake server.

Headless (FakeBackend, in-memory DB); D-Bus wire behavior is covered by
the dbus-run-session drivers (test_mpris.py, test_player_bus.py).
"""

import pytest

from xx_audiobook import __app_id__ as app_id
from xx_audiobook import store as store_module
from xx_audiobook.api import AudiobookApi
from xx_audiobook.player import FakeBackend
from xx_audiobook.service.player_service import (
    MPRIS_BUS,
    PLAYER_BUS,
    PlayerService,
    ServiceError,
)


def _service(server, backend=None):
    db = store_module.Database.in_memory()
    api = AudiobookApi(server.api_base, "tok")
    service = PlayerService(
        db, api, backend or FakeBackend(), local_file=None)
    service.set_session(server.api_base, "tok")
    return db, service


def _drain(service):
    service._queue.join()


def test_bus_names_differ_from_gui_app_id():
    assert PLAYER_BUS == "io.piercingxx.XXAudiobook.Player"
    assert MPRIS_BUS == "org.mpris.MediaPlayer2.xx_audiobook"
    assert PLAYER_BUS != app_id
    assert MPRIS_BUS != app_id


def test_open_streams_with_bearer_and_server_resume(fake_server):
    db, service = _service(fake_server)
    backend = service.engine._backend
    try:
        opened = service.open_book_sync("it-1")
        book, resume_ms, rate = opened
        service.finish_open(opened)
        assert book.item_id == "it-1"
        assert book.session_id == "ps-1"
        assert resume_ms == 4100000
        assert rate == 1.0
        assert service.engine.track_index == 1
        assert backend.uris[0].startswith(fake_server.api_base)
        assert backend.headers[0] == {"Authorization": "Bearer tok"}
        assert [c.title for c in book.timeline.chapters] == ["One", "Two"]
        state = service.state_dict()
        assert state["status"] == "Paused"
        assert state["position_us"] == 4100000 * 1000
        assert state["duration_us"] == 7200000 * 1000
        assert state["has_book"] is True
    finally:
        _drain(service)
        db.close()


def test_offline_open_uses_local_files_without_session():
    import json

    from fake_server import DETAIL_1

    from xx_audiobook import api as api_module
    from xx_audiobook import repos as repos_module

    db = store_module.Database.in_memory()
    try:
        api = AudiobookApi("http://127.0.0.1:9/audiobook/", "tok")
        service = PlayerService(
            db, api, FakeBackend(),
            local_file=lambda item, fid: f"/books/{item}/{fid}.m4b")
        detail = api_module.ItemDetail.from_dict(dict(DETAIL_1))
        db.details().upsert(store_module.ItemDetail(
            "it-1", json.dumps(repos_module.detail_to_wire(detail)),
            99))
        book, resume_ms, rate = service.open_book_sync("it-1")
        assert book.session_id is None
        assert book.timeline.tracks[0].uri == (
            "file:///books/it-1/f-a.m4b")
        assert book.timeline.tracks[1].uri == (
            "file:///books/it-1/f-b.m4b")
        service.finish_open((book, resume_ms, rate))
        assert service.engine._backend.headers[0] == {}
        assert service.engine.status == "Paused"
    finally:
        _drain(service)
        db.close()


def test_open_401_maps_to_not_signed_in():
    db = store_module.Database.in_memory()
    try:
        def transport(method, url, headers, body, timeout):
            return (401, '{"reason": "expired"}')

        api = AudiobookApi("http://127.0.0.1:9/audiobook/", "tok",
                           transport=transport)
        service = PlayerService(db, api, FakeBackend())
        with pytest.raises(ServiceError, match="not signed in"):
            service.open_book_sync("it-1")
    finally:
        db.close()


def test_pause_writes_session_sync_and_sleep_counts(fake_server):
    db, service = _service(fake_server)
    try:
        service.finish_open(service.open_book_sync("it-1"))
        assert service.play() is True
        assert service.pause() is True
        _drain(service)
        assert fake_server.last_call(
            "POST", "/audiobook/api/sessions/ps-1/sync") is not None
        service.sleep_ms(60000)
        assert service.state_dict()["sleep_remaining_ms"] == 60000
        service._sleep_tick()
        assert service.state_dict()["sleep_remaining_ms"] <= 60000
        service.cancel_sleep()
        assert service.state_dict()["sleep_remaining_ms"] == 0
        with pytest.raises(ServiceError):
            service.sleep_ms(0)
    finally:
        _drain(service)
        db.close()


def test_conflict_plumbing_for_the_60s_prompt(fake_server):
    db, service = _service(fake_server)
    try:
        service.finish_open(service.open_book_sync("it-1"))
        service._progress.record("it-1", 100.0, 7200.0)
        variant = service._conflict_variant_sync("it-1")
        (plain,) = variant.unpack()
        assert plain["needs_prompt"] is True
        assert plain["server_s"] == 4100.0
        assert plain["local_s"] == 100.0
        conflict = service._progress.compare_on_open("it-1")
        assert conflict is not None
        service._progress.resolve(conflict, use_server=True)
        assert db.progress().get("it-1").position_s == 4100.0
    finally:
        _drain(service)
        db.close()


def test_rate_persists_per_book_and_caps_publish(fake_server):
    db, service = _service(fake_server)
    try:
        service.finish_open(service.open_book_sync("it-1"))
        assert service.set_rate(9.0) == 3.0
        _drain(service)
        assert db.progress().get("it-1").speed == 3.0
        assert service.engine.snapshot().can_go_next is False
        assert service.engine.snapshot().can_go_previous is True
        service.seek_to_ms(1000)
        assert service.engine.position_global_ms() == 1000
        service.skip_forward()
        assert service.engine.position_global_ms() == 31000
        service.next_chapter()
        assert service.engine.position_global_ms() == 3600000
        service.previous_chapter()
        assert service.engine.position_global_ms() == 0
        assert service.action_rows()[0][0] == "skip-back"
        assert service.action_rows()[1][0] == "bookmark"
        assert service.action_rows()[2][0] == "skip-forward"
        assert service.track_id().endswith("/Track/0")
    finally:
        _drain(service)
        db.close()


def test_mpris_props_shape(fake_server):
    db, service = _service(fake_server)
    try:
        service.finish_open(service.open_book_sync("it-1"))
        root = {k: v.unpack()
                for k, v in service.mpris_root_props().items()}
        assert root["Identity"] == "xx-audiobook"
        assert root["DesktopEntry"] == "io.piercingxx.XXAudiobook"
        assert root["CanQuit"] is True
        player = {k: v.unpack()
                  for k, v in service.mpris_player_props().items()}
        assert player["PlaybackStatus"] == "Paused"
        assert player["CanPlay"] is True
        assert player["CanSeek"] is True
        assert player["MinimumRate"] == 0.5
        assert player["MaximumRate"] == 3.0
        meta = player["Metadata"]
        assert meta["xesam:title"] == "The Long Way"
        assert meta["xesam:artist"] == ["Becky Chambers"]
        assert meta["mpris:length"] == 7200000 * 1000
        assert str(type(player["Position"]).__name__) == "int"
    finally:
        _drain(service)
        db.close()


def test_clear_session_stops_and_drops_bearer(fake_server):
    db, service = _service(fake_server)
    try:
        service.finish_open(service.open_book_sync("it-1"))
        service.play()
        service.clear_session()
        _drain(service)
        assert service.engine.status == "Stopped"
        assert service._stream_auth.headers_for(
            fake_server.api_base + "api/x") == {}
        with pytest.raises(ServiceError):
            service.set_session("", "")
    finally:
        _drain(service)
        db.close()
