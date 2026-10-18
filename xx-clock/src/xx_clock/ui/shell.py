"""Main window: four tabs (Clock/Alarms/Timers/Setup) over a toast overlay."""

from __future__ import annotations

import time as _time

from .. import fmt, prefs as prefs_module
from ..service_client import call_async
from . import alarm_dialog, alarms_page, clock_page, ring_card, setup_page, timers_page

TABS = ("clock", "alarms", "timers", "setup")

APP_CSS = (clock_page.APP_CSS_EXTRA + timers_page.APP_CSS_EXTRA
           + alarm_dialog.APP_CSS) + """
.xx-clock-alarm-time { font-size: 30px; font-weight: 400; }
.xx-clock-off { opacity: 0.45; }
"""


class Calls:
    def __init__(self, shell) -> None:
        self._shell = shell

    def toggle_alarm(self, alarm_id: int, enabled: bool) -> None:
        self._shell._mutate(lambda svc: svc.toggle_alarm(alarm_id, enabled))

    def delete_alarm(self, alarm_id: int) -> None:
        self._shell._mutate(lambda svc: svc.delete_alarm(alarm_id))

    def open_editor(self, draft: dict | None) -> None:
        self._shell.open_editor(draft)

    def start_timer(self, duration_ms: int, label: str) -> None:
        self._shell._mutate(lambda svc: svc.start_timer(duration_ms, label))

    def pause_timer(self, timer_id: int) -> None:
        self._shell._mutate(lambda svc: svc.pause_timer(timer_id))

    def resume_timer(self, timer_id: int) -> None:
        self._shell._mutate(lambda svc: svc.resume_timer(timer_id))

    def reset_timer(self, timer_id: int) -> None:
        self._shell._mutate(lambda svc: svc.reset_timer(timer_id))

    def add_minute_timer(self, timer_id: int) -> None:
        self._shell._mutate(lambda svc: svc.add_minute_timer(timer_id))

    def delete_timer(self, timer_id: int) -> None:
        self._shell._mutate(lambda svc: svc.delete_timer(timer_id))

    def snooze_ringing(self, alarm_id: int, minutes: int) -> None:
        self._shell._mutate(lambda svc: svc.snooze_ringing(alarm_id, minutes))

    def dismiss_ringing(self, alarm_id: int) -> None:
        self._shell._mutate(lambda svc: svc.dismiss_ringing(alarm_id))

    def stop_timer_ringing(self, timer_id: int) -> None:
        self._shell._mutate(lambda svc: svc.stop_timer_ringing(timer_id))

    def set_snooze(self, minutes: int) -> None:
        self._shell.set_snooze(minutes)

    def set_gradual(self, enabled: bool) -> None:
        self._shell.set_gradual(enabled)

    def test_sound(self) -> None:
        self._shell.test_sound()

    def show_licenses(self) -> None:
        self._shell.show_licenses()


class ClockWindow:
    def __init__(self, app, service, settings) -> None:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gdk, GLib, Gtk, Pango
        self._Adw, self._Gdk, self._GLib = Adw, Gdk, GLib
        self._Gtk, self._Pango = Gtk, Pango
        import xx_ui
        self._xx_ui = xx_ui
        self.service = service
        self.settings = settings
        self.calls = Calls(self)
        self._state: dict = {"alarms": [], "timers": [], "ringing": None, "next": None}
        self._tab = settings.last_tab if settings.last_tab in TABS else "clock"

        self.win = Adw.ApplicationWindow(application=app)
        self.win.set_title("xx-clock")
        self.win.set_default_size(360, 720)
        # Adw windows floor at 360 px wide by default: a 320 px
        # screen would cut the app off.
        self.win.set_size_request(320, 200)
        try:
            xx_ui.apply(self.win)
            self._theme_unsub = xx_ui.on_change(lambda _t: xx_ui.apply(self.win))
        except Exception:
            self._theme_unsub = None
        provider = Gtk.CssProvider()
        provider.load_from_data(APP_CSS.encode("utf-8"))
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.clock = clock_page.ClockPage(Gtk, xx_ui.widgets, settings,
                                          self.toggle_clock_style)
        self.alarms = alarms_page.AlarmsPage(Gtk, Pango, xx_ui.widgets, service,
                                             settings, self.calls)
        self.timers = timers_page.TimersPage(Gtk, Pango, xx_ui.widgets, self.calls)
        self.setup = setup_page.SetupPage(Gtk, xx_ui.widgets, settings, self.calls)
        self._pages = {"clock": self.clock, "alarms": self.alarms,
                       "timers": self.timers, "setup": self.setup}
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self._stack.set_vexpand(True)
        for name, page in self._pages.items():
            self._stack.add_named(page.widget, name)
        self._tabs = xx_ui.widgets.bottom_tabs(
            [("", name.capitalize(), name == self._tab,
              lambda n=name: self.show_tab(n)) for name in TABS])
        column = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        column.append(self._stack)
        column.append(self._tabs)
        self._toasts = Adw.ToastOverlay()
        self._toasts.set_child(column)
        self.win.set_content(self._toasts)
        self._ring: ring_card.RingCard | None = None
        self._editor = None
        self._seeded = False
        self.show_tab(self._tab)
        GLib.timeout_add(1000, self._tick)
        GLib.timeout_add(500, self._tick_timers)
        self.refresh()

    def show_tab(self, name: str) -> None:
        if name not in TABS:
            return
        self._tab = name
        self._stack.set_visible_child_name(name)
        box = self._tabs
        child = box.get_first_child()
        for tab_name in TABS:
            if child is not None:
                if tab_name == name:
                    child.add_css_class("active")
                else:
                    child.remove_css_class("active")
                child = child.get_next_sibling()
        self.settings.last_tab = name
        try:
            prefs_module.save(self.settings)
        except Exception:
            pass

    def open_sheet(self, name: str) -> None:
        self.show_tab(name)

    def present(self) -> None:
        self.win.present()

    def toast(self, message: str) -> None:
        try:
            self._toasts.add_toast(self._Adw.Toast.new(message))
        except Exception:
            pass

    def refresh(self) -> None:
        service = self.service

        def _work():
            return service.get_state(), self.daemon_status()

        def _done(outcome) -> None:
            if outcome[0] != "ok":
                return
            state, status = outcome[1]
            try:
                self.setup.refresh(status)
            except Exception:
                pass
            if self._seeded:
                return
            self._apply_state(state)

        call_async(_work, _done)

    def refresh_sync(self, state: dict) -> None:
        self._apply_state(state)

    def _apply_state(self, state: dict) -> None:
        self._state = state or self._state
        try:
            self.clock.refresh(self._state)
            self.alarms.refresh(self._state)
            self.timers.refresh(self._state)
        except Exception:
            pass
        ringing = self._state.get("ringing")
        if ringing:
            self._show_ring(ringing)
        elif self._ring is not None:
            self._ring.hide()

    def _mutate(self, work) -> None:
        service = self.service

        def _done(outcome) -> None:
            self.refresh()

        call_async(lambda: work(service), _done)

    def _tick(self) -> bool:
        try:
            self.clock.tick()
        except Exception:
            pass
        return True

    def _tick_timers(self) -> bool:
        try:
            self.timers.tick()
        except Exception:
            pass
        return True

    def toggle_clock_style(self) -> None:
        self.settings.clock_style = (
            "analog" if self.settings.clock_style == "digital" else "digital")
        try:
            prefs_module.save(self.settings)
        except Exception:
            pass
        self.clock.apply_style()

    def set_snooze(self, minutes: int) -> None:
        self.settings.snooze_minutes = max(1, min(120, minutes))
        try:
            prefs_module.save(self.settings)
        except Exception:
            pass

    def set_gradual(self, enabled: bool) -> None:
        self.settings.gradual_volume = enabled
        try:
            prefs_module.save(self.settings)
        except Exception:
            pass

    def test_sound(self) -> None:
        from .. import ringer as ringer_module
        # Plays three seconds of the alarm sound; only a failure speaks.
        try:
            ring = ringer_module.Ringer()
            started = ring.start([], gradual=False)
        except Exception:
            started = False
        if not started:
            self.toast("No sound output found")
            return
        self._GLib.timeout_add(3000, lambda: (ring.stop(), False)[1])

    def show_licenses(self) -> None:
        widgets = self._xx_ui.widgets
        widgets.show_licenses_dialog(
            self.win, "xx-clock", widgets.standard_license_entries("xx-clock"))

    def open_editor(self, draft: dict | None) -> None:
        import gi
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Gtk, Pango = self._Gtk, self._Pango
        if draft is None:
            slots = int(_time.time() // 60) + 1
            rounded = ((slots + 29) // 30) * 30
            draft = {"hour": (rounded // 60) % 24, "minute": rounded % 60,
                     "days_mask": 0, "label": "", "enabled": True,
                     "vibrate": True, "sound_uri": None}
        self._editor = alarm_dialog.AlarmDialog(
            Adw, Gtk, Pango, self._xx_ui.widgets, draft,
            self._save_editor, self.win)

    def _save_editor(self, draft: dict) -> None:
        service = self.service

        def _work():
            service.save_alarm(draft)
            return service.get_state()

        def _done(outcome) -> None:
            # Saving is a direct action: the updated list is the answer,
            # no "Alarm set for" toast.
            if outcome[0] == "ok":
                self._apply_state(outcome[1])

        call_async(_work, _done)

    def daemon_status(self) -> dict:
        import subprocess
        running = bool(getattr(self.service, "daemon_available", False))
        autostart = False
        try:
            unit = "xx-clock-daemon.service"
            proc = subprocess.run(["systemctl", "--user", "is-enabled", unit],
                                  capture_output=True, text=True, timeout=5)
            autostart = proc.stdout.strip() == "enabled"
        except Exception:
            pass
        return {"running": running, "autostart": autostart}

    def on_ringing(self, entry_id: int, ringing: bool, kind: str, label: str) -> None:
        GLib = self._GLib

        def _show() -> bool:
            if ringing:
                self._show_ring({"id": entry_id, "kind": kind, "label": label})
            elif self._ring is not None:
                self._ring.hide()
            self.refresh()
            return False

        GLib.idle_add(_show)

    def _show_ring(self, ringing: dict) -> None:
        if self._ring is None:
            self._ring = ring_card.RingCard(self._Gtk, self._Gdk,
                                            self._xx_ui.widgets, self.calls)
        if ringing.get("kind") == "timer":
            self._ring.show_timer(ringing["id"], ringing.get("label", ""))
        else:
            self._ring.show_alarm(ringing["id"], ringing.get("label", ""),
                                  self.settings.snooze_minutes)

    def seed_demo(self) -> None:
        self._seeded = True
        try:
            self.setup.refresh({"running": False, "autostart": False})
        except Exception:
            pass
        state = {
            "alarms": [
                {"id": 1, "hour": 7, "minute": 0, "days_mask": 31,
                 "label": "Work", "enabled": True, "vibrate": True,
                 "sound_uri": None},
                {"id": 2, "hour": 8, "minute": 30, "days_mask": 0,
                 "label": "", "enabled": False, "vibrate": True,
                 "sound_uri": None},
            ],
            "timers": [
                {"id": 11, "duration_ms": 300_000, "state": "RUNNING",
                 "ends_at_ms": int(_time.time() * 1000) + 150_000,
                 "remaining_ms": 0, "label": "Pasta"},
                {"id": 12, "duration_ms": 60_000, "state": "PAUSED",
                 "ends_at_ms": 0, "remaining_ms": 45_000, "label": ""},
            ],
            "ringing": None,
            "next": {"id": 1, "label": "Work", "fire_text": "07:00",
                     "until_text": fmt.until(0, 8 * 3600_000)},
        }
        self._apply_state(state)
