"""Schedule-owning user service (`xx-clock --daemon`).

Owns `io.piercingxx.XXClock.Daemon` (never the GTK app id — sharing it
would make the GUI exit as a secondary instance). The daemon is the single
writer: every mutation goes through its methods and it emits the signals.
Started by the systemd user unit and by D-Bus activation; it reconciles on
start and never idle-exits while armed.
"""

from __future__ import annotations

import json
import time as _time

from . import DAEMON_BUS_NAME, DAEMON_INTERFACE, DAEMON_OBJECT_PATH
from . import api_validate, applog, fmt, prefs, scheduler
from .coordinator import TITLE_MISSED_ALARM
from .models import Alarm

INTROSPECTION_XML = f"""<node>
  <interface name="{DAEMON_INTERFACE}">
    <method name="GetState"><arg type="s" name="state" direction="out"/></method>
    <method name="SaveAlarm">
      <arg type="s" name="alarm_json" direction="in"/>
      <arg type="x" name="alarm_id" direction="out"/>
    </method>
    <method name="DeleteAlarm">
      <arg type="x" name="alarm_id" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="ToggleAlarm">
      <arg type="x" name="alarm_id" direction="in"/>
      <arg type="b" name="enabled" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="SnoozeRinging">
      <arg type="x" name="alarm_id" direction="in"/>
      <arg type="i" name="minutes" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="DismissRinging">
      <arg type="x" name="alarm_id" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="StartTimer">
      <arg type="x" name="duration_ms" direction="in"/>
      <arg type="s" name="label" direction="in"/>
      <arg type="x" name="timer_id" direction="out"/>
    </method>
    <method name="PauseTimer">
      <arg type="x" name="timer_id" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="ResumeTimer">
      <arg type="x" name="timer_id" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="ResetTimer">
      <arg type="x" name="timer_id" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="AddMinuteTimer">
      <arg type="x" name="timer_id" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="DeleteTimer">
      <arg type="x" name="timer_id" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="StopTimerRinging">
      <arg type="x" name="timer_id" direction="in"/>
      <arg type="b" name="ok" direction="out"/>
    </method>
    <method name="Resync">
      <arg type="b" name="ok" direction="out"/>
    </method>
    <signal name="StateChanged"/>
    <signal name="RingingChanged">
      <arg type="x" name="entry_id"/>
      <arg type="b" name="ringing"/>
      <arg type="s" name="kind"/>
      <arg type="s" name="label"/>
    </signal>
  </interface>
</node>"""


def alarm_to_dict(alarm: Alarm) -> dict:
    return {"id": alarm.id, "hour": alarm.hour, "minute": alarm.minute,
            "days_mask": alarm.days_mask, "label": alarm.label,
            "enabled": alarm.enabled, "vibrate": alarm.vibrate,
            "sound_uri": alarm.sound_uri}


def alarm_from_dict(data: dict, alarm_id: int) -> Alarm | None:
    try:
        hour, minute = int(data["hour"]), int(data["minute"])
        if hour not in range(24) or minute not in range(60):
            return None
        return Alarm(id=alarm_id, hour=hour, minute=minute,
                     days_mask=int(data.get("days_mask", 0)) & 0x7F,
                     label=str(data.get("label", ""))[:120],
                     enabled=bool(data.get("enabled", True)),
                     vibrate=bool(data.get("vibrate", True)),
                     sound_uri=data.get("sound_uri") or None)
    except Exception:
        return None


class Api:
    """Plain-value implementation behind the D-Bus surface (bus-free tests)."""

    def __init__(self, store, settings=None, clock=None, hooks=None) -> None:
        from .coordinator import Coordinator, Hooks
        self._store = store
        self._settings = settings or prefs.load()
        self._clock = clock or (lambda: int(_time.time() * 1000))
        self._hooks = hooks or Hooks()
        self.coordinator = Coordinator(store, self._clock, self._hooks)

    def get_state(self) -> dict:
        from . import next_armed
        store = self._store
        alarms = [alarm_to_dict(a) for a in store.alarms()]
        timers = [{"id": t.id, "duration_ms": t.duration_ms, "state": t.state,
                   "ends_at_ms": t.ends_at_ms, "remaining_ms": t.remaining_ms,
                   "label": t.label} for t in store.timers()]
        ringing_id = store.ringing_id()
        ringing = None
        if ringing_id is not None:
            alarm = store.get_alarm(ringing_id)
            timer = store.get_timer(ringing_id)
            if alarm is not None:
                ringing = {"id": ringing_id, "kind": "alarm", "label": alarm.label}
            elif timer is not None:
                ringing = {"id": ringing_id, "kind": "timer", "label": timer.label}
        now = self._clock()
        upcoming = next_armed.next_armed(
            store.alarms(), store.snoozed_until, store.scheduled_fire, now)
        state = {"alarms": alarms, "timers": timers, "ringing": ringing, "next": None}
        if upcoming is not None:
            alarm, at = upcoming
            state["next"] = {"id": alarm.id, "label": alarm.label,
                             "fire_at_ms": at, "fire_text": fmt.format_time(at),
                             "until_text": fmt.until(now, at)}
        return state

    def save_alarm(self, data: dict) -> int:
        alarm_id = int(data.get("id", 0) or 0)
        if alarm_id and self._store.get_alarm(alarm_id) is None:
            return -1
        if not alarm_id:
            alarm_id = self._store.next_id()
        alarm = alarm_from_dict(data, alarm_id)
        if alarm is None:
            return -1
        self.coordinator.save_alarm(alarm)
        return alarm_id

    def delete_alarm(self, alarm_id: int) -> bool:
        if self._store.get_alarm(alarm_id) is None:
            return False
        self.coordinator.delete_alarm(alarm_id)
        return True

    def toggle_alarm(self, alarm_id: int, enabled: bool) -> bool:
        if self._store.get_alarm(alarm_id) is None:
            return False
        self.coordinator.toggle_alarm(alarm_id, enabled)
        return True

    def snooze_ringing(self, alarm_id: int, minutes: int) -> bool:
        if self._store.get_alarm(alarm_id) is None:
            return False
        self.coordinator.snooze(
            alarm_id, api_validate.snooze_minutes(minutes, self._settings.snooze_minutes))
        return True

    def dismiss_ringing(self, alarm_id: int) -> bool:
        self.coordinator.dismiss(alarm_id)
        return True

    def start_timer(self, duration_ms: int, label: str) -> int:
        if not 1000 <= int(duration_ms) <= 86_400_000:
            return -1
        timer = self.coordinator.start_or_reuse_timer(
            int(duration_ms), (label or "").strip()[:120])
        return timer.id

    def _timer_ok(self, timer_id: int, action: str) -> bool:
        if self._store.get_timer(timer_id) is None:
            return False
        getattr(self.coordinator, action)(timer_id)
        return True

    def pause_timer(self, timer_id: int) -> bool:
        return self._timer_ok(timer_id, "pause_timer")

    def resume_timer(self, timer_id: int) -> bool:
        return self._timer_ok(timer_id, "resume_timer")

    def reset_timer(self, timer_id: int) -> bool:
        return self._timer_ok(timer_id, "stop_timer")

    def add_minute_timer(self, timer_id: int) -> bool:
        return self._timer_ok(timer_id, "add_minute")

    def delete_timer(self, timer_id: int) -> bool:
        return self._timer_ok(timer_id, "delete_timer")

    def stop_timer_ringing(self, timer_id: int) -> bool:
        return self._timer_ok(timer_id, "stop_timer")


class ClockDaemon:
    def __init__(self, api: Api | None = None, glib=None) -> None:
        import gi
        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib
        self._Gio, self._GLib = Gio, GLib
        self._glib = glib or GLib
        if api is None:
            from .store import Store
            store = Store()
            settings = prefs.load()
            hooks = self._production_hooks(store, settings)
            api = Api(store, settings=settings, hooks=hooks)
        self.api = api
        store = api._store
        self._sched = scheduler.Scheduler(self._glib, api._clock)
        api._hooks.schedule_alarm = self._on_schedule_alarm
        api._hooks.cancel_alarm = self._on_cancel_alarm
        api._hooks.schedule_timers = self._on_schedule_timers
        previous_refreshed = api._hooks.refreshed
        api._hooks.refreshed = lambda: (previous_refreshed(), self.emit_state_changed())
        self._bus = None
        self._name_id = 0
        self._auto_silence_source: int | None = None
        applog.log("daemon", "starting; reconcile")
        api.coordinator.reconcile(force=True, recover_ringing=True)
        self._reschedule_all()

    def _production_hooks(self, store, settings):
        from .coordinator import Hooks
        from . import notify as notify_module
        from . import ringer as ringer_module
        from . import ring_candidates
        notifier = notify_module.Notifier(on_action=self._on_notification_action)
        ringer = ringer_module.Ringer()

        def _ring_start(entry_id: int, is_alarm: bool) -> bool:
            label = ""
            candidates: list[str] = []
            if is_alarm:
                alarm = store.get_alarm(entry_id)
                if alarm is None:
                    return False
                label = alarm.label
                candidates = ring_candidates.ring_candidates(alarm.sound_uri, None, None)
            else:
                timer = store.get_timer(entry_id)
                if timer is None:
                    return False
                label = timer.label
            started = ringer.start(candidates, gradual=settings.gradual_volume)
            if is_alarm:
                notifier.ring_alarm(label, fmt.format_time(int(_time.time() * 1000)))
            else:
                notifier.ring_timer(label)
            self.emit_ringing_changed(entry_id, True, "alarm" if is_alarm else "timer", label)
            self._arm_auto_silence(entry_id, is_alarm)
            return started

        def _ring_stop() -> None:
            ringer.stop()
            self._disarm_auto_silence()

        def _missed(entry_id: int, title: str) -> None:
            notifier.missed(title if title != TITLE_MISSED_ALARM else "Missed alarm")
            self.emit_state_changed()

        return Hooks(ring_start=_ring_start, ring_stop=_ring_stop,
                     show_alert=lambda kind, eid: self.emit_state_changed(),
                     missed=_missed)

    def _on_notification_action(self, action: str, _nid: int) -> None:
        ringing = self.api._store.ringing_id()
        if ringing is None:
            return
        if action == "snooze":
            self.api.snooze_ringing(ringing, self.api._settings.snooze_minutes)
        elif action == "dismiss":
            self.api.dismiss_ringing(ringing)
        elif action == "stop":
            self.api.stop_timer_ringing(ringing)
        elif action == "addminute":
            self.api.add_minute_timer(ringing)

    def _arm_auto_silence(self, entry_id: int, is_alarm: bool) -> None:
        from .prefs import AUTO_SILENCE_MINUTES
        self._disarm_auto_silence()

        def _silence() -> bool:
            self._auto_silence_source = None
            self.api.coordinator.auto_silence(entry_id, is_alarm)
            return False

        self._auto_silence_source = self._glib.timeout_add(
            AUTO_SILENCE_MINUTES * 60_000, _silence)

    def _disarm_auto_silence(self) -> None:
        if self._auto_silence_source is not None:
            try:
                self._glib.source_remove(self._auto_silence_source)
            except Exception:
                pass
            self._auto_silence_source = None

    def _on_schedule_alarm(self, alarm: Alarm, at_ms: int) -> None:
        self.api._store.set_scheduled_fire(alarm.id, at_ms)
        self._sched.schedule_alarm(alarm, at_ms, self._fire_alarm)

    def _on_cancel_alarm(self, alarm_id: int) -> None:
        self.api._store.clear_scheduled_fire(alarm_id)
        self._sched.cancel_alarm(alarm_id)

    def _on_schedule_timers(self) -> None:
        self._sched.schedule_timers(self.api._store.timers(), self._fire_timer)

    def _fire_alarm(self, alarm_id: int) -> None:
        self.api.coordinator.fire_alarm(alarm_id)
        self._reschedule_all()

    def _fire_timer(self, timer_id: int) -> None:
        self.api.coordinator.fire_timer(timer_id)
        self._reschedule_all()

    def _reschedule_all(self) -> None:
        for alarm in self.api._store.alarms():
            fire_at = self.api._store.scheduled_fire(alarm.id)
            snoozed = self.api._store.snoozed_until(alarm.id)
            at = max(fire_at, snoozed)
            if (alarm.enabled or snoozed > self.api._clock()) and at > self.api._clock():
                self._sched.schedule_alarm(alarm, at, self._fire_alarm)
        self._on_schedule_timers()

    def emit_state_changed(self) -> None:
        if self._bus is None:
            return
        try:
            self._bus.emit_signal(None, DAEMON_OBJECT_PATH, DAEMON_INTERFACE,
                                  "StateChanged", None)
        except Exception:
            pass

    def emit_ringing_changed(self, entry_id: int, ringing: bool, kind: str,
                             label: str) -> None:
        if self._bus is None:
            return
        try:
            params = self._GLib.Variant("(xbss)", (entry_id, ringing, kind, label))
            self._bus.emit_signal(None, DAEMON_OBJECT_PATH, DAEMON_INTERFACE,
                                  "RingingChanged", params)
        except Exception:
            pass

    def _on_method(self, _conn, _sender, _path, _iface, method, params, invocation):
        GLib = self._GLib
        try:
            result = self._dispatch(method, params.unpack())
        except Exception as exc:
            applog.log("daemon", f"method {method} failed: {exc}")
            invocation.return_error_literal(GLib.quark_from_string("xx-clock"), 1, "failed")
            return
        invocation.return_value(result)

    def _dispatch(self, method: str, args: tuple):
        GLib = self._GLib
        api = self.api
        if method == "GetState":
            return GLib.Variant("(s)", (json.dumps(api.get_state()),))
        if method == "SaveAlarm":
            try:
                data = json.loads(args[0])
            except Exception:
                data = {}
            return GLib.Variant("(x)", (api.save_alarm(data),))
        if method == "DeleteAlarm":
            return GLib.Variant("(b)", (api.delete_alarm(int(args[0])),))
        if method == "ToggleAlarm":
            return GLib.Variant("(b)", (api.toggle_alarm(int(args[0]), bool(args[1])),))
        if method == "SnoozeRinging":
            return GLib.Variant("(b)", (api.snooze_ringing(int(args[0]), int(args[1])),))
        if method == "DismissRinging":
            return GLib.Variant("(b)", (api.dismiss_ringing(int(args[0])),))
        if method == "StartTimer":
            return GLib.Variant("(x)", (api.start_timer(int(args[0]), str(args[1])),))
        if method == "PauseTimer":
            return GLib.Variant("(b)", (api.pause_timer(int(args[0])),))
        if method == "ResumeTimer":
            return GLib.Variant("(b)", (api.resume_timer(int(args[0])),))
        if method == "ResetTimer":
            return GLib.Variant("(b)", (api.reset_timer(int(args[0])),))
        if method == "AddMinuteTimer":
            return GLib.Variant("(b)", (api.add_minute_timer(int(args[0])),))
        if method == "DeleteTimer":
            return GLib.Variant("(b)", (api.delete_timer(int(args[0])),))
        if method == "StopTimerRinging":
            return GLib.Variant("(b)", (api.stop_timer_ringing(int(args[0])),))
        if method == "Resync":
            api.coordinator.reconcile(force=True, recover_ringing=False)
            self._reschedule_all()
            return GLib.Variant("(b)", (True,))
        raise ValueError(f"unknown method {method}")

    def run(self, connection=None) -> int:
        Gio, GLib = self._Gio, self._GLib
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        loop = GLib.MainLoop()
        self._loop = loop
        state: dict = {}

        def _acquired(conn, _name) -> None:
            self._bus = conn
            conn.register_object(DAEMON_OBJECT_PATH, node.interfaces[0], self._on_method)
            state["ok"] = True

        def _lost(_conn, _name) -> None:
            state["lost"] = True
            loop.quit()

        if connection is None:
            self._name_id = Gio.bus_own_name(
                Gio.BusType.SESSION, DAEMON_BUS_NAME, Gio.BusNameOwnerFlags.NONE,
                _acquired, lambda c, n: None, _lost)
        else:
            Gio.bus_own_name_on_connection(connection, DAEMON_BUS_NAME,
                                           Gio.BusNameOwnerFlags.NONE,
                                           _acquired, _lost)
        applog.log("daemon", f"owning {DAEMON_BUS_NAME}")
        try:
            loop.run()
        finally:
            if self._name_id:
                Gio.bus_unown_name(self._name_id)
                self._name_id = 0
        return 0 if state.get("ok") and not state.get("lost") else 1
