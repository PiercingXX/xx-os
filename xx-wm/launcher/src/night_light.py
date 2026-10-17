"""Night light schedules: off (manual), custom times, sunset to sunrise.

Sunset times come from a pure NOAA-style solar calculation over the
shell's weather location (manual coordinates or the cached automatic
fix) — no network for the sun times. A small scheduler turns the light
on and off at the boundaries with one GLib timeout to the next
boundary, recomputed daily and after resume; a manual tile flip holds
only until the next boundary. Without a location, sunset mode falls
back to the custom times.

Pure stdlib; the clock, timezone and timer are injectable so tests run
without GLib, D-Bus or the real sky.
"""
from __future__ import annotations

import calendar
import datetime
import math
import re
import time

MODES = ('off', 'custom', 'sunset')

DEFAULT_FROM = '22:00'
DEFAULT_TO = '06:00'

NO_LOCATION_TEXT = 'Set a location in Settings'

_ZENITH = math.radians(90.833)

_HHMM_RE = re.compile(r'^(\d{1,2}):(\d{2})$')

_BACKSTOP_S = 6 * 3600


def parse_hhmm(value) -> int | None:
    """Minutes since midnight for ``HH:MM``, else None."""
    try:
        from xx_phone.schedules import parse_hhmm as shared
        found = shared(value)
        if found is not None:
            return found
    except (ImportError, ValueError, AttributeError):
        pass
    if not isinstance(value, str):
        return None
    match = _HHMM_RE.match(value.strip())
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    return hours * 60 + minutes


def format_hhmm(minutes) -> str:
    try:
        total = int(minutes) % 1440
    except (TypeError, ValueError):
        return DEFAULT_FROM
    return '%02d:%02d' % (total // 60, total % 60)


def valid_coords(lat, lon) -> bool:
    try:
        lat_f, lon_f = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    return -90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0


def mode_of(config) -> str:
    """Schedule mode with legacy fallback: the old boolean
    ``night_light_schedule`` True reads as sunset."""
    try:
        get = getattr(config, 'get', None)
        if callable(get):
            mode = get('night_light_mode', '')
            legacy = get('night_light_schedule', False)
        else:
            data = getattr(config, 'data', config)
            mode = data.get('night_light_mode', '')
            legacy = data.get('night_light_schedule', False)
    except Exception:
        return 'off'
    if mode in MODES:
        return mode
    return 'sunset' if legacy else 'off'


def custom_range(config) -> tuple[int, int]:
    """(from_min, to_min) for custom mode, defaulting to 22:00–06:00."""
    try:
        get = getattr(config, 'get', None)
        if callable(get):
            raw_from = get('night_light_from', DEFAULT_FROM)
            raw_to = get('night_light_to', DEFAULT_TO)
        else:
            data = getattr(config, 'data', config)
            raw_from = data.get('night_light_from', DEFAULT_FROM)
            raw_to = data.get('night_light_to', DEFAULT_TO)
    except Exception:
        return (parse_hhmm(DEFAULT_FROM), parse_hhmm(DEFAULT_TO))
    from_min = parse_hhmm(raw_from)
    to_min = parse_hhmm(raw_to)
    if from_min is None:
        from_min = parse_hhmm(DEFAULT_FROM)
    if to_min is None:
        to_min = parse_hhmm(DEFAULT_TO)
    return (from_min, to_min)


def solar_utc(day_of_year: int, lat, lon) -> tuple[float, float, str]:
    """(sunrise, sunset) UTC minutes + kind for a date and position.

    NOAA approximation (equation of time + declination series, 90.833°
    zenith): within a couple of minutes of the almanac outside the
    polar circles. ``kind`` is 'normal', 'polar-day' or 'polar-night';
    unparseable input degrades to 06:00/18:00.
    """
    try:
        lat_f, lon_f = float(lat), float(lon)
        doy = int(day_of_year)
    except (TypeError, ValueError):
        return (360.0, 1080.0, 'normal')
    if not (-90.0 <= lat_f <= 90.0 and -180.0 <= lon_f <= 180.0):
        return (360.0, 1080.0, 'normal')
    gamma = 2.0 * math.pi / 365.0 * (doy - 1)
    eqtime = 229.18 * (
        0.000075 + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma)
            + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma)
            + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma)
            + 0.00148 * math.sin(3 * gamma))
    lat_r = math.radians(lat_f)
    cos_arg = (math.cos(_ZENITH) / (math.cos(lat_r) * math.cos(decl))
               - math.tan(lat_r) * math.tan(decl))
    if cos_arg > 1.0:
        return (0.0, 0.0, 'polar-night')
    if cos_arg < -1.0:
        return (0.0, 0.0, 'polar-day')
    hour_angle = math.degrees(math.acos(cos_arg))
    rise = 720.0 - 4.0 * (lon_f + hour_angle) - eqtime
    set_ = 720.0 - 4.0 * (lon_f - hour_angle) - eqtime
    return (rise, set_, 'normal')


def sun_local_minutes(utc_minutes: float, utc_offset_min: float) -> float:
    return (float(utc_minutes) + float(utc_offset_min)) % 1440.0


def is_night(now_min: float, rise_min: float, set_min: float) -> bool:
    if rise_min <= set_min:
        return now_min < rise_min or now_min >= set_min
    return set_min <= now_min < rise_min


def in_custom(now_min: float, from_min: float, to_min: float) -> bool:
    if from_min == to_min:
        return False
    if from_min < to_min:
        return from_min <= now_min < to_min
    return now_min >= from_min or now_min < to_min


def _utc_midnight_epoch(utc_date: datetime.date) -> int:
    return calendar.timegm(utc_date.timetuple())


def sun_edges_for_utc_date(utc_date: datetime.date, lat: float,
                           lon: float) -> tuple[list, bool | None]:
    """([(epoch, on), ...], constant): sunset switches on, sunrise off."""
    rise, set_, kind = solar_utc(utc_date.timetuple().tm_yday, lat, lon)
    if kind == 'polar-day':
        return ([], False)
    if kind == 'polar-night':
        return ([], True)
    midnight = _utc_midnight_epoch(utc_date)
    return ([(midnight + int(set_ * 60), True),
             (midnight + int(rise * 60), False)], None)


def custom_edges_for_local_date(local_date: datetime.date, from_min: int,
                                to_min: int,
                                tz: datetime.tzinfo) -> tuple[list, bool | None]:
    if from_min == to_min:
        return ([], False)
    start = datetime.datetime(local_date.year, local_date.month,
                              local_date.day, tzinfo=tz)
    on_at = start + datetime.timedelta(minutes=from_min)
    if from_min < to_min:
        off_at = start + datetime.timedelta(minutes=to_min)
    else:
        off_at = (start + datetime.timedelta(days=1, minutes=to_min))
    return ([(int(on_at.timestamp()), True),
             (int(off_at.timestamp()), False)], None)


def collect_edges(now_epoch: float, mode: str, lat, lon,
                  from_min: int, to_min: int,
                  tz: datetime.tzinfo) -> tuple[list, bool | None]:
    """Sorted [(epoch, on)] edges around now plus a constant when the
    schedule never flips (polar day/night, degenerate custom range)."""
    if mode == 'sunset' and valid_coords(lat, lon):
        lat_f, lon_f = float(lat), float(lon)
        edges: list = []
        now_utc = datetime.datetime.fromtimestamp(
            now_epoch, tz=datetime.timezone.utc).date()
        for delta in (-1, 0, 1, 2):
            day = now_utc + datetime.timedelta(days=delta)
            day_edges, constant = sun_edges_for_utc_date(day, lat_f, lon_f)
            if constant is not None:
                return ([], constant)
            edges.extend(day_edges)
        edges.sort()
        return (edges, None)
    edges = []
    local_now = datetime.datetime.fromtimestamp(now_epoch, tz=tz).date()
    for delta in (-1, 0, 1):
        day = local_now + datetime.timedelta(days=delta)
        day_edges, constant = custom_edges_for_local_date(
            day, from_min, to_min, tz)
        if constant is not None:
            return ([], constant)
        edges.extend(day_edges)
    edges.sort()
    return (edges, None)


def wanted_at(now_epoch: float, edges: list,
              constant: bool | None = None) -> bool:
    if constant is not None:
        return bool(constant)
    wanted = False
    for when, state in edges:
        if when <= now_epoch:
            wanted = state
        else:
            break
    return wanted


def next_edge(now_epoch: float, edges: list):
    for when, state in edges:
        if when > now_epoch:
            return (when, state)
    return None


class NightScheduler:
    """One GLib timeout to the next night-light boundary.

    ``get_mode``/``get_custom``/``get_coords`` read live config so
    hot-reloads apply; ``apply(on)`` performs the switch (the shell's
    wlsunset/gammastep spawn, flap-guarded there); ``arm``/``disarm``
    wrap GLib.timeout_add/… (fakes in tests).
    """

    def __init__(self, *, get_mode, get_custom, get_coords, apply,
                 clock=None, local_tz=None, arm=None,
                 disarm=None) -> None:
        self._get_mode = get_mode
        self._get_custom = get_custom
        self._get_coords = get_coords
        self._apply = apply
        self._clock = clock or time.time
        self._local_tz = local_tz or _system_tz
        self._arm = arm
        self._disarm = disarm
        self._timer = None
        self._override: bool | None = None
        self._override_until: float | None = None

    def _retime(self, delay_s: float, callback) -> None:
        self._drop_timer()
        if self._arm is None:
            return
        try:
            self._timer = self._arm(max(1.0, delay_s), callback)
        except Exception:
            self._timer = None

    def _drop_timer(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None and self._disarm is not None:
            try:
                self._disarm(timer)
            except Exception:
                pass

    def _snapshot(self):
        try:
            mode = self._get_mode()
        except Exception:
            mode = 'off'
        if mode not in MODES:
            mode = 'off'
        try:
            coords = self._get_coords()
        except Exception:
            coords = None
        lat = lon = None
        if coords is not None:
            try:
                lat, lon = coords
            except (TypeError, ValueError):
                lat, lon = None, None
        if mode == 'sunset' and not valid_coords(lat, lon):
            mode = '__fallback__'
        try:
            from_min, to_min = self._get_custom()
        except Exception:
            from_min, to_min = (parse_hhmm(DEFAULT_FROM),
                               parse_hhmm(DEFAULT_TO))
        return (mode, lat, lon, from_min, to_min)

    def refresh(self, _reason: str = '') -> bool | None:
        """Recompute state now and re-arm: after boot, resume, midnight
        rollover and every config change. Returns the applied state, or
        None while the mode is manual."""
        now = self._clock()
        mode, lat, lon, from_min, to_min = self._snapshot()
        if mode == 'off' or (mode == '__fallback__'
                             and from_min == to_min):
            self._drop_timer()
            self._override = None
            self._override_until = None
            return None
        use_mode = 'custom' if mode == '__fallback__' else mode
        try:
            tz = self._local_tz()
        except Exception:
            tz = None
        if tz is None:
            tz = datetime.timezone.utc
        edges, constant = collect_edges(now, use_mode, lat, lon,
                                        from_min, to_min, tz)
        if (self._override is not None and self._override_until is not None
                and now < self._override_until):
            wanted = self._override
            wake = self._override_until
        else:
            self._override = None
            self._override_until = None
            wanted = wanted_at(now, edges, constant)
            edge = next_edge(now, edges)
            wake = edge[0] if edge is not None else now + _BACKSTOP_S
        try:
            self._apply(bool(wanted))
        except Exception:
            pass
        self._retime(wake - now, lambda: self.refresh('boundary'))
        return bool(wanted)

    def manual_set(self, enabled: bool) -> bool:
        """Tile/manual flip: holds until the next boundary, then the
        schedule resumes. Manual mode applies immediately."""
        now = self._clock()
        mode, lat, lon, from_min, to_min = self._snapshot()
        if mode == 'off':
            try:
                self._apply(bool(enabled))
            except Exception:
                pass
            return bool(enabled)
        use_mode = 'custom' if mode == '__fallback__' else mode
        try:
            tz = self._local_tz()
        except Exception:
            tz = None
        if tz is None:
            tz = datetime.timezone.utc
        edges, _constant = collect_edges(now, use_mode, lat, lon,
                                         from_min, to_min, tz)
        edge = next_edge(now, edges)
        self._override = bool(enabled)
        self._override_until = (edge[0] if edge is not None
                                else now + _BACKSTOP_S)
        try:
            self._apply(bool(enabled))
        except Exception:
            pass
        self._retime(self._override_until - now,
                     lambda: self.refresh('boundary'))
        return bool(enabled)

    def stop(self) -> None:
        self._drop_timer()
        self._override = None
        self._override_until = None


_scheduler: NightScheduler | None = None


def get_scheduler() -> NightScheduler | None:
    return _scheduler


def configure_scheduler(**kwargs) -> NightScheduler:
    global _scheduler
    old = _scheduler
    if old is not None:
        try:
            old.stop()
        except Exception:
            pass
    _scheduler = NightScheduler(**kwargs)
    return _scheduler


def _system_tz() -> datetime.tzinfo:
    try:
        return datetime.datetime.now().astimezone().tzinfo
    except Exception:
        return datetime.timezone.utc
