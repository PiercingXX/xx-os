"""
Weather widget backend — Open-Meteo current conditions, no API key.

Coordinates come from the `weather_lat`/`weather_lon` config floats or the
`weather_location` string (`lat,lon`); with `weather_auto_location` (the
default) and neither set, a coarse GeoClue fix is used instead. Results
cache to ~/.cache/xx-wm/weather.json and refresh at most every 15 minutes.
Offline or unset → the provider reports `--°` and the home screen hides
the weather line entirely (never a placeholder). Fetches run on a daemon
thread; the UI callback is dispatched via an injectable dispatcher
(GLib.idle_add at runtime, synchronous in tests).
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable

REFRESH_SECONDS = 15 * 60
AUTO_LOCATION_REFRESH_SECONDS = 30 * 60
PLACEHOLDER = '--°'

_API = ('https://api.open-meteo.com/v1/forecast'
        '?latitude={lat}&longitude={lon}&current=temperature_2m,weather_code'
        '&temperature_unit={unit}')

_UNITS = ('fahrenheit', 'celsius')

try:
    # One shared fetch/parse implementation with the xx-weather app
    # (common/xx_phone/weather.py, installed as xx_phone on the phone).
    from xx_phone import weather as _shared_weather
except Exception:  # noqa: BLE001 - the local copies below take over
    _shared_weather = None


def _local_describe(code: int) -> str:
    # WMO weather interpretation codes, collapsed to short text
    table = [
        ((0,), 'Clear'),
        ((1, 2), 'Partly cloudy'),
        ((3,), 'Cloudy'),
        ((45, 48), 'Fog'),
        ((51, 53, 55, 56, 57), 'Drizzle'),
        ((61, 63, 65, 66, 67, 80, 81, 82), 'Rain'),
        ((71, 73, 75, 77, 85, 86), 'Snow'),
        ((95, 96, 99), 'Storm'),
    ]
    for codes, text in table:
        if code in codes:
            return text
    return ''


def _local_format_current(temp: float, code: int,
                           unit: str = 'fahrenheit') -> str:
    text = _local_describe(code)
    suffix = 'F' if unit == 'fahrenheit' else 'C'
    degrees = f'{round(temp)}°{suffix}'
    return f'{degrees} {text}'.strip()


if _shared_weather is not None:
    describe = _shared_weather.wmo_text
    format_current = _shared_weather.widget_format_current
else:
    describe = _local_describe
    format_current = _local_format_current


def default_cache_path() -> Path:
    return Path.home() / '.cache' / 'xx-wm' / 'weather.json'


def default_location_cache_path() -> Path:
    return Path.home() / '.cache' / 'xx-wm' / 'weather-location.json'


def _http_fetch(lat: float, lon: float, unit: str = 'fahrenheit') -> dict:
    if _shared_weather is not None:
        return _shared_weather.fetch_openmeteo_current(lat, lon, unit)
    url = _API.format(lat=lat, lon=lon, unit=unit)
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode('utf-8'))
    current = payload.get('current') or {}
    return {
        'temp': float(current['temperature_2m']),
        'code': int(current.get('weather_code', -1)),
    }


# --- GeoClue coarse fix ----------------------------------------------------

_GEOCLUE_NAME = 'org.freedesktop.GeoClue2'
_GEOCLUE_MGR_PATH = '/org/freedesktop/GeoClue2/Manager'
_GEOCLUE_MGR_IFACE = 'org.freedesktop.GeoClue2.Manager'
_GEOCLUE_CLIENT_IFACE = 'org.freedesktop.GeoClue2.Client'
_GEOCLUE_LOCATION_IFACE = 'org.freedesktop.GeoClue2.Location'
_GEOCLUE_PROPS_IFACE = 'org.freedesktop.DBus.Properties'
_GEOCLUE_TIMEOUT_MS = 3000
# GClueAccuracyLevel city: coarse enough for weather, no street-level fix.
_GEOCLUE_ACCURACY_CITY = 2


def _variant_value(value: object) -> object:
    try:
        unpack = getattr(value, 'unpack', None)
    except Exception:
        return value
    if callable(unpack):
        try:
            return unpack()
        except Exception:
            return value
    return value


def _geoclue_fix_sync(bus_factory: Callable[[], object] | None = None,
                      ) -> tuple[float, float] | None:
    """One-shot coarse GeoClue position, or None when unavailable.

    WORKER THREAD ONLY: every step is a blocking D-Bus round trip. Never
    registers the system's single agent slot — the short-lived client is
    authorized through whichever agent already owns it (the shell's
    location tile when on), so a denied or absent service simply yields
    no fix and the weather line stays hidden.
    """
    from gi.repository import Gio, GLib
    try:
        bus = bus_factory() if bus_factory is not None else Gio.bus_get_sync(
            Gio.BusType.SYSTEM, None)
        if bus is None:
            return None
        flags = Gio.DBusCallFlags.NONE
        timeout = _GEOCLUE_TIMEOUT_MS
        client_path = bus.call_sync(
            _GEOCLUE_NAME, _GEOCLUE_MGR_PATH, _GEOCLUE_MGR_IFACE,
            'GetClient', None, GLib.VariantType.new('(o)'),
            flags, timeout, None,
        ).unpack()[0]
        bus.call_sync(
            _GEOCLUE_NAME, str(client_path), _GEOCLUE_PROPS_IFACE, 'Set',
            GLib.Variant('(ssv)', (_GEOCLUE_CLIENT_IFACE, 'DesktopId',
                                   GLib.Variant('s', 'xx-wm'))),
            None, flags, timeout, None,
        )
        bus.call_sync(
            _GEOCLUE_NAME, str(client_path), _GEOCLUE_PROPS_IFACE, 'Set',
            GLib.Variant('(ssv)', (_GEOCLUE_CLIENT_IFACE,
                                   'RequestedAccuracyLevel',
                                   GLib.Variant('u', _GEOCLUE_ACCURACY_CITY))),
            None, flags, timeout, None,
        )
        bus.call_sync(
            _GEOCLUE_NAME, str(client_path), _GEOCLUE_CLIENT_IFACE,
            'Start', None, None, flags, timeout, None,
        )
        try:
            location_path = bus.call_sync(
                _GEOCLUE_NAME, str(client_path), _GEOCLUE_PROPS_IFACE,
                'Get', GLib.Variant('(ss)', (_GEOCLUE_CLIENT_IFACE,
                                             'Location')),
                GLib.VariantType.new('(v)'), flags, timeout, None,
            ).unpack()[0]
            props = bus.call_sync(
                _GEOCLUE_NAME, str(_variant_value(location_path)),
                _GEOCLUE_PROPS_IFACE, 'GetAll',
                GLib.Variant('(s)', (_GEOCLUE_LOCATION_IFACE,)),
                GLib.VariantType.new('(a{sv})'), flags, timeout, None,
            ).unpack()[0]
        finally:
            try:
                bus.call_sync(
                    _GEOCLUE_NAME, str(client_path),
                    _GEOCLUE_CLIENT_IFACE, 'Stop', None, None,
                    flags, timeout, None,
                )
            except Exception:
                pass
        if not isinstance(props, dict):
            return None
        lat = float(_variant_value(props.get('Latitude')))
        lon = float(_variant_value(props.get('Longitude')))
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return None
        return (lat, lon)
    except Exception:
        return None


class WeatherProvider:
    def __init__(self, config: object,
                 cache_path: Path | None = None,
                 fetcher: Callable[[float, float, str], dict] | None = None,
                 clock: Callable[[], float] = time.time,
                 dispatch: Callable[..., object] | None = None,
                 location_provider: Callable[[], tuple[float, float] | None]
                 | None = None,
                 location_cache_path: Path | None = None) -> None:
        self._config = config
        self._cache_path = cache_path or default_cache_path()
        self._fetch = fetcher or _http_fetch
        self._clock = clock
        self._dispatch = dispatch
        self._location_provider = location_provider or _geoclue_fix_sync
        self._location_cache_path = (
            location_cache_path or default_location_cache_path())
        self._fetching = False

    # -- units ---------------------------------------------------------------

    def _unit(self) -> str:
        try:
            unit = getattr(self._config, 'weather_temp_unit', 'fahrenheit')
        except Exception:
            return 'fahrenheit'
        if unit is None:
            try:
                unit = self._config.data.get('weather_temp_unit',
                                             'fahrenheit')
            except AttributeError:
                return 'fahrenheit'
        return unit if unit in _UNITS else 'fahrenheit'

    def _auto_enabled(self) -> bool:
        try:
            value = getattr(self._config, 'weather_auto_location', True)
        except Exception:
            return True
        if value is None:
            try:
                value = self._config.data.get('weather_auto_location', True)
            except AttributeError:
                return True
        return value if isinstance(value, bool) else True

    # -- coordinates -------------------------------------------------------

    @staticmethod
    def parse_location(raw: object) -> tuple[float, float] | None:
        """Parse a `weather_location` string as `lat,lon`. Anything else
        (city names need a network geocoder) yields None."""
        if not isinstance(raw, str) or ',' not in raw:
            return None
        try:
            lat_raw, lon_raw = raw.split(',', 1)
            lat, lon = float(lat_raw.strip()), float(lon_raw.strip())
        except (TypeError, ValueError):
            return None
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return None
        return lat, lon

    def coordinates(self) -> tuple[float, float] | None:
        """Manual coordinates first; otherwise the cached automatic fix
        when fresh. Never blocks: the fix itself refreshes on a worker."""
        try:
            lat = float(self._config.data.get('weather_lat'))
            lon = float(self._config.data.get('weather_lon'))
        except (TypeError, ValueError):
            lat, lon = None, None
        if lat is not None and lon is not None:
            return lat, lon
        try:
            location = self._config.data.get('weather_location')
        except AttributeError:
            location = None
        manual = self.parse_location(location)
        if manual is not None:
            return manual
        if not self._auto_enabled():
            return None
        return self._auto_fix()

    # -- cache -------------------------------------------------------------

    def _load_cache(self) -> dict | None:
        try:
            raw = json.loads(self._cache_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError, ValueError):
            return None
        if not isinstance(raw, dict) or 'fetched_at' not in raw:
            return None
        return raw

    def _store_cache(self, data: dict) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(json.dumps(data), encoding='utf-8')
        except OSError:
            pass

    def cache_is_fresh(self) -> bool:
        cached = self._load_cache()
        if cached is None:
            return False
        if cached.get('unit', 'celsius') != self._unit():
            return False
        try:
            return self._clock() - float(cached['fetched_at']) < REFRESH_SECONDS
        except (TypeError, ValueError):
            return False

    # -- automatic fix -----------------------------------------------------

    def _load_fix(self) -> dict | None:
        try:
            raw = json.loads(
                self._location_cache_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        try:
            lat, lon = float(raw['lat']), float(raw['lon'])
            fetched_at = float(raw['fetched_at'])
        except (KeyError, TypeError, ValueError):
            return None
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            return None
        return {'lat': lat, 'lon': lon, 'fetched_at': fetched_at}

    def _store_fix(self, lat: float, lon: float) -> None:
        try:
            self._location_cache_path.parent.mkdir(parents=True,
                                                   exist_ok=True)
            self._location_cache_path.write_text(json.dumps({
                'lat': lat, 'lon': lon, 'fetched_at': self._clock(),
            }), encoding='utf-8')
        except OSError:
            pass

    def _fix_is_fresh(self) -> bool:
        fix = self._load_fix()
        if fix is None:
            return False
        try:
            return (self._clock() - fix['fetched_at']
                    < AUTO_LOCATION_REFRESH_SECONDS)
        except (TypeError, ValueError):
            return False

    def _auto_fix(self) -> tuple[float, float] | None:
        fix = self._load_fix()
        if fix is None or not self._fix_is_fresh():
            return None
        return (fix['lat'], fix['lon'])

    # -- public ------------------------------------------------------------

    def current_text(self) -> str:
        """Cached conditions as `72°F Clear`; `--°` when unset/offline."""
        cached = self._load_cache()
        if cached is None:
            return PLACEHOLDER
        try:
            temp = cached.get('temp', cached.get('temp_c'))
            unit = cached.get('unit', 'celsius'
                              if 'temp' not in cached else self._unit())
            return format_current(float(temp), int(cached['code']), unit)
        except (KeyError, TypeError, ValueError):
            return PLACEHOLDER

    def refresh(self, on_done: Callable[[str], None],
                force: bool = False) -> None:
        """Refresh if stale (or forced); on_done always gets the display text."""
        coords = self.coordinates()
        if coords is not None:
            if not force and self.cache_is_fresh():
                on_done(self.current_text())
                return
            if self._fetching:
                return
            self._fetching = True
            unit = self._unit()
            threading.Thread(target=self._refresh_worker,
                             args=(coords, unit, on_done),
                             daemon=True, name='weather-fetch').start()
            return
        if not self._auto_enabled():
            on_done(PLACEHOLDER)
            return
        if self._fetching:
            return
        self._fetching = True
        threading.Thread(target=self._refresh_auto_worker,
                         args=(self._unit(), force, on_done),
                         daemon=True, name='weather-locate').start()

    def _refresh_worker(self, coords: tuple[float, float], unit: str,
                        on_done: Callable[[str], None]) -> None:
        text = self.current_text()
        try:
            data = self._fetch(*coords, unit)
            data['fetched_at'] = self._clock()
            data['unit'] = unit
            self._store_cache(data)
            temp = data.get('temp', data.get('temp_c'))
            text = format_current(float(temp), int(data['code']), unit)
        except Exception:
            pass  # offline: keep whatever the cache had
        finally:
            self._fetching = False
        self._deliver(on_done, text)

    def _refresh_auto_worker(self, unit: str, force: bool,
                             on_done: Callable[[str], None]) -> None:
        """Resolve the automatic fix, then fetch — all off the main loop."""
        text = self.current_text()
        try:
            if force or not self._fix_is_fresh():
                fix = self._location_provider()
                if fix is not None:
                    self._store_fix(float(fix[0]), float(fix[1]))
            coords = self._auto_fix()
            if coords is not None and (
                    force or self.cache_is_fresh() is False):
                data = self._fetch(*coords, unit)
                data['fetched_at'] = self._clock()
                data['unit'] = unit
                self._store_cache(data)
                temp = data.get('temp', data.get('temp_c'))
                text = format_current(float(temp), int(data['code']), unit)
            elif coords is not None:
                text = self.current_text()
        except Exception:
            pass  # offline or no fix: keep whatever the cache had
        finally:
            self._fetching = False
        self._deliver(on_done, text)

    def _deliver(self, on_done: Callable[[str], None], text: str) -> None:
        dispatch = self._dispatch
        if dispatch is None:
            from gi.repository import GLib
            dispatch = GLib.idle_add
        dispatch(lambda: on_done(text) and False or False)
