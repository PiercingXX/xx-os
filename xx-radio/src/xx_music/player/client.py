"""Async Player1 proxy for the window. Every call returns through a callback
on the main loop; nothing here blocks, so the GTK loop stays responsive.
"""

from __future__ import annotations

import json
from typing import Callable

from gi.repository import Gio, GLib

from xx_music.appspec import AppSpec
from xx_music.engine import Track
from xx_music.player import api

TIMEOUT_MS = 10000


class PlayerClient:
    def __init__(self, connection: Gio.DBusConnection, spec: AppSpec) -> None:
        self._connection = connection
        self._spec = spec

    def _call(self, method: str, params: GLib.Variant | None,
              callback: Callable | None) -> None:
        self._connection.call(
            self._spec.player_bus_name, self._spec.player_object_path,
            self._spec.player_interface, method, params, None,
            Gio.DBusCallFlags.NONE, TIMEOUT_MS, None, callback)

    @staticmethod
    def _done_string(callback):
        def on_reply(connection, result) -> None:
            try:
                reply = connection.call_finish(result)
                (value,) = reply.unpack()
            except Exception as exc:  # noqa: BLE001 - forwarded to the UI
                callback(None, exc)
            else:
                callback(value, None)
        return on_reply

    def play(self, tracks: list[Track], start: int = 0,
             origin: dict | None = None, callback: Callable | None = None
             ) -> None:
        self._call("Play", GLib.Variant(
            "(sis)", (api.tracks_to_json(tracks), int(start),
                       json.dumps(origin or {}))), callback)

    def start_radio(self, track: Track, callback: Callable | None = None
                    ) -> None:
        self._call("StartRadio", GLib.Variant(
            "(s)", (json.dumps(track.to_dict()),)), callback)

    def enqueue(self, tracks: list[Track], play_next: bool = False,
                callback: Callable | None = None) -> None:
        self._call("Enqueue", GLib.Variant(
            "(sb)", (api.tracks_to_json(tracks), bool(play_next))), callback)

    def play_at(self, index: int, callback: Callable | None = None) -> None:
        self._call("PlayAt", GLib.Variant("(i)", (int(index),)), callback)

    def remove(self, index: int, callback: Callable | None = None) -> None:
        self._call("Remove", GLib.Variant("(i)", (int(index),)), callback)

    def move(self, source: int, dest: int,
             callback: Callable | None = None) -> None:
        self._call("Move", GLib.Variant(
            "(ii)", (int(source), int(dest))), callback)

    def set_shuffle(self, on: bool, callback: Callable | None = None) -> None:
        self._call("SetShuffle", GLib.Variant("(b)", (bool(on),)), callback)

    def set_repeat(self, mode: str, callback: Callable | None = None) -> None:
        self._call("SetRepeat", GLib.Variant("(s)", (str(mode),)), callback)

    def set_sleep(self, seconds: int,
                  callback: Callable | None = None) -> None:
        self._call("SetSleep", GLib.Variant("(i)", (int(seconds),)), callback)

    def rate(self, track_id: str, rating: str,
             callback: Callable | None = None) -> None:
        self._call("Rate", GLib.Variant(
            "(ss)", (str(track_id), str(rating))), callback)

    def download(self, items: list[dict],
                 callback: Callable | None = None) -> None:
        self._call("Download", GLib.Variant(
            "(s)", (json.dumps(items),)), callback)

    def remove_download(self, item_id: str,
                        callback: Callable | None = None) -> None:
        self._call("RemoveDownload", GLib.Variant(
            "(s)", (str(item_id),)), callback)

    def snapshot(self, callback: Callable | None = None) -> None:
        self._call("Snapshot", None, self._done_string(callback)
                   if callback else None)

    def queue(self, callback: Callable | None = None) -> None:
        def on_reply(connection, result) -> None:
            try:
                reply = connection.call_finish(result)
                version, payload, index = reply.unpack()
                tracks = api.tracks_from_json(payload)
            except Exception as exc:  # noqa: BLE001 - forwarded to the UI
                callback(None, exc)
            else:
                callback((version, tracks, index), None)
        self._call("Queue", None, on_reply if callback else None)

    @staticmethod
    def parse_snapshot(payload: str) -> dict:
        return json.loads(payload)
