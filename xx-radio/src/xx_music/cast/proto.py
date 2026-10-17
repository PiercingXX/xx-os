"""CastMessage framing and message builders for the Cast V2 protocol.

The envelope is a tiny protobuf with six fields; it is hand-encoded so
the core stays dependency-free. Every sender speaks the same bytes.
"""

from __future__ import annotations

import json
import urllib.parse

NS_CONNECTION = "urn:x-cast:com.google.cast.tp.connection"
NS_HEARTBEAT = "urn:x-cast:com.google.cast.tp.heartbeat"
NS_RECEIVER = "urn:x-cast:com.google.cast.receiver"
NS_MEDIA = "urn:x-cast:com.google.cast.media"

SENDER_ID = "sender-0"
RECEIVER_ID = "receiver-0"

DEFAULT_RECEIVER = "CC1AD845"

MAX_MESSAGE_BYTES = 60 * 1024

RECEIVER_ERRORS = frozenset({
    "LOAD_FAILED", "LOAD_CANCELLED", "INVALID_REQUEST", "ERROR",
})


def _varint(value: int) -> bytes:
    out = bytearray()
    value = int(value)
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def _field_varint(number: int, value: int) -> bytes:
    return _varint((number << 3) | 0) + _varint(value)


def _field_string(number: int, text: str) -> bytes:
    raw = text.encode("utf-8")
    return _varint((number << 3) | 2) + _varint(len(raw)) + raw


def encode(source: str, dest: str, namespace: str, payload: str) -> bytes:
    """One CastMessage body (the 4-byte big-endian length goes on wire)."""
    parts = [
        _field_varint(1, 0),
        _field_string(2, source),
        _field_string(3, dest),
        _field_string(4, namespace),
        _field_varint(5, 0),
        _field_string(6, payload),
    ]
    return b"".join(parts)


def _read_varint(buf: bytes, pos: int) -> tuple[int, int] | None:
    value = 0
    shift = 0
    index = pos
    while index < len(buf):
        byte = buf[index]
        index += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, index
        shift += 7
        if shift >= 64:
            return None
    return None


def decode(body: bytes) -> tuple[str, str, str, str] | None:
    """Split a message body into (source, dest, namespace, payload)."""
    source, dest, namespace, payload = "", "", "", ""
    pos = 0
    while pos < len(body):
        key = _read_varint(body, pos)
        if key is None:
            return None
        tag, pos = key
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            skip = _read_varint(body, pos)
            if skip is None:
                return None
            pos = skip[1]
        elif wire == 2:
            length = _read_varint(body, pos)
            if length is None:
                return None
            size, pos = length
            text = body[pos:pos + size].decode("utf-8", "replace")
            pos += size
            if number == 2:
                source = text
            elif number == 3:
                dest = text
            elif number == 4:
                namespace = text
            elif number == 6:
                payload = text
        else:
            return None
    if not namespace and not payload:
        return None
    return source, dest, namespace, payload


def _loads(payload: str) -> dict:
    try:
        parsed = json.loads(payload)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def connect_payload() -> str:
    return json.dumps({"type": "CONNECT"})


def ping_payload() -> str:
    return json.dumps({"type": "PING"})


def pong_payload() -> str:
    return json.dumps({"type": "PONG"})


def receiver_get_status(request_id: int) -> str:
    return json.dumps({"type": "GET_STATUS", "requestId": int(request_id)})


def receiver_launch(request_id: int,
                    app_id: str = DEFAULT_RECEIVER) -> str:
    return json.dumps({"type": "LAUNCH", "appId": app_id,
                       "requestId": int(request_id)})


def receiver_set_volume(request_id: int, level: float | None = None,
                        muted: bool | None = None) -> str:
    volume: dict = {}
    if level is not None:
        volume["level"] = min(1.0, max(0.0, float(level)))
    if muted is not None:
        volume["muted"] = bool(muted)
    return json.dumps({"type": "SET_VOLUME",
                       "requestId": int(request_id), "volume": volume})


def media_get_status(request_id: int, media_session_id: int = 0) -> str:
    body: dict = {"type": "GET_STATUS", "requestId": int(request_id)}
    if media_session_id:
        body["mediaSessionId"] = int(media_session_id)
    return json.dumps(body)


def _media_base(kind: str, request_id: int,
                media_session_id: int) -> dict:
    return {"type": kind, "requestId": int(request_id),
            "mediaSessionId": int(media_session_id)}


def media_play(request_id: int, media_session_id: int) -> str:
    return json.dumps(_media_base("PLAY", request_id, media_session_id))


def media_pause(request_id: int, media_session_id: int) -> str:
    return json.dumps(_media_base("PAUSE", request_id, media_session_id))


def media_stop(request_id: int, media_session_id: int) -> str:
    return json.dumps(_media_base("STOP", request_id, media_session_id))


def media_seek(request_id: int, media_session_id: int,
               position_s: float) -> str:
    body = _media_base("SEEK", request_id, media_session_id)
    body["currentTime"] = max(0.0, float(position_s))
    body["resumeState"] = "PLAYBACK_START"
    return json.dumps(body)


def media_load(request_id: int, content_id: str, content_type: str,
               title: str, artist: str = "", album: str = "",
               art_url: str = "", position_s: float = 0.0,
               autoplay: bool = True,
               media_id: str = "") -> str:
    """A single-item LOAD for the default media receiver."""
    metadata: dict = {"metadataType": 3, "title": title or ""}
    if artist:
        metadata["artist"] = artist
    if album:
        metadata["albumName"] = album
    if art_url:
        metadata["images"] = [{"url": art_url}]
    if media_id:
        metadata["songId"] = media_id
    media: dict = {"contentId": content_id, "streamType": "BUFFERED",
                   "contentType": content_type or "audio/mpeg",
                   "metadata": metadata}
    if media_id:
        media["customData"] = {"id": media_id}
    body: dict = {"type": "LOAD", "requestId": int(request_id),
                  "media": media, "autoplay": bool(autoplay),
                  "currentTime": max(0.0, float(position_s))}
    if media_id:
        body["customData"] = {"id": media_id}
    return json.dumps(body)


def parse_receiver_status(payload: str) -> dict:
    """Transport id plus volume from a receiver-namespace payload."""
    root = _loads(payload)
    status = root.get("status")
    if not isinstance(status, dict):
        return {}
    found: dict = {}
    apps = status.get("applications")
    if isinstance(apps, list) and apps and isinstance(apps[0], dict):
        transport = apps[0].get("transportId")
        if transport:
            found["transportId"] = str(transport)
    volume = status.get("volume")
    if isinstance(volume, dict):
        if "level" in volume:
            try:
                found["level"] = float(volume["level"])
            except (TypeError, ValueError):
                pass
        if "muted" in volume:
            found["muted"] = bool(volume["muted"])
    return found


def _status_row(payload: str) -> dict:
    root = _loads(payload)
    status = root.get("status")
    if isinstance(status, list) and status and isinstance(status[0], dict):
        return status[0]
    if isinstance(status, dict):
        return status
    return {}


def media_id_from_url(url: str | None) -> str:
    raw = (url or "").split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    if not raw:
        return ""
    try:
        return urllib.parse.unquote(raw)
    except Exception:  # noqa: BLE001 - a bad escape keeps the raw id
        return raw


def media_id_from_status(row: dict, media: dict | None = None) -> str:
    for scope in (row.get("customData"),
                  (media or {}).get("customData"),
                  (media or {}).get("metadata")):
        if isinstance(scope, dict):
            for key in ("id", "songId"):
                value = scope.get(key)
                if value:
                    return str(value)
    content = (media or {}).get("contentId")
    return media_id_from_url(str(content) if content else "")


def parse_media_status(payload: str) -> dict:
    """Position and state from a media-namespace payload."""
    row = _status_row(payload)
    if not row:
        return {}
    found: dict = {}
    if "mediaSessionId" in row:
        try:
            found["mediaSessionId"] = int(row["mediaSessionId"])
        except (TypeError, ValueError):
            pass
    if "currentTime" in row:
        try:
            found["positionMs"] = int(float(row["currentTime"]) * 1000.0)
        except (TypeError, ValueError):
            pass
    media = row.get("media") if isinstance(row.get("media"), dict) else None
    if media is not None and "duration" in media:
        try:
            found["durationMs"] = int(float(media["duration"]) * 1000.0)
        except (TypeError, ValueError):
            pass
    media_id = media_id_from_status(row, media)
    if media_id:
        found["mediaId"] = media_id
    state = row.get("playerState")
    if isinstance(state, str) and state:
        found["playerState"] = state
        if state in ("PLAYING", "BUFFERING"):
            found["playing"] = True
        elif state in ("PAUSED", "IDLE"):
            found["playing"] = False
    return found


def is_finished_payload(payload: str) -> bool:
    return '"IDLE"' in payload or '"FINISHED"' in payload
