"""Display section: night light and power profile (ref power-manager.sh).

Night light drives wlsunset (preferred) or gammastep as the caller's own
processes — no privilege involved. phoc wires wlroots'
wlr_gamma_control_manager_v1 (wlr-gamma-control-unstable-v1, the protocol
both tools speak), so the backend is viable; the rows hide when neither
tool is installed. Power profiles go through power-profiles-daemon while
it runs with a real driver, else through the root helper's allowlisted
cpufreq governor map; the choice persists and battery saver selects
power-saver. Night light schedules (off/custom/sunset) live in
night_light.py with a one-timeout scheduler.
"""
from __future__ import annotations

import datetime
import shutil
import subprocess

import night_light
import power_profiles
import settings_page
import settings_ui as ui

DEFAULT_TEMP = 2200
MIN_TEMP = 2200
MAX_TEMP = 6500

PROFILE_BUS_NAMES = (
    'org.freedesktop.UPower.PowerProfiles',
    'net.hadess.PowerProfiles',
)
_PROFILE_IFACE = 'net.hadess.PowerProfiles'

_night_proc = None


def night_tool(which=None) -> str | None:
    """'wlsunset', 'gammastep', or None when neither is installed."""
    lookup = which or shutil.which
    if lookup('wlsunset'):
        return 'wlsunset'
    if lookup('gammastep'):
        return 'gammastep'
    return None


def clamp_temp(value) -> int:
    try:
        temp = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TEMP
    return max(MIN_TEMP, min(MAX_TEMP, temp))


def spawn_argv(tool: str, temp: int, lat, lon) -> list[str]:
    """Argv to start the night light. Coordinates select the
    sunset-to-sunrise schedule; without them the low temperature
    applies immediately."""
    temp = clamp_temp(temp)
    if tool == 'gammastep':
        if lat is None or lon is None:
            return ['gammastep', '-O', '%d' % temp]
        return ['gammastep', '-t', '%d:%d' % (temp, MAX_TEMP),
                '-l', '%s:%s' % (lat, lon)]
    argv = ['wlsunset', '-t', '%d' % temp, '-T', '%d' % MAX_TEMP]
    if lat is not None and lon is not None:
        argv += ['-l', str(lat), '-L', str(lon)]
    return argv


def set_night_light(enabled: bool, temp: int = DEFAULT_TEMP,
                    lat=None, lon=None, spawn=None) -> bool:
    """Start (True) or stop the night-light process. The handle is kept
    module-side so stop terminates exactly what was started."""
    global _night_proc
    stop_night_light()
    if not enabled:
        return False
    tool = night_tool()
    if tool is None:
        return False
    argv = spawn_argv(tool, temp, lat, lon)
    run = spawn or subprocess.Popen
    try:
        _night_proc = run(argv, close_fds=True)
    except (OSError, TypeError):
        _night_proc = None
        return False
    return True


def stop_night_light() -> None:
    """Terminate the tracked night-light process, if any."""
    global _night_proc
    proc, _night_proc = _night_proc, None
    if proc is not None:
        try:
            proc.terminate()
        except Exception:
            pass


def night_light_running() -> bool:
    proc = _night_proc
    try:
        return proc is not None and proc.poll() is None
    except Exception:
        return False


def sunrise_sunset(lat: float, lon: float, day_of_year: int) -> tuple:
    """(sunrise, sunset) UTC decimal hours from latitude/longitude (see
    night_light.solar_utc; polar day/night clamps to (0, 0))."""
    rise, set_, _kind = night_light.solar_utc(day_of_year, lat, lon)
    return (rise / 60.0, set_ / 60.0)


def night_active(now_hours: float, sunrise: float, sunset: float) -> bool:
    """True when `now_hours` falls in the night half of the day."""
    if sunrise <= sunset:
        return now_hours < sunrise or now_hours >= sunset
    return sunset <= now_hours < sunrise


def schedule_state(lat, lon, now: datetime.datetime | None = None) -> bool:
    """Whether the sunset-to-sunrise schedule wants the light on now."""
    if lat is None or lon is None:
        return False
    moment = now or datetime.datetime.now()
    rise, set_ = sunrise_sunset(lat, lon, moment.timetuple().tm_yday)
    return night_active(moment.hour + moment.minute / 60.0, rise, set_)


def _profile_bus() -> str | None:
    """First power-profiles bus name with an owner, else None."""
    try:
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except Exception:
        return None
    for name in PROFILE_BUS_NAMES:
        try:
            from gi.repository import GLib
            import settings_device
            owned = settings_device.dbus_call_sync(
                bus, 'org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'NameHasOwner',
                GLib.Variant('(s)', (name,)),
                GLib.VariantType('(b)'), 2000,
            ).unpack()[0]
        except Exception:
            continue
        if owned:
            return name
    return None


def parse_active_profile(reply) -> str | None:
    """ActiveProfile from a Properties.GetAll reply tuple (or None)."""
    try:
        props = reply[0] if reply else {}
    except (TypeError, IndexError):
        return None
    value = (props or {}).get('ActiveProfile')
    return str(value) if value else None


def _profile_path(name: str) -> str:
    return ('/org/freedesktop/UPower/PowerProfiles'
            if name == PROFILE_BUS_NAMES[0]
            else '/net/hadess/PowerProfiles')


def read_power_full() -> tuple[str | None, dict]:
    """(bus_name, GetAll properties) or (None, {}): worker only."""
    try:
        from gi.repository import Gio, GLib
        import settings_device
        name = _profile_bus()
        if name is None:
            return None, {}
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        props = settings_device.dbus_call_sync(
            bus, name, _profile_path(name),
            'org.freedesktop.DBus.Properties', 'GetAll',
            GLib.Variant('(s)', (_PROFILE_IFACE,)),
            GLib.VariantType('(a{sv})'), 3000,
        ).unpack()[0]
        return name, dict(props or {})
    except Exception:
        return None, {}


def read_power_profile() -> tuple[str | None, list[str]]:
    """(bus_name, profiles) or (None, []): worker only, never raises."""
    name, props = read_power_full()
    profiles = [str(entry.get('Profile', ''))
                for entry in (props.get('Profiles') or [])
                if isinstance(entry, dict) and entry.get('Profile')]
    return name, profiles


def probe_power_state() -> dict:
    """One worker-side snapshot: backend ('ppd'/'helper'/'none'), the
    daemon bus/profiles when present, and the live active profile."""
    name, props = read_power_full()
    backend = power_profiles.select_backend(
        name, props, power_profiles.cpufreq_present())
    active = parse_active_profile((props,)) if props else None
    profiles = [str(entry.get('Profile', ''))
                for entry in (props.get('Profiles') or [])
                if isinstance(entry, dict) and entry.get('Profile')]
    return {'backend': backend, 'bus': name if backend == 'ppd' else None,
            'profiles': profiles, 'active': active}


def apply_profile_async(profile: str, backend: str, bus: str | None,
                        on_done) -> None:
    """Apply a profile off the main loop: the daemon synchronously in a
    worker, the helper through its async client. on_done(ok) runs on
    the main loop."""
    if backend == 'ppd' and bus:
        from settings_page import _run_bg

        def _work():
            return set_power_profile(bus, profile)

        def _done(ok) -> None:
            try:
                on_done(bool(ok))
            except Exception:
                pass

        _run_bg(_work, _done)
        return
    try:
        import helper_client
    except ImportError:
        try:
            on_done(False)
        except Exception:
            pass
        return

    def _done(ok: bool, _result) -> None:
        try:
            on_done(bool(ok))
        except Exception:
            pass

    try:
        helper_client.set_power_profile(profile, _done)
    except Exception:
        _done(False, None)


def reapply_power_profile(config, saver_on: bool,
                          on_done=None) -> None:
    """Session-start (and saver-change) enforcement of the effective
    profile. Never raises; backend probing stays on a worker."""
    from settings_page import _run_bg

    wanted = power_profiles.effective_profile(config, saver_on)

    def _work():
        return probe_power_state()

    def _done(state) -> None:
        try:
            if not isinstance(state, dict):
                return
            if state.get('backend') == 'none':
                return
            apply_profile_async(
                wanted, state.get('backend'), state.get('bus'),
                on_done or (lambda _ok: None))
        except Exception:
            pass

    try:
        _run_bg(_work, _done)
    except Exception:
        pass


def set_power_profile(name: str, profile: str) -> bool:
    """Apply a power profile; worker only, False on any failure."""
    try:
        from gi.repository import Gio, GLib
        import settings_device
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        settings_device.dbus_call_sync(
            bus, name, '/org/freedesktop/UPower/PowerProfiles'
            if name == PROFILE_BUS_NAMES[0]
            else '/net/hadess/PowerProfiles',
            'org.freedesktop.DBus.Properties', 'Set',
            GLib.Variant('(ssv)', (_PROFILE_IFACE, 'ActiveProfile',
                                   GLib.Variant('s', str(profile)))),
            None, 3000,
        )
        return True
    except Exception:
        return False


def tile_get_night() -> bool | None:
    """Quick-tile state: None hides the tile when no tool is installed."""
    if night_tool() is None:
        return None
    return night_light_running()


def tile_set_night(enabled: bool) -> None:
    """Manual tile flip: holds only until the next schedule boundary."""
    scheduler = night_light.get_scheduler()
    if scheduler is not None:
        try:
            scheduler.manual_set(bool(enabled))
            return
        except Exception:
            pass
    set_night_light(bool(enabled))


def night_coords(win):
    """Shell location for sunset mode: manual coordinates, the
    ``lat,lon`` string, then the cached automatic fix (same order as
    the weather widget). Sync file reads only, never the network."""
    try:
        import weather
        return weather.WeatherProvider(win.config).coordinates()
    except Exception:
        pass
    try:
        lat = win.config.data.get('weather_lat')
        lon = win.config.data.get('weather_lon')
        if lat is not None and lon is not None:
            return (float(lat), float(lon))
    except (TypeError, ValueError, AttributeError):
        pass
    except Exception:
        pass
    return None


def _scheduler_apply(win, enabled: bool) -> None:
    """Scheduler callback: express the wanted state without flapping
    the wlsunset/gammastep process when it already matches."""
    try:
        running = night_light_running()
    except Exception:
        running = False
    if bool(enabled) == bool(running):
        return
    try:
        mode = night_light.mode_of(win.config)
        temp = clamp_temp(_config_get(win, 'night_light_temp',
                                      DEFAULT_TEMP))
    except Exception:
        mode, temp = 'off', DEFAULT_TEMP
    lat = lon = None
    if mode == 'sunset':
        try:
            coords = night_coords(win)
            if coords is not None:
                lat, lon = coords
        except Exception:
            lat, lon = None, None
    set_night_light(bool(enabled), temp, lat, lon)


def _config_get(win, key: str, default):
    try:
        get = getattr(win.config, 'get', None)
        if callable(get):
            return get(key, default)
        return win.config.data.get(key, default)
    except Exception:
        return default


def ensure_night_scheduler(win):
    """Process-wide scheduler bound to this window's live config. One
    GLib timeout at a time; safe to call on every refresh."""
    from gi.repository import GLib

    def _arm(delay_s, callback):
        return GLib.timeout_add(int(max(1.0, delay_s)) * 1000,
                                _scheduler_fire, callback)

    def _scheduler_fire(callback) -> bool:
        try:
            callback()
        except Exception:
            pass
        return False

    def _disarm(handle) -> None:
        try:
            GLib.source_remove(handle)
        except Exception:
            pass

    scheduler = night_light.get_scheduler()
    if scheduler is None:
        scheduler = night_light.configure_scheduler(
            get_mode=lambda: night_light.mode_of(win.config),
            get_custom=lambda: night_light.custom_range(win.config),
            get_coords=lambda: night_coords(win),
            apply=lambda on: _scheduler_apply(win, on),
            arm=_arm, disarm=_disarm,
        )
    try:
        return scheduler.refresh('ensure')
    except Exception:
        return None


def refresh_night_scheduler(reason: str = '') -> None:
    """Recompute the schedule after resume or config change outside the
    Display page. No-op until the scheduler exists."""
    scheduler = night_light.get_scheduler()
    if scheduler is None:
        return
    try:
        scheduler.refresh(reason)
    except Exception:
        pass


class DisplaySection(settings_page._Section):
    """Night light (wlsunset/gammastep) plus the power-profile picker."""

    key = 'display'
    title = 'Night light'

    def __init__(self) -> None:
        self._status = None
        self._night_box = None
        self._profile_box = None
        self._profile_row = None
        self._schedule_row = None
        self._from_row = None
        self._to_row = None
        self._updating = False
        self._profile_name: str | None = None
        self._profiles: list[str] = []
        self._backend: str = 'none'
        self._active_profile: str | None = None

    def build(self, win) -> list:
        night = ui.group(self.title)
        self._night_box = night[1]
        self._profile_row = ui.nav_row(
            'Power profile', 'Checking…', lambda r: self._show_profiles(win))
        power = ui.group('Performance', [self._profile_row], note='')
        self._profile_box = power[1]
        self._status = power[2]
        self._render_night(win)
        bar = ui.group('Status bar', [self._status_bar_row(win)],
                       note='Keeps apps below the front camera.')
        return night + power + bar

    def refresh(self, win) -> None:
        from settings_page import _run_bg

        def _work():
            state = probe_power_state()
            state['tool'] = night_tool()
            return state

        _run_bg(_work, lambda res: self._on_probed(win, res))
        try:
            ensure_night_scheduler(win)
        except Exception:
            pass

    def _on_probed(self, win, res) -> None:
        if not isinstance(res, dict):
            return
        backend = res.get('backend') or 'none'
        self._backend = backend if backend in ('ppd', 'helper') else 'none'
        self._profile_name = res.get('bus')
        self._profiles = list(res.get('profiles') or [])
        self._active_profile = res.get('active')
        self._render_night(win)
        self._render_profiles(win)

    def _saver_on(self, win) -> bool:
        try:
            import settings_standby
            return bool(settings_standby.saver_is_on(win))
        except Exception:
            return False

    def _config(self, win, key: str, default):
        try:
            get = getattr(win.config, 'get', None)
            if callable(get):
                return get(key, default)
            return win.config.data.get(key, default)
        except Exception:
            return default

    def _render_night(self, win) -> None:
        from gi.repository import Gtk
        import settings_launcher

        if self._night_box is None:
            return
        if night_tool() is None:
            ui.fill(self._night_box, [ui.row(
                'Night light', 'Needs wlsunset or gammastep installed')])
            return
        temp = clamp_temp(self._config(win, 'night_light_temp',
                                       DEFAULT_TEMP))
        night = ui.switch_row('Night light', 'Warm, easier on the eyes',
                              night_light_running())
        night.switch.connect('notify::active',
                             lambda sw, _p: self._on_night_flipped(win, sw))
        self._night_row = night
        scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, MIN_TEMP, MAX_TEMP, 100)
        scale.set_value(float(temp))
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        warmth = settings_launcher._scale_row(
            'Color temperature', '%d K' % temp, scale)
        scale.connect('value-changed',
                      lambda adj: self._on_temp_changed(win, adj, warmth))
        mode = night_light.mode_of(win.config)
        coords = night_coords(win)
        self._schedule_row = ui.nav_row(
            'Schedule', self._schedule_subtitle(win, mode, coords),
            lambda r: self._pick_schedule(win, r))
        rows = [night, warmth, self._schedule_row]
        if mode != 'off' and (mode == 'custom' or coords is None):
            from_min, to_min = night_light.custom_range(win.config)
            self._from_row = ui.nav_row(
                'From', night_light.format_hhmm(from_min),
                lambda r: self._pick_time(win, r, 'from'))
            self._to_row = ui.nav_row(
                'To', night_light.format_hhmm(to_min),
                lambda r: self._pick_time(win, r, 'to'))
            rows += [self._from_row, self._to_row]
        ui.fill(self._night_box, rows)

    def _schedule_subtitle(self, win, mode: str, coords) -> str:
        if mode == 'custom':
            from_min, to_min = night_light.custom_range(win.config)
            return '%s – %s' % (night_light.format_hhmm(from_min),
                                night_light.format_hhmm(to_min))
        if mode == 'sunset':
            if coords is None:
                return night_light.NO_LOCATION_TEXT
            return 'Sunset to sunrise'
        return 'Off'

    def _render_profiles(self, win) -> None:
        if self._profile_box is None:
            return
        if self._backend == 'none':
            ui.fill(self._profile_box, [ui.row(
                'Power profile', 'Not available on this device')])
            return
        ui.set_subtitle(self._profile_row,
                        self._current_profile(win) or 'Unknown')
        ui.fill(self._profile_box, [self._profile_row])

    def _show_profiles(self, win) -> None:
        choices = power_profiles.available_choices(
            self._backend, self._profiles)
        actions = [(power_profiles.profile_label(profile),
                    lambda profile=profile: self._on_profile(win, profile))
                   for profile in choices]
        win.item_actions._show_action_menu(
            self._profile_row, 'Power profile', actions)

    def _current_profile(self, win) -> str:
        return power_profiles.profile_label(power_profiles.current_label(
            self._active_profile, win.config, self._saver_on(win)))

    def _coords(self, win):
        try:
            coords = night_coords(win)
            if coords is not None:
                return coords
        except Exception:
            pass
        try:
            return (win.config.data.get('weather_lat'),
                    win.config.data.get('weather_lon'))
        except Exception:
            return (None, None)

    def _sunset_active(self, win) -> bool:
        return night_light.mode_of(win.config) == 'sunset'

    def _on_night_flipped(self, win, switch) -> None:
        if self._updating:
            return
        enabled = bool(switch.get_active())
        mode = night_light.mode_of(win.config)
        if mode != 'off':
            scheduler = night_light.get_scheduler()
            if scheduler is not None:
                try:
                    scheduler.manual_set(enabled)
                    win.config.set('night_light', bool(enabled))
                    return
                except Exception:
                    pass
        lat, lon = self._coords(win)
        scheduled = self._sunset_active(win)
        if enabled and scheduled:
            enabled = schedule_state(lat, lon)
        temp = clamp_temp(self._config(win, 'night_light_temp',
                                       DEFAULT_TEMP))
        ok = set_night_light(enabled, temp,
                             lat if scheduled else None,
                             lon if scheduled else None)
        try:
            win.config.set('night_light', bool(enabled and ok))
        except Exception:
            pass
        self._say('' if ok or not enabled else 'Could not start night light.')

    def _on_temp_changed(self, win, scale, row=None) -> None:
        temp = clamp_temp(scale.get_value())
        if row is not None:
            row.value_label.set_text('%d K' % temp)
        try:
            win.config.set('night_light_temp', temp)
        except Exception:
            pass
        if night_light_running():
            lat, lon = self._coords(win)
            scheduled = self._sunset_active(win)
            set_night_light(True, temp,
                            lat if scheduled else None,
                            lon if scheduled else None)

    def _pick_schedule(self, win, anchor) -> None:
        labels = (('Off', 'off'), ('Custom times', 'custom'),
                  ('Sunset to sunrise', 'sunset'))
        win.item_actions._show_action_menu(
            anchor, 'Schedule',
            [(label, lambda mode=mode: self._set_mode(win, mode))
             for label, mode in labels])

    def _set_mode(self, win, mode: str) -> None:
        if mode not in night_light.MODES:
            return
        try:
            win.config.set('night_light_mode', mode)
            win.config.set('night_light_schedule', mode == 'sunset')
        except Exception:
            pass
        self._render_night(win)
        refresh_night_scheduler('mode')

    def _pick_time(self, win, anchor, which: str) -> None:
        from_min, to_min = night_light.custom_range(win.config)
        current = night_light.format_hhmm(
            from_min if which == 'from' else to_min)
        title = 'From time' if which == 'from' else 'To time'

        def _commit(values) -> str | None:
            picked = night_light.parse_hhmm(
                (values.get('time') or '').strip())
            if picked is None:
                return 'Use HH:MM, e.g. 22:00.'
            try:
                win.config.set(
                    'night_light_from' if which == 'from'
                    else 'night_light_to',
                    night_light.format_hhmm(picked))
            except Exception:
                pass
            self._render_night(win)
            refresh_night_scheduler('time')
            return None

        return ui.form_sheet(
            win, anchor, title, [('time', 'Time (HH:MM)', current, 'text')],
            'Save', _commit)

    def _on_profile(self, win, profile: str) -> None:
        if self._backend == 'none' or profile not in power_profiles.PROFILES:
            return
        try:
            win.config.set('power_profile', profile)
        except Exception:
            pass
        ui.set_subtitle(self._profile_row, self._current_profile(win))
        apply_profile_async(
            profile, self._backend, self._profile_name,
            lambda ok: self._say(
                '' if ok else 'Could not set power profile.'))

    def _say(self, text: str) -> None:
        ui.say(self._status, text)

    # -- status bar height (status_bar.py) ---------------------------------
    def _status_bar_row(self, win):
        """Slider for the top status bar height: live, so the owner can
        line the bar's edge up under the camera hole on the real phone."""
        from gi.repository import Gtk
        import settings_launcher
        import status_bar

        bar = getattr(win, '_status_bar', None)
        height = getattr(bar, 'height', None) or status_bar.resolve_height(
            win.config)
        scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, status_bar.MIN_HEIGHT,
            status_bar.MAX_HEIGHT, 1)
        scale.set_value(float(height))
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        row = settings_launcher._scale_row(
            'Status bar height', '%d px' % height, scale)
        scale.connect('value-changed',
                      lambda s: self._on_status_bar_height(win, s, row))
        return row

    def _on_status_bar_height(self, win, scale, row=None) -> None:
        import status_bar

        height = status_bar.clamp_height(round(scale.get_value()))
        if height is None:
            return
        if row is not None:
            row.value_label.set_text('%d px' % height)
        try:
            win.config.set(status_bar.CONFIG_KEY, height)
        except Exception:
            pass
        bar = getattr(win, '_status_bar', None)
        if bar is not None:
            bar.apply_config()


def _read_active(bus_name: str) -> str | None:
    try:
        from gi.repository import Gio, GLib
        import settings_device
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        props = settings_device.dbus_call_sync(
            bus, bus_name, '/org/freedesktop/UPower/PowerProfiles'
            if bus_name == PROFILE_BUS_NAMES[0]
            else '/net/hadess/PowerProfiles',
            'org.freedesktop.DBus.Properties', 'GetAll',
            GLib.Variant('(s)', (_PROFILE_IFACE,)),
            GLib.VariantType('(a{sv})'), 3000,
        ).unpack()[0]
        return parse_active_profile((props,))
    except Exception:
        return None
