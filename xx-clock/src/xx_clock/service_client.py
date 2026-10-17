"""GUI-facing clock service (daemon when reachable, local fallback).

The daemon is the single writer whenever it runs; every mutation prefers
its D-Bus methods. When the daemon is absent (tests, first run before the
user unit starts) a local Api over the same store keeps the window fully
functional, and the daemon reconciles when it appears. Blocking bus calls
never run on the GTK thread — use the `call_async` helper.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable

from . import DAEMON_BUS_NAME, DAEMON_INTERFACE, DAEMON_OBJECT_PATH


def _gio():
    import gi
    gi.require_version("Gio", "2.0")
    gi.require_version("GLib", "2.0")
    from gi.repository import Gio, GLib
    return Gio, GLib


def call_async(work: Callable, done: Callable | None = None) -> None:
    """Run `work` off the GTK thread, `done(result)` back on it."""

    def _runner() -> None:
        try:
            outcome = ("ok", work())
        except Exception as exc:
            outcome = ("error", exc)
        if done is None:
            return
        _, GLib = _gio()

        def _deliver() -> bool:
            try:
                done(outcome)
            except Exception:
                pass
            return False

        GLib.idle_add(_deliver)

    threading.Thread(target=_runner, daemon=True).start()


class ClockService:
    def __init__(self, on_state=None, on_ringing=None, local_hooks=None,
                 bus=None, force_local: bool = False,
                 defer_daemon: bool = False) -> None:
        self._on_state = on_state or (lambda: None)
        self._on_ringing = on_ringing or (lambda _e, _r, _k, _l: None)
        self._bus = bus
        self._proxy = None
        self._local = None
        self._local_hooks = local_hooks
        self.daemon_available = False
        if not force_local and not defer_daemon:
            self.daemon_available = self._connect()
        if not self.daemon_available:
            self._local = self._build_local(local_hooks)

    def upgrade_to_daemon(self, done: Callable[[bool], None] | None = None) -> None:
        """Blocking bus probe off the GTK thread; swaps to the daemon when
        found and asks it to resync anything written locally meanwhile."""

        def _work() -> bool:
            self.daemon_available = self._connect()
            if not self.daemon_available:
                return False
            try:
                (ok,) = self._call("Resync", None, "(b)")
                return bool(ok)
            except Exception:
                return False

        def _finished(outcome) -> None:
            ok = outcome[0] == "ok" and outcome[1]
            if ok:
                self._local = None
                try:
                    self._on_state()
                except Exception:
                    pass
            if done is not None:
                try:
                    done(ok)
                except Exception:
                    pass

        call_async(_work, _finished)

    def _connect(self) -> bool:
        try:
            Gio, GLib = _gio()
            bus = self._bus or Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self._bus = bus
            owner = bus.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "GetNameOwner",
                GLib.Variant("(s)", (DAEMON_BUS_NAME,)),
                GLib.VariantType.new("(s)"),
                Gio.DBusCallFlags.NONE, 2000, None)
            if not owner:
                return False
            self._proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
                DAEMON_BUS_NAME, DAEMON_OBJECT_PATH, DAEMON_INTERFACE, None)
            bus.signal_subscribe(None, DAEMON_INTERFACE, "StateChanged",
                                 DAEMON_OBJECT_PATH, None, 0, self._state_signal)
            bus.signal_subscribe(None, DAEMON_INTERFACE, "RingingChanged",
                                 DAEMON_OBJECT_PATH, None, 0, self._ringing_signal)
            return True
        except Exception:
            self._proxy = None
            return False

    def _state_signal(self, _conn, _sender, _path, _iface, _sig, _params, _data=None) -> None:
        try:
            self._on_state()
        except Exception:
            pass

    def _ringing_signal(self, _conn, _sender, _path, _iface, _sig, params, _data=None) -> None:
        try:
            entry_id, ringing, kind, label = params.unpack()
            self._on_ringing(int(entry_id), bool(ringing), str(kind), str(label))
        except Exception:
            pass

    def _build_local(self, local_hooks):
        from . import prefs
        from .coordinator import Hooks
        from .daemon import Api
        from .store import Store
        store = Store()
        hooks = local_hooks or Hooks()
        keep_schedule, keep_cancel = hooks.schedule_alarm, hooks.cancel_alarm

        def _schedule(alarm, at_ms) -> None:
            store.set_scheduled_fire(alarm.id, at_ms)
            keep_schedule(alarm, at_ms)

        def _cancel(alarm_id) -> None:
            store.clear_scheduled_fire(alarm_id)
            keep_cancel(alarm_id)

        hooks.schedule_alarm, hooks.cancel_alarm = _schedule, _cancel
        previous = hooks.refreshed
        hooks.refreshed = lambda: (previous(), self._local_changed())
        api = Api(store, settings=prefs.load(), hooks=hooks)
        api.coordinator.reconcile(force=True, recover_ringing=True)
        return api

    def _local_changed(self) -> None:
        try:
            self._on_state()
        except Exception:
            pass

    def _call(self, method: str, params, reply_type: str):
        Gio, _ = _gio()
        result = self._proxy.call_sync(
            method, params, Gio.DBusCallFlags.NONE, 5000, None)
        assert result.get_type_string() == reply_type, result.get_type_string()
        return result.unpack()

    def get_state(self) -> dict:
        if self._proxy is not None:
            _, GLib = _gio()
            (raw,) = self._call("GetState", None, "(s)")
            try:
                return json.loads(raw)
            except Exception:
                return {"alarms": [], "timers": [], "ringing": None, "next": None}
        return self._local.get_state()

    def save_alarm(self, data: dict) -> int:
        if self._proxy is not None:
            _, GLib = _gio()
            (alarm_id,) = self._call("SaveAlarm", GLib.Variant("(s)", (json.dumps(data),)), "(x)")
            return int(alarm_id)
        return self._local.save_alarm(data)

    def _bool_call(self, method: str, params, local: Callable[[], bool]) -> bool:
        if self._proxy is not None:
            try:
                (ok,) = self._call(method, params, "(b)")
                return bool(ok)
            except Exception:
                return False
        return local()

    def delete_alarm(self, alarm_id: int) -> bool:
        _, GLib = _gio()
        return self._bool_call("DeleteAlarm", GLib.Variant("(x)", (alarm_id,)),
                               lambda: self._local.delete_alarm(alarm_id))

    def toggle_alarm(self, alarm_id: int, enabled: bool) -> bool:
        _, GLib = _gio()
        return self._bool_call("ToggleAlarm", GLib.Variant("(xb)", (alarm_id, enabled)),
                               lambda: self._local.toggle_alarm(alarm_id, enabled))

    def snooze_ringing(self, alarm_id: int, minutes: int) -> bool:
        _, GLib = _gio()
        return self._bool_call("SnoozeRinging", GLib.Variant("(xi)", (alarm_id, minutes)),
                               lambda: self._local.snooze_ringing(alarm_id, minutes))

    def dismiss_ringing(self, alarm_id: int) -> bool:
        _, GLib = _gio()
        return self._bool_call("DismissRinging", GLib.Variant("(x)", (alarm_id,)),
                               lambda: self._local.dismiss_ringing(alarm_id))

    def start_timer(self, duration_ms: int, label: str) -> int:
        if self._proxy is not None:
            try:
                _, GLib = _gio()
                (timer_id,) = self._call(
                    "StartTimer", GLib.Variant("(xs)", (int(duration_ms), label or "")), "(x)")
                return int(timer_id)
            except Exception:
                return -1
        return self._local.start_timer(duration_ms, label)

    def _timer_call(self, method: str, timer_id: int, local: Callable[[], bool]) -> bool:
        _, GLib = _gio()
        return self._bool_call(method, GLib.Variant("(x)", (timer_id,)), local)

    def pause_timer(self, timer_id: int) -> bool:
        return self._timer_call("PauseTimer", timer_id, lambda: self._local.pause_timer(timer_id))

    def resume_timer(self, timer_id: int) -> bool:
        return self._timer_call("ResumeTimer", timer_id, lambda: self._local.resume_timer(timer_id))

    def reset_timer(self, timer_id: int) -> bool:
        return self._timer_call("ResetTimer", timer_id, lambda: self._local.reset_timer(timer_id))

    def add_minute_timer(self, timer_id: int) -> bool:
        return self._timer_call("AddMinuteTimer", timer_id,
                                lambda: self._local.add_minute_timer(timer_id))

    def delete_timer(self, timer_id: int) -> bool:
        return self._timer_call("DeleteTimer", timer_id, lambda: self._local.delete_timer(timer_id))

    def stop_timer_ringing(self, timer_id: int) -> bool:
        return self._timer_call("StopTimerRinging", timer_id,
                                lambda: self._local.stop_timer_ringing(timer_id))
