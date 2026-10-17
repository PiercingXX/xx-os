"""CastMessage framing round-trips and status parsing. No network."""

import json

from xx_music.cast import proto


def test_frame_round_trip():
    payload = proto.receiver_get_status(7)
    body = proto.encode(proto.SENDER_ID, proto.RECEIVER_ID,
                        proto.NS_RECEIVER, payload)
    parsed = proto.decode(body)
    assert parsed == (proto.SENDER_ID, proto.RECEIVER_ID,
                      proto.NS_RECEIVER, payload)


def test_frame_keeps_unicode_payload():
    payload = json.dumps({"type": "LOAD", "title": "Café Møøse"})
    body = proto.encode("sender-0", "transport-3", proto.NS_MEDIA,
                        payload)
    assert proto.decode(body)[3] == payload


def test_decode_rejects_garbage():
    assert proto.decode(b"") is None
    assert proto.decode(b"\xff\xff\xff") is None


def test_receiver_status_parsing():
    payload = json.dumps({"type": "RECEIVER_STATUS", "status": {
        "applications": [{"transportId": "tr-9"}],
        "volume": {"level": 0.4, "muted": True}}})
    assert proto.parse_receiver_status(payload) == {
        "transportId": "tr-9", "level": 0.4, "muted": True}
    assert proto.parse_receiver_status("{}") == {}
    assert proto.parse_receiver_status("nope") == {}


def test_media_status_parsing():
    payload = json.dumps({"type": "MEDIA_STATUS", "status": [{
        "mediaSessionId": 3, "playerState": "PLAYING",
        "currentTime": 12.5,
        "media": {"duration": 200.0,
                  "contentId": "http://10.0.0.2:8080/t/abc",
                  "customData": {"id": "tr-001"}}}]})
    assert proto.parse_media_status(payload) == {
        "mediaSessionId": 3, "positionMs": 12500,
        "durationMs": 200000, "mediaId": "tr-001",
        "playerState": "PLAYING", "playing": True}


def test_media_status_paused_and_idle():
    paused = json.dumps({"type": "MEDIA_STATUS", "status": [{
        "playerState": "PAUSED", "currentTime": 4.0}]})
    assert proto.parse_media_status(paused)["playing"] is False
    idle = json.dumps({"type": "MEDIA_STATUS", "status": [{
        "playerState": "IDLE"}]})
    assert proto.parse_media_status(idle)["playing"] is False
    assert proto.is_finished_payload('{"playerState":"IDLE"}')
    assert proto.is_finished_payload('{"idleReason":"FINISHED"}')
    assert not proto.is_finished_payload('{"playerState":"PLAYING"}')


def test_media_id_falls_back_to_url_tail():
    assert proto.media_id_from_url("http://10.0.0.2:9/t/tr-42?x=1") == \
        "tr-42"
    row = {"customData": {}, "playerState": "PLAYING"}
    media = {"contentId": "http://10.0.0.2:9/t/tr-7"}
    assert proto.media_id_from_status(row, media) == "tr-7"


def test_load_builder_carries_metadata():
    payload = proto.media_load(2, "http://10.0.0.2:9/t/abc",
                               "audio/ogg", "Song", "Singer",
                               "Record", "http://art/1", 30.0,
                               True, "tr-001")
    body = json.loads(payload)
    assert body["type"] == "LOAD"
    assert body["media"]["contentId"] == "http://10.0.0.2:9/t/abc"
    assert body["media"]["metadata"]["title"] == "Song"
    assert body["media"]["metadata"]["artist"] == "Singer"
    assert body["media"]["metadata"]["albumName"] == "Record"
    assert body["media"]["metadata"]["images"] == [{"url": "http://art/1"}]
    assert body["media"]["customData"] == {"id": "tr-001"}
    assert body["currentTime"] == 30.0


def test_volume_and_seek_builders():
    volume = json.loads(proto.receiver_set_volume(4, 0.5, None))
    assert volume["volume"] == {"level": 0.5}
    seek = json.loads(proto.media_seek(5, 9, 61.5))
    assert seek["currentTime"] == 61.5
    assert seek["mediaSessionId"] == 9
