"""Zones against the fake HA endpoints: cast, transport, bring-back."""

import json

from xx_audiobook import api as api_module
from xx_audiobook import zones as zones_module
from xx_audiobook.api import AudiobookApi
from xx_audiobook.zones import Phone, ZoneOutput, ZonesState, ZoneRepository


def _repo(server, transport=None):
    kwargs = {} if transport is None else {"transport": transport}
    return ZoneRepository(AudiobookApi(server.api_base, "tok", **kwargs))


def _dead_transport(method, url, headers, body, timeout):
    raise api_module.ApiError.Unreachable("off")


def test_refresh_lists_locations_and_targets(fake_server):
    repo = _repo(fake_server)
    rows, error = repo.refresh_locations()
    assert (error, [r.id for r in rows]) == (None, ["loc-home"])
    assert repo.locations[0].name == "Home"
    targets, error = repo.refresh_targets("loc-home")
    assert error is None
    assert [t.entity_id for t in targets] == ["media_player.kitchen"]
    assert isinstance(repo.output, Phone)


def test_cast_play_transport_and_stop(fake_server):
    repo = _repo(fake_server)
    targets, _error = repo.refresh_targets("loc-home")
    zone, error = repo.play(targets[0], "loc-home", "it-1", 30.0)
    assert error is None
    assert isinstance(zone, ZoneOutput)
    assert (zone.entity_id, zone.item_id) == (
        "media_player.kitchen", "it-1")
    assert isinstance(repo.output, ZoneOutput)
    _method, _path, _headers, body, _query = fake_server.last_call(
        "POST", "/audiobook/api/cast/play")
    assert json.loads(body)["position_s"] == 30.0

    result, error = repo.transport("pause")
    assert (result.ok, error) == (True, None)
    assert isinstance(repo.output, ZoneOutput)
    result, error = repo.transport("volume", 0.8)
    assert error is None
    _method, _path, _headers, body, _query = fake_server.last_call(
        "POST", "/audiobook/api/cast/transport")
    assert json.loads(body) == {
        "entity_id": "media_player.kitchen", "action": "volume",
        "location_id": "loc-home", "value": 0.8}

    result, error = repo.transport("stop")
    assert error is None
    assert isinstance(repo.output, Phone)


def test_transport_without_zone_errors(fake_server):
    repo = _repo(fake_server)
    result, error = repo.transport("pause")
    assert result is None
    assert isinstance(error, api_module.ApiError)


def test_bring_back_returns_item_only_after_stop(fake_server):
    repo = _repo(fake_server)
    assert repo.bring_back_to_phone() == (None, None)
    targets, _error = repo.refresh_targets("loc-home")
    repo.play(targets[0], "loc-home", "it-1", 30.0)
    zone, error = repo.bring_back_to_phone()
    assert (zone.item_id, error) == ("it-1", None)
    assert isinstance(repo.output, Phone)


def test_bring_back_failure_keeps_zone_playing(fake_server):
    repo = _repo(fake_server)
    targets, _error = repo.refresh_targets("loc-home")
    repo.play(targets[0], "loc-home", "it-1", 30.0)
    fake_server.queue("POST", "/audiobook/api/cast/transport", 502,
                      '{"detail": "HA is down"}')
    zone, error = repo.bring_back_to_phone()
    assert zone is None
    assert error is not None
    assert isinstance(repo.output, ZoneOutput)


def test_ha_errors_surface_verbatim(fake_server):
    repo = _repo(fake_server)
    fake_server.queue(
        "POST", "/audiobook/api/cast/play", 503,
        '{"reason": "stream_not_configured",'
        ' "detail": "Speech host not configured"}')
    targets, _error = repo.refresh_targets("loc-home")
    zone, error = repo.play(targets[0], "loc-home", "it-1", 0.0)
    assert zone is None
    assert isinstance(error, api_module.Http)
    assert error.code == 503
    assert error.reason == "stream_not_configured"
    assert error.message == "Speech host not configured"
    assert isinstance(repo.output, Phone)


def test_state_select_play_volume_and_bring_back(fake_server):
    state = ZonesState(api=AudiobookApi(fake_server.api_base, "tok"))
    rows, error = state.refresh()
    assert error is None
    snap = state.snapshot()
    assert snap.selected == "loc-home"
    assert snap.message is None
    assert snap.busy is False

    player = state.zone_player()
    assert player is None
    zone, error = state.play_on(snap.targets["loc-home"][0], "it-1",
                                12.0)
    assert error is None
    assert state.snapshot().zone_playing is True
    assert state.snapshot().volume == 0.5
    assert state.zone_player().entity_id == "media_player.kitchen"

    _result, error = state.set_volume(2.0)
    assert error is None
    assert state.snapshot().volume == 1.0
    _method, _path, _headers, body, _query = fake_server.last_call(
        "POST", "/audiobook/api/cast/transport")
    assert json.loads(body)["value"] == 1.0

    item_id, error = state.bring_back_to_phone()
    assert (item_id, error) == ("it-1", None)
    snap = state.snapshot()
    assert isinstance(snap.output, Phone)
    assert snap.volume is None
    assert snap.zone_playing is None


def test_state_play_needs_a_book(fake_server):
    state = ZonesState(api=AudiobookApi(fake_server.api_base, "tok"))
    state.refresh()
    zone, error = state.play_on(
        state.snapshot().targets["loc-home"][0], "", 0.0)
    assert zone is None
    assert error is not None
    assert "Pick a book" in (state.snapshot().message or "")


def test_state_seek_uses_reported_position(fake_server):
    state = ZonesState(api=AudiobookApi(fake_server.api_base, "tok"))
    state.refresh()
    snap = state.snapshot()
    state.play_on(snap.targets["loc-home"][0], "it-1", 12.0)
    fake_server.queue(
        "GET", "/audiobook/api/cast/targets", 200,
        '{"location_id": "loc-home", "targets": [{'
        '"entity_id": "media_player.kitchen", "name": "Kitchen",'
        ' "state": "playing", "volume_level": 0.5,'
        ' "media_title": "The Long Way",'
        ' "media_position_s": 100.0}]}')
    rows, error = state.load_targets("loc-home")
    assert error is None
    assert state.zone_player().media_position_s == 100.0
    result, error = state.seek_by(zones_module.SEEK_STEP_S)
    assert error is None
    _method, _path, _headers, body, _query = fake_server.last_call(
        "POST", "/audiobook/api/cast/transport")
    assert json.loads(body)["value"] == 130.0
