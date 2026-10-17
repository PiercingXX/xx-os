"""Display section: night light and power profile (ref power-manager.sh).

Night light drives wlsunset (preferred) or gammastep as the caller's own
processes — no privilege involved. phoc wires wlroots'
wlr_gamma_control_manager_v1 (wlr-gamma-control-unstable-v1, the protocol
both tools speak), so the backend is viable; the rows hide when neither
tool is installed. Power profiles go through power-profiles-daemon's
session D-Bus API; the picker shows only while the daemon owns its bus
name. The shell's own Battery saver (standby lane) is untouched.
"""
from __future__ import annotations

import datetime
import math
import shutil
import subprocess

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
    """(sunrise, sunset) in local decimal hours from latitude/longitude.
    NOAA approximation; polar day/night clamps to (0, 24)/(12, 12)."""
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return (6.0, 18.0)
    lat_r = math.radians(max(-66.0, min(66.0, lat_f)))
    decl = math.radians(-23.44 * math.cos(
        2 * math.pi * (float(day_of_year) + 10) / 365.0))
    cos_omega = -math.tan(lat_r) * math.tan(decl)
    cos_omega = max(-1.0, min(1.0, cos_omega))
    half_day = math.degrees(math.acos(cos_omega)) / 15.0
    solar_noon = 12.0 - lon_f / 15.0
    return (solar_noon - half_day, solar_noon + half_day)


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


def read_power_profile() -> tuple[str | None, list[str]]:
    """(bus_name, profiles) or (None, []): worker only, never raises."""
    try:
        from gi.repository import Gio, GLib
        import settings_device
        name = _profile_bus()
        if name is None:
            return None, []
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        props = settings_device.dbus_call_sync(
            bus, name, '/org/freedesktop/UPower/PowerProfiles'
            if name == PROFILE_BUS_NAMES[0]
            else '/net/hadess/PowerProfiles',
            'org.freedesktop.DBus.Properties', 'GetAll',
            GLib.Variant('(s)', (_PROFILE_IFACE,)),
            GLib.VariantType('(a{sv})'), 3000,
        ).unpack()[0]
        profiles = [str(entry.get('Profile', ''))
                    for entry in (props.get('Profiles') or [])
                    if isinstance(entry, dict) and entry.get('Profile')]
        return name, profiles
    except Exception:
        return None, []


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
    set_night_light(bool(enabled))


class DisplaySection(settings_page._Section):
    """Night light (wlsunset/gammastep) plus the power-profile picker."""

    key = 'display'
    title = 'Night light'

    def __init__(self) -> None:
        self._status = None
        self._night_box = None
        self._profile_box = None
        self._profile_row = None
        self._updating = False
        self._profile_name: str | None = None
        self._profiles: list[str] = []

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
            profile = read_power_profile()
            active = None
            if profile[0] is not None:
                active = _read_active(profile[0])
            return {'tool': night_tool(), 'bus': profile[0],
                    'profiles': profile[1], 'active': active}

        _run_bg(_work, lambda res: self._on_probed(win, res))

    def _on_probed(self, win, res) -> None:
        if not isinstance(res, dict):
            return
        self._profile_name = res.get('bus')
        self._profiles = list(res.get('profiles') or [])
        self._active_profile = res.get('active')
        self._render_night(win)
        self._render_profiles(win)

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
        try:
            lat = win.config.data.get('weather_lat')
            lon = win.config.data.get('weather_lon')
        except Exception:
            lat, lon = None, None
        scheduled = bool(self._config(win, 'night_light_schedule', False))
        detail = ('Set a manual weather location first'
                  if scheduled and (lat is None or lon is None)
                  else 'Uses the weather location')
        sched = ui.switch_row('Sunset to sunrise', detail, scheduled)
        sched.switch.connect(
            'notify::active',
            lambda sw, _p: self._on_schedule_flipped(win, sw))
        ui.fill(self._night_box, [night, warmth, sched])

    def _render_profiles(self, win) -> None:
        if self._profile_box is None:
            return
        if self._profile_name is None:
            ui.fill(self._profile_box, [ui.row(
                'Power profile', 'Not available on this device')])
            return
        ui.set_subtitle(self._profile_row,
                        self._current_profile(win) or 'Unknown')
        ui.fill(self._profile_box, [self._profile_row])

    def _show_profiles(self, win) -> None:
        actions = [(profile, lambda profile=profile: self._on_profile(
            win, profile)) for profile in self._profiles]
        actions.append(('Cancel', lambda: None))
        win.item_actions._show_action_menu(
            self._profile_row, 'Power profile', actions)

    def _current_profile(self, win) -> str:
        configured = str(self._config(win, 'power_profile', ''))
        if configured:
            return configured
        return str(getattr(self, '_active_profile', None) or 'Unknown')

    def _coords(self, win):
        try:
            return (win.config.data.get('weather_lat'),
                    win.config.data.get('weather_lon'))
        except Exception:
            return (None, None)

    def _on_night_flipped(self, win, switch) -> None:
        if self._updating:
            return
        enabled = bool(switch.get_active())
        lat, lon = self._coords(win)
        scheduled = bool(self._config(win, 'night_light_schedule', False))
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
            scheduled = bool(self._config(win, 'night_light_schedule',
                                          False))
            set_night_light(True, temp,
                            lat if scheduled else None,
                            lon if scheduled else None)

    def _on_schedule_flipped(self, win, switch) -> None:
        try:
            win.config.set('night_light_schedule', bool(switch.get_active()))
        except Exception:
            pass
        self._render_night(win)

    def _on_profile(self, win, profile: str) -> None:
        from settings_page import _run_bg
        if not self._profile_name:
            return
        name = self._profile_name
        try:
            win.config.set('power_profile', profile)
        except Exception:
            pass

        def _work():
            return set_power_profile(name, profile)

        ui.set_subtitle(self._profile_row, profile)
        _run_bg(_work, lambda ok: self._say(
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
