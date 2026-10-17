"""Alarm sound + vibration (Linux half of audio/KlaxonPlayer.kt).

Sound plays through GStreamer at alarm volume with the 60 s gradual ramp;
vibration goes through feedbackd (`alarm-clock-elapsed`) when present.
Every bus call is async and every GStreamer import is lazy, so the module
imports headless and a missing pipeline degrades to the missed-notification
path instead of a silent ring.
"""

from __future__ import annotations

FEEDBACK_BUS = "org.sigxcpu.Feedback"
FEEDBACK_PATH = "/org/sigxcpu/Feedback"
FEEDBACK_IFACE = "org.sigxcpu.Feedback"
FEEDBACK_EVENT = "alarm-clock-elapsed"

FALLBACK_SOUNDS = ("alarm-clock-elapsed", "alarm", "message-new-instant")


def feedback_params(app_id: str, glib=None):
    if glib is None:
        import gi
        gi.require_version("GLib", "2.0")
        from gi.repository import GLib as glib
    return glib.Variant("(ssa{sv}i)", (app_id, FEEDBACK_EVENT, {}, 0))


class Ringer:
    def __init__(self, app_id: str = "io.piercingxx.XXClock",
                 play=None, stop=None, vibrate=None) -> None:
        self._app_id = app_id
        self._play = play
        self._stop = stop
        self._vibrate = vibrate
        self._player = None
        self._ramp_source = None
        self._ringing = False

    @property
    def ringing(self) -> bool:
        return self._ringing

    def start(self, candidates: list[str], gradual: bool = True) -> bool:
        self.stop()
        vibrated = self._trigger_vibration()
        sounded = self._start_sound(candidates, gradual)
        self._ringing = bool(vibrated or sounded)
        return self._ringing

    def stop(self) -> None:
        self._ringing = False
        if self._stop is not None:
            try:
                self._stop()
            except Exception:
                pass
        else:
            self._stop_pipeline()
        self._cancel_ramp()

    def _trigger_vibration(self) -> bool:
        if self._vibrate is not None:
            try:
                return bool(self._vibrate())
            except Exception:
                return False
        try:
            import gi
            gi.require_version("Gio", "2.0")
            gi.require_version("GLib", "2.0")
            from gi.repository import Gio, GLib
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            params = feedback_params(self._app_id, GLib)
            bus.call(FEEDBACK_BUS, FEEDBACK_PATH, FEEDBACK_IFACE, "TriggerFeedback",
                     params, None, 0, 5000, None, lambda _c, _r: None)
            return True
        except Exception:
            return False

    def _start_sound(self, candidates: list[str], gradual: bool) -> bool:
        if self._play is not None:
            try:
                return bool(self._play(candidates))
            except Exception:
                return False
        try:
            import gi
            gi.require_version("Gst", "1.0")
            gi.require_version("GLib", "2.0")
            from gi.repository import Gst, GLib
            Gst.init(None)
            for uri in list(candidates) + [f"theme:{name}" for name in FALLBACK_SOUNDS]:
                player = self._build_player(Gst, uri, gradual)
                if player is not None:
                    self._player = player
                    if gradual:
                        self._arm_ramp(GLib)
                    return True
        except Exception:
            pass
        return False

    def _build_player(self, gst, uri: str, gradual: bool):
        try:
            if uri.startswith("theme:"):
                pipeline = gst.parse_launch(
                    f"libcanberrasrc event-id={uri[6:]} ! autoaudiosink")
            else:
                path = uri[7:] if uri.startswith("file://") else uri
                pipeline = gst.ElementFactory.make("playbin", None)
                pipeline.set_property("uri", f"file://{path}" if not uri.startswith("file://") else uri)
            pipeline.set_property("volume", 0.15 if gradual else 1.0)
            pipeline.set_state(gst.State.PLAYING)
            return pipeline
        except Exception:
            return None

    def _arm_ramp(self, glib) -> None:
        import time as _time
        from .prefs import VOLUME_RAMP_MS
        started = _time.monotonic()

        def _step() -> bool:
            if self._player is None:
                return False
            elapsed_ms = (_time.monotonic() - started) * 1000
            fraction = min(1.0, elapsed_ms / VOLUME_RAMP_MS)
            try:
                self._player.set_property("volume", 0.15 + 0.85 * fraction)
            except Exception:
                return False
            return fraction < 1.0

        try:
            self._ramp_source = glib.timeout_add(1000, _step)
        except Exception:
            self._ramp_source = None

    def _stop_pipeline(self) -> None:
        player, self._player = self._player, None
        if player is None:
            return
        try:
            import gi
            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
            player.set_state(Gst.State.NULL)
        except Exception:
            pass

    def _cancel_ramp(self) -> None:
        source, self._ramp_source = self._ramp_source, None
        if source is None:
            return
        try:
            import gi
            gi.require_version("GLib", "2.0")
            from gi.repository import GLib
            GLib.source_remove(source)
        except Exception:
            pass
