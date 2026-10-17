"""The window-to-service API: JSON payloads of contract models over D-Bus."""

from __future__ import annotations

import json

from xx_music.appspec import AppSpec
from xx_music.engine import Track

INVALID_ARGS = "org.freedesktop.DBus.Error.InvalidArgs"


def player_xml(spec: AppSpec) -> str:
    return f"""<node>
  <interface name="{spec.player_interface}">
    <method name="Play"><arg direction="in" type="s"/><arg direction="in" type="i"/><arg direction="in" type="s"/></method>
    <method name="StartRadio"><arg direction="in" type="s"/></method>
    <method name="Enqueue"><arg direction="in" type="s"/><arg direction="in" type="b"/></method>
    <method name="PlayAt"><arg direction="in" type="i"/></method>
    <method name="Remove"><arg direction="in" type="i"/></method>
    <method name="Move"><arg direction="in" type="i"/><arg direction="in" type="i"/></method>
    <method name="SetShuffle"><arg direction="in" type="b"/></method>
    <method name="SetRepeat"><arg direction="in" type="s"/></method>
    <method name="SetSleep"><arg direction="in" type="i"/></method>
    <method name="Rate"><arg direction="in" type="s"/><arg direction="in" type="s"/></method>
    <method name="Download"><arg direction="in" type="s"/></method>
    <method name="RemoveDownload"><arg direction="in" type="s"/></method>
    <method name="Snapshot"><arg direction="out" type="s"/></method>
    <method name="Queue"><arg direction="out" type="u"/><arg direction="out" type="s"/><arg direction="out" type="i"/></method>
    <method name="ListCastDevices"><arg direction="out" type="s"/></method>
    <method name="CastTo"><arg direction="in" type="s"/></method>
    <method name="StopCasting"/>
    <method name="CastState"><arg direction="out" type="s"/></method>
    <property name="CastState" type="s" access="read"/>
    <signal name="Changed"><arg type="s"/></signal>
    <signal name="QueueChanged"><arg type="u"/></signal>
    <signal name="DownloadsChanged"><arg type="u"/></signal>
    <signal name="Problem"><arg type="s"/><arg type="s"/></signal>
    <signal name="CastChanged"><arg type="s"/></signal>
  </interface>
  <interface name="org.freedesktop.DBus.Properties">
    <method name="Get"><arg direction="in" type="s"/><arg direction="in" type="s"/><arg direction="out" type="v"/></method>
    <method name="GetAll"><arg direction="in" type="s"/><arg direction="out" type="a{{sv}}"/></method>
  </interface>
</node>"""


def tracks_to_json(tracks: list[Track]) -> str:
    return json.dumps([t.to_dict() for t in tracks])


def tracks_from_json(payload: str) -> list[Track]:
    try:
        raw = json.loads(payload)
    except ValueError as exc:
        raise ValueError(f"not JSON: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError("tracks payload must be a JSON list")
    try:
        return [Track.from_dict(item) for item in raw]
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"bad track: {exc}") from exc


def track_from_json(payload: str) -> Track:
    try:
        raw = json.loads(payload)
    except ValueError as exc:
        raise ValueError(f"not JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("track payload must be a JSON object")
    try:
        return Track.from_dict(raw)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"bad track: {exc}") from exc


def origin_from_json(payload: str) -> dict:
    if not payload:
        return {}
    try:
        raw = json.loads(payload)
    except ValueError as exc:
        raise ValueError(f"not JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("origin payload must be a JSON object")
    return raw
