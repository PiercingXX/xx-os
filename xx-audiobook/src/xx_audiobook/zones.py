"""Home Assistant zones: locations, players, cast transport (M5).

Mirrors data/repo/ZoneRepository.kt: locations and cast targets are cached
in memory, the output is the phone or one HA player, and "Bring back to
phone" only resolves once a confirmed stop lands (on failure the zone
stays the output, so the phone never plays while the zone may still be
playing). HA errors surface verbatim. Synchronous; callers run the repo
on worker threads, screens use ZonesState.submit_* from the GTK thread.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from xx_audiobook import api as api_module


class Phone:
    """Audio goes to this phone (no zone active)."""


@dataclass
class ZoneOutput:
    location_id: str = ""
    entity_id: str = ""
    name: str = ""
    item_id: str = ""

    @property
    def is_zone(self) -> bool:
        return True


NOT_ON_ZONE = "Not playing on a zone."
ZONE_SILENT = "The zone did not answer."
SEEK_STEP_S = 30.0


def _display_name(target) -> str:
    name = (target.name or "").strip()
    return name if name else target.entity_id


class ZoneRepository:
    """Zone output state; thin wrapper over the cast endpoints."""

    def __init__(self, api) -> None:
        self._api = api
        self._lock = threading.Lock()
        self._locations: list = []
        self._targets: dict[str, list] = {}
        self._output = Phone()

    @property
    def locations(self) -> list:
        with self._lock:
            return list(self._locations)

    @property
    def targets(self) -> dict:
        with self._lock:
            return {key: list(rows)
                    for key, rows in self._targets.items()}

    @property
    def output(self):
        with self._lock:
            return self._output

    def targets_for(self, location_id: str) -> list:
        with self._lock:
            return list(self._targets.get(location_id, []))

    def refresh_locations(self) -> tuple:
        try:
            rows = self._api.locations()
        except api_module.ApiError as exc:
            return (None, exc)
        with self._lock:
            self._locations = list(rows)
        return (list(rows), None)

    def refresh_targets(self, location_id: str) -> tuple:
        try:
            rows = self._api.cast_targets(location_id)
        except api_module.ApiError as exc:
            return (None, exc)
        with self._lock:
            self._targets[location_id] = list(rows)
        return (list(rows), None)

    def play(self, target, location_id: str, item_id: str,
             position_s: float | None = None,
             chapter_index: int | None = None) -> tuple:
        try:
            self._api.cast_play(api_module.CastPlay(
                entity_id=target.entity_id, item_id=item_id,
                location_id=location_id, chapter_index=chapter_index,
                position_s=position_s))
        except api_module.ApiError as exc:
            return (None, exc)
        zone = ZoneOutput(location_id=location_id,
                          entity_id=target.entity_id,
                          name=_display_name(target), item_id=item_id)
        with self._lock:
            self._output = zone
        return (zone, None)

    def transport(self, action: str,
                  value: float | None = None) -> tuple:
        with self._lock:
            zone = self._output
        if not isinstance(zone, ZoneOutput):
            return (None, api_module.ApiError(NOT_ON_ZONE))
        try:
            result = self._api.cast_transport(api_module.CastTransport(
                entity_id=zone.entity_id, action=action,
                location_id=zone.location_id, value=value))
        except api_module.ApiError as exc:
            return (None, exc)
        if not result.ok:
            return (None, api_module.ApiError(ZONE_SILENT))
        if action == "stop":
            with self._lock:
                self._output = Phone()
        return (result, None)

    def bring_back_to_phone(self) -> tuple:
        with self._lock:
            zone = self._output
        if not isinstance(zone, ZoneOutput):
            return (None, None)
        _result, error = self.transport("stop")
        if error is not None:
            return (None, error)
        return (zone, None)


@dataclass
class ZonesSnapshot:
    locations: list = field(default_factory=list)
    targets: dict = field(default_factory=dict)
    output: object = field(default_factory=Phone)
    selected: str | None = None
    message: str | None = None
    busy: bool = False
    volume: float | None = None
    zone_playing: bool | None = None


def _message_of(error) -> str:
    return str(getattr(error, "message", error) or error)


class ZonesState:
    """Small state API for the Zones screen (mirrors ZonesViewModel.kt).

    Screens read snapshot() (any thread) and call the plain methods on a
    worker thread or the submit_* variants from the GTK thread (results
    arrive via GLib.idle_add).
    """

    IDLE_STATES = ("paused", "idle", "off", "standby")

    def __init__(self, repo: ZoneRepository | None = None,
                 api=None) -> None:
        self._repo = repo or ZoneRepository(api)
        self._lock = threading.Lock()
        self._selected: str | None = None
        self._message: str | None = None
        self._busy = False
        self._loading: set = set()
        self._volume: float | None = None
        self._zone_playing: bool | None = None

    @property
    def repo(self) -> ZoneRepository:
        return self._repo

    def snapshot(self) -> ZonesSnapshot:
        with self._lock:
            selected = self._selected
            message = self._message
            busy = self._busy
            volume = self._volume
            playing = self._zone_playing
        return ZonesSnapshot(
            locations=self._repo.locations, targets=self._repo.targets,
            output=self._repo.output, selected=selected, message=message,
            busy=busy, volume=volume, zone_playing=playing)

    def selected(self) -> str | None:
        with self._lock:
            return self._selected

    def loading_targets(self) -> set:
        with self._lock:
            return set(self._loading)

    def zone_player(self):
        output = self._repo.output
        if not isinstance(output, ZoneOutput):
            return None
        for target in self._repo.targets_for(output.location_id):
            if target.entity_id == output.entity_id:
                return target
        return None

    def refresh(self) -> tuple:
        with self._lock:
            self._busy = True
        try:
            rows, error = self._repo.refresh_locations()
        finally:
            with self._lock:
                self._busy = False
        with self._lock:
            if error is not None:
                self._message = _message_of(error)
                return (None, error)
            if not rows:
                self._message = ("No zones yet. "
                                 "An admin adds them in the studio.")
            else:
                self._message = None
                if (self._selected is None or not any(
                        r.id == self._selected for r in rows)):
                    self._selected = rows[0].id
            selected = self._selected
        if selected is not None:
            self.load_targets(selected)
        return (rows, None)

    def select(self, location_id: str) -> None:
        with self._lock:
            self._selected = location_id
        self.load_targets(location_id)

    def load_targets(self, location_id: str) -> tuple:
        with self._lock:
            self._loading.add(location_id)
        try:
            rows, error = self._repo.refresh_targets(location_id)
        finally:
            with self._lock:
                self._loading.discard(location_id)
        with self._lock:
            if error is not None:
                self._message = _message_of(error)
            elif rows is not None:
                self._adopt(rows)
        return (rows, error)

    def _adopt(self, rows: list) -> None:
        output = self._repo.output
        if not isinstance(output, ZoneOutput):
            return
        for target in rows:
            if target.entity_id != output.entity_id:
                continue
            if target.volume_level is not None:
                self._volume = float(target.volume_level)
            if target.state == "playing":
                self._zone_playing = True
            elif target.state in self.IDLE_STATES:
                self._zone_playing = False

    def play_on(self, target, item_id: str | None,
                position_s: float | None) -> tuple:
        with self._lock:
            location_id = self._selected
        if location_id is None:
            return (None, api_module.ApiError("Pick a zone first."))
        if not item_id:
            with self._lock:
                self._message = ("Pick a book first "
                                 "— start it in the Library.")
            return (None, api_module.ApiError(self._message or ""))
        zone, error = self._repo.play(target, location_id, item_id,
                                      position_s)
        with self._lock:
            if error is not None:
                self._message = _message_of(error)
            else:
                self._message = None
                if target.volume_level is not None:
                    self._volume = float(target.volume_level)
                self._zone_playing = True
        return (zone, error)

    def transport(self, action: str,
                  value: float | None = None) -> tuple:
        result, error = self._repo.transport(action, value)
        with self._lock:
            if error is not None:
                self._message = _message_of(error)
            elif action == "play":
                self._zone_playing = True
            elif action == "pause":
                self._zone_playing = False
        return (result, error)

    def seek_by(self, seconds: float) -> tuple:
        player = self.zone_player()
        if player is None or player.media_position_s is None:
            return (None, None)
        at = max(0.0, player.media_position_s + seconds)
        result, error = self.transport("seek", at)
        output = self._repo.output
        if isinstance(output, ZoneOutput):
            self.load_targets(output.location_id)
        return (result, error)

    def seek_step(self, forward: bool = True) -> tuple:
        return self.seek_by(SEEK_STEP_S if forward else -SEEK_STEP_S)

    def set_volume(self, value: float) -> tuple:
        clamped = min(1.0, max(0.0, value))
        with self._lock:
            self._volume = clamped
        return self.transport("volume", clamped)

    def quieter(self, step: float = 0.1) -> tuple:
        with self._lock:
            current = self._volume if self._volume is not None else 0.5
        return self.set_volume(current - step)

    def louder(self, step: float = 0.1) -> tuple:
        with self._lock:
            current = self._volume if self._volume is not None else 0.5
        return self.set_volume(current + step)

    def bring_back_to_phone(self) -> tuple:
        output = self._repo.output
        if not isinstance(output, ZoneOutput):
            return (None, None)
        _result, error = self._repo.bring_back_to_phone()
        with self._lock:
            if error is not None:
                self._message = _message_of(error)
                return (None, error)
            self._message = None
            self._volume = None
            self._zone_playing = None
        return (output.item_id, None)

    def submit_refresh(self, on_done=None):
        return api_module.submit(self.refresh, on_done)

    def submit_select(self, location_id: str, on_done=None):
        return api_module.submit(
            lambda: (self.select(location_id), None), on_done)

    def submit_transport(self, action: str, value: float | None = None,
                         on_done=None):
        return api_module.submit(
            lambda: self.transport(action, value), on_done)

    def submit_bring_back(self, on_done=None):
        return api_module.submit(self.bring_back_to_phone, on_done)
