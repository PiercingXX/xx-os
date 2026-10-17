"""Shared weather fetch/parse/cache logic for the XX phone suite.

Port of the Android xx-weather Play-edition pipeline (NWS primary,
Open-Meteo enrichment + fallback, zippopotam ZIP geocoding, local
sunrise/sunset, per-ZIP alert triggers). Pure module: no GTK, no
blocking calls on import. Both the XX-WM home weather line and the
xx-weather app build on this file so the two never drift.

Network only ever touches api.weather.gov (NWS, US only) and
api.open-meteo.com, plus api.zippopotam.us for ZIP lookup. Callers run
fetches on a worker thread and post results with GLib.idle_add.
"""

from __future__ import annotations

import gzip
import json
import math
import re
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Callable


class Condition(str, Enum):
    CLEAR = "CLEAR"
    MOSTLY_CLEAR = "MOSTLY_CLEAR"
    PARTLY_CLOUDY = "PARTLY_CLOUDY"
    CLOUDY = "CLOUDY"
    OVERCAST = "OVERCAST"
    FOG = "FOG"
    DRIZZLE = "DRIZZLE"
    RAIN = "RAIN"
    HEAVY_RAIN = "HEAVY_RAIN"
    SHOWERS = "SHOWERS"
    THUNDERSTORM = "THUNDERSTORM"
    SNOW = "SNOW"
    HEAVY_SNOW = "HEAVY_SNOW"
    SLEET = "SLEET"
    WINDY = "WINDY"


_CONDITION_LABELS = {
    Condition.CLEAR: "Clear",
    Condition.MOSTLY_CLEAR: "Mostly Clear",
    Condition.PARTLY_CLOUDY: "Partly Cloudy",
    Condition.CLOUDY: "Cloudy",
    Condition.OVERCAST: "Overcast",
    Condition.FOG: "Foggy",
    Condition.DRIZZLE: "Drizzle",
    Condition.RAIN: "Rain",
    Condition.HEAVY_RAIN: "Heavy Rain",
    Condition.SHOWERS: "Rain Showers",
    Condition.THUNDERSTORM: "Thunderstorm",
    Condition.SNOW: "Snow",
    Condition.HEAVY_SNOW: "Heavy Snow",
    Condition.SLEET: "Sleet / Mix",
    Condition.WINDY: "Windy",
}


def condition_label(condition: Condition) -> str:
    return _CONDITION_LABELS[condition]


_SEVERITY = {
    Condition.CLEAR: 0,
    Condition.MOSTLY_CLEAR: 1,
    Condition.PARTLY_CLOUDY: 1,
    Condition.CLOUDY: 2,
    Condition.OVERCAST: 2,
    Condition.WINDY: 3,
    Condition.FOG: 4,
    Condition.DRIZZLE: 5,
    Condition.SHOWERS: 5,
    Condition.RAIN: 5,
    Condition.HEAVY_RAIN: 5,
    Condition.SLEET: 6,
    Condition.SNOW: 6,
    Condition.HEAVY_SNOW: 6,
    Condition.THUNDERSTORM: 7,
}

_ICON_TOKENS = {
    "skc": Condition.CLEAR,
    "wind_skc": Condition.CLEAR,
    "few": Condition.MOSTLY_CLEAR,
    "wind_few": Condition.MOSTLY_CLEAR,
    "sct": Condition.PARTLY_CLOUDY,
    "wind_sct": Condition.PARTLY_CLOUDY,
    "bkn": Condition.CLOUDY,
    "wind_bkn": Condition.CLOUDY,
    "ovc": Condition.OVERCAST,
    "cloud": Condition.OVERCAST,
    "wind_ovc": Condition.OVERCAST,
    "fg": Condition.FOG,
    "fzfg": Condition.FOG,
    "haze": Condition.FOG,
    "br": Condition.FOG,
    "du": Condition.FOG,
    "dust": Condition.FOG,
    "smoke": Condition.FOG,
    "fu": Condition.FOG,
    "ra": Condition.RAIN,
    "rain": Condition.RAIN,
    "shra": Condition.SHOWERS,
    "rain_showers": Condition.SHOWERS,
    "rain_showers_high": Condition.SHOWERS,
    "rain_showers_hi": Condition.SHOWERS,
    "hi_shwrs": Condition.SHOWERS,
    "drizzle": Condition.DRIZZLE,
    "fzdrizzle": Condition.DRIZZLE,
    "tsra": Condition.THUNDERSTORM,
    "tsra_sct": Condition.THUNDERSTORM,
    "tsra_hi": Condition.THUNDERSTORM,
    "trw": Condition.THUNDERSTORM,
    "sn": Condition.SNOW,
    "snow": Condition.SNOW,
    "blizzard": Condition.SNOW,
    "blowing_snow": Condition.SNOW,
    "snbr": Condition.SNOW,
    "heavy_snow": Condition.HEAVY_SNOW,
    "fzra": Condition.SLEET,
    "raip": Condition.SLEET,
    "rain_snow": Condition.SLEET,
    "sleet": Condition.SLEET,
    "ip": Condition.SLEET,
    "mix": Condition.SLEET,
    "rain_sleet": Condition.SLEET,
    "snow_sleet": Condition.SLEET,
    "slip": Condition.SLEET,
}


def nws_text_condition(text: str | None) -> Condition:
    t = (text or "").lower()
    if not t:
        return Condition.PARTLY_CLOUDY
    if "thunderstorm" in t or "t-storm" in t or "tsra" in t:
        return Condition.THUNDERSTORM
    if "heavy snow" in t or "blizzard" in t:
        return Condition.HEAVY_SNOW
    if "snow" in t and ("rain" in t or "sleet" in t or "mix" in t
                        or "freezing" in t):
        return Condition.SLEET
    if "snow" in t or "flurr" in t:
        return Condition.SNOW
    if "heavy rain" in t:
        return Condition.HEAVY_RAIN
    if "showers" in t:
        return Condition.SHOWERS
    if "drizzle" in t:
        return Condition.DRIZZLE
    if "rain" in t:
        return Condition.RAIN
    if ("fog" in t or "haze" in t or "smoke" in t or "mist" in t):
        return Condition.FOG
    if "sleet" in t or "freezing rain" in t:
        return Condition.SLEET
    if "mostly cloudy" in t or "considerable cloudiness" in t:
        return Condition.CLOUDY
    if "cloudy" in t or "overcast" in t:
        return Condition.OVERCAST
    if ("partly sunny" in t or "partly cloudy" in t
            or "increasing clouds" in t):
        return Condition.PARTLY_CLOUDY
    if "mostly sunny" in t or "mostly clear" in t:
        return Condition.MOSTLY_CLEAR
    if "sunny" in t or "clear" in t or "fair" in t:
        return Condition.CLEAR
    if "windy" in t or "breezy" in t:
        return Condition.WINDY
    return Condition.PARTLY_CLOUDY


def nws_icon_condition(url: str | None,
                       fallback_text: str | None = None) -> Condition:
    if url:
        tail = url.split("/icons/", 1)[1] if "/icons/" in url else ""
        tail = tail.split("?", 1)[0]
        if tail:
            tokens = re.split(r"[/,]", tail)
            try:
                start = next(i for i, tok in enumerate(tokens)
                             if tok in ("day", "night"))
            except StopIteration:
                start = -1
            if start >= 0:
                best: Condition | None = None
                for token in tokens[start + 1:]:
                    mapped = _ICON_TOKENS.get(token)
                    if mapped is None:
                        continue
                    if best is None or _SEVERITY[mapped] > _SEVERITY[best]:
                        best = mapped
                if best is not None:
                    return best
    return nws_text_condition(fallback_text)


def wmo_condition(code: int) -> Condition:
    if code == 0:
        return Condition.CLEAR
    if code == 1:
        return Condition.MOSTLY_CLEAR
    if code == 2:
        return Condition.PARTLY_CLOUDY
    if code == 3:
        return Condition.OVERCAST
    if code in (45, 48):
        return Condition.FOG
    if code in (51, 53, 55, 56, 57):
        return Condition.DRIZZLE
    if code in (61, 63):
        return Condition.RAIN
    if code == 65:
        return Condition.HEAVY_RAIN
    if code in (66, 67):
        return Condition.SLEET
    if code in (71, 73, 77, 85):
        return Condition.SNOW
    if code in (75, 86):
        return Condition.HEAVY_SNOW
    if code in (80, 81, 82):
        return Condition.SHOWERS
    if code in (95, 96, 99):
        return Condition.THUNDERSTORM
    return Condition.PARTLY_CLOUDY


# --- home-widget short text (the exact xx-wm line wording) ------------------

_WIDGET_WMO_TEXT = (
    ((0,), "Clear"),
    ((1, 2), "Partly cloudy"),
    ((3,), "Cloudy"),
    ((45, 48), "Fog"),
    ((51, 53, 55, 56, 57), "Drizzle"),
    ((61, 63, 65, 66, 67, 80, 81, 82), "Rain"),
    ((71, 73, 75, 77, 85, 86), "Snow"),
    ((95, 96, 99), "Storm"),
)


def wmo_text(code: int) -> str:
    for codes, text in _WIDGET_WMO_TEXT:
        if code in codes:
            return text
    return ""


def widget_format_current(temp: float, code: int,
                           unit: str = "fahrenheit") -> str:
    text = wmo_text(code)
    suffix = "F" if unit == "fahrenheit" else "C"
    return f"{round(temp)}°{suffix} {text}".strip()


OPENMETEO_CURRENT_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}&current=temperature_2m,weather_code"
    "&temperature_unit={unit}"
)


def openmeteo_current_url(lat: float, lon: float,
                          unit: str = "fahrenheit") -> str:
    return OPENMETEO_CURRENT_URL.format(lat=lat, lon=lon, unit=unit)


def parse_openmeteo_current(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("bad current-conditions payload")
    current = payload.get("current")
    if not isinstance(current, dict):
        raise ValueError("bad current-conditions payload")
    return {
        "temp": float(current["temperature_2m"]),
        "code": int(current.get("weather_code", -1)),
    }


def fetch_openmeteo_current(lat: float, lon: float, unit: str = "fahrenheit",
                            timeout: int = 10) -> dict:
    url = openmeteo_current_url(lat, lon, unit)
    with urllib.request.urlopen(url, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return parse_openmeteo_current(payload)


# --- models ----------------------------------------------------------------

class Units(str, Enum):
    FAHRENHEIT = "F"
    CELSIUS = "C"


@dataclass(frozen=True)
class Place:
    zip: str
    city: str
    state: str
    lat: float
    lon: float

    @property
    def display_name(self) -> str:
        return self.city if not self.state else f"{self.city}, {self.state}"


@dataclass(frozen=True)
class CurrentConditions:
    temp_f: float
    feels_like_f: float | None
    condition: Condition
    condition_text: str
    humidity_pct: int | None
    wind_mph: float | None
    wind_dir_deg: int | None
    pressure_inhg: float | None
    visibility_mi: float | None
    uv_index: float | None
    dewpoint_f: float | None
    pop_pct: int | None
    pressure_station_level: bool = False


@dataclass(frozen=True)
class HourlyPoint:
    time: datetime
    temp_f: float
    pop_pct: int | None
    condition: Condition
    is_day: bool


@dataclass(frozen=True)
class DailyPoint:
    date: date
    hi_f: float | None
    lo_f: float
    pop_pct: int | None
    condition: Condition
    uv_max: float | None


@dataclass(frozen=True)
class SunTimes:
    sunrise: datetime | None
    sunset: datetime | None


@dataclass(frozen=True)
class Extras:
    uv_index: float | None
    visibility_mi: float | None
    pressure_inhg: float | None
    dewpoint_f: float | None
    sun: SunTimes | None
    pressure_station_level: bool = False


@dataclass(frozen=True)
class WeatherData:
    place: Place
    updated_at_ms: int
    current: CurrentConditions
    hourly: tuple = ()
    daily: tuple = ()
    sun: SunTimes | None = None
    source: str = ""

    def hourly_on(self, day: date) -> list:
        return [h for h in self.hourly if h.time.date() == day]


def max_pop_next_hours(hourly: list, now_ms: int, hours: int = 12,
                       zone=None) -> int | None:
    now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    local = zone if zone is not None else datetime.now().astimezone().tzinfo
    now_local = now.astimezone(local).replace(tzinfo=None)
    pops = [h.pop_pct for h in hourly
            if h.time >= now_local and h.pop_pct is not None][:hours]
    return max(pops) if pops else None


# --- display formatting (US English, locale-independent) -------------------

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")
_WEEKDAYS_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONTHS_SHORT = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
                 "Sep", "Oct", "Nov", "Dec")

_WIND_DIRS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")


def convert_temp(fahrenheit: float, units: Units) -> float:
    if units == Units.CELSIUS:
        return (fahrenheit - 32.0) * 5.0 / 9.0
    return fahrenheit


def temp_str(fahrenheit: float | None, units: Units) -> str:
    if fahrenheit is None:
        return "--"
    return f"{round(convert_temp(fahrenheit, units))}°"


def hilo(hi_f: float | None, lo_f: float | None, units: Units) -> str:
    parts = []
    if hi_f is not None:
        parts.append(f"H:{round(convert_temp(hi_f, units))}°")
    if lo_f is not None:
        parts.append(f"L:{round(convert_temp(lo_f, units))}°")
    return " ".join(parts)


def _ampm(hour: int) -> str:
    h12 = hour % 12 or 12
    return f"{h12} {'AM' if hour < 12 else 'PM'}"


def hour_label(t: datetime) -> str:
    return _ampm(t.hour)


def clock_label(t: datetime) -> str:
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'AM' if t.hour < 12 else 'PM'}"


def updated_label(epoch_ms: int, zone=None) -> str:
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    local = zone if zone is not None else datetime.now().astimezone().tzinfo
    return clock_label(dt.astimezone(local).replace(tzinfo=None))


def day_label(day: date) -> str:
    return _WEEKDAYS_SHORT[day.weekday()]


def full_date(day: date) -> str:
    return f"{_WEEKDAYS[day.weekday()]}, {_MONTHS_SHORT[day.month - 1]} {day.day}"


def wind_dir(degrees: int) -> str:
    norm = ((degrees % 360) + 360) % 360
    return _WIND_DIRS[math.floor((norm + 11.25) / 22.5) % 16]


def uv_text(uv: float) -> str:
    if uv < 10.0:
        return f"{uv:.1f}"
    return str(round(uv))


def uv_label(uv: float | None) -> str:
    if uv is None:
        return ""
    if uv < 3.0:
        return "Low"
    if uv < 6.0:
        return "Moderate"
    if uv < 8.0:
        return "High"
    if uv < 11.0:
        return "Very High"
    return "Extreme"


def is_daytime(data: WeatherData, now: datetime | None = None) -> bool:
    at = now if now is not None else datetime.now()
    if data.sun is not None and data.sun.sunrise is not None \
            and data.sun.sunset is not None:
        return data.sun.sunrise < at < data.sun.sunset
    if data.hourly:
        return data.hourly[0].is_day
    return True


# --- sunrise/sunset (NOAA solar equations, public domain) ------------------

def suncalc_utc(day: date, lat_deg: float, lon_deg: float):
    rad = math.pi / 180.0
    n = day.toordinal() - date(2000, 1, 1).toordinal() + 0.0008
    j_star = n - lon_deg / 360.0
    mean_anomaly = _norm360(357.5291 + 0.98560028 * j_star)
    center = (1.9148 * math.sin(mean_anomaly * rad)
              + 0.02 * math.sin(2 * mean_anomaly * rad)
              + 0.0003 * math.sin(3 * mean_anomaly * rad))
    ecliptic_lon = _norm360(mean_anomaly + center + 180.0 + 102.9372)
    j_transit = (2451545.0 + j_star
                 + 0.0053 * math.sin(mean_anomaly * rad)
                 - 0.0069 * math.sin(2 * ecliptic_lon * rad))
    declination = math.asin(math.sin(ecliptic_lon * rad) * math.sin(23.44 * rad))
    cos_omega = (math.sin(-0.833 * rad) - math.sin(lat_deg * rad)
                 * math.sin(declination)) / (math.cos(lat_deg * rad)
                                             * math.cos(declination))
    if cos_omega < -1.0 or cos_omega > 1.0:
        return None
    omega = math.degrees(math.acos(cos_omega))
    return (_julian_to_ms(j_transit - omega / 360.0),
            _julian_to_ms(j_transit + omega / 360.0))


def _norm360(deg: float) -> float:
    d = deg % 360.0
    return d + 360.0 if d < 0 else d


def _julian_to_ms(julian_day: float) -> int:
    return int((julian_day - 2440587.5) * 86400000.0)


def suncalc_times(day: date, lat_deg: float, lon_deg: float, zone=None):
    utc = suncalc_utc(day, lat_deg, lon_deg)
    if utc is None:
        return None
    local = zone if zone is not None else datetime.now().astimezone().tzinfo
    rise = datetime.fromtimestamp(utc[0] / 1000, tz=timezone.utc)
    settled = datetime.fromtimestamp(utc[1] / 1000, tz=timezone.utc)
    return (rise.astimezone(local).replace(tzinfo=None),
            settled.astimezone(local).replace(tzinfo=None))


# --- ZIP geocoding --------------------------------------------------------

ZIP_RE = re.compile(r"^\d{5}$")

NWS_UA = "(xx-weather, https://github.com/PiercingXX/xx-weather)"


def _vi(zip_code: str, city: str, lat: float, lon: float) -> Place:
    return Place(zip=zip_code, city=city, state="VI", lat=lat, lon=lon)


TERRITORY_ZIPS = {p.zip: p for p in (
    _vi("00801", "Charlotte Amalie", 18.3419, -64.9307),
    _vi("00802", "Charlotte Amalie", 18.3419, -64.9307),
    _vi("00803", "Charlotte Amalie", 18.3419, -64.9307),
    _vi("00804", "Charlotte Amalie", 18.3419, -64.9307),
    _vi("00805", "Charlotte Amalie", 18.3381, -64.8941),
    _vi("00820", "Christiansted", 17.7466, -64.7032),
    _vi("00821", "Christiansted", 17.7466, -64.7032),
    _vi("00822", "Christiansted", 17.7466, -64.7032),
    _vi("00823", "Christiansted", 17.7466, -64.7032),
    _vi("00824", "Christiansted", 17.7466, -64.7032),
    _vi("00830", "Cruz Bay", 18.3313, -64.7956),
    _vi("00831", "Cruz Bay", 18.3313, -64.7956),
    _vi("00840", "Frederiksted", 17.7125, -64.8819),
    _vi("00841", "Frederiksted", 17.7125, -64.8819),
    _vi("00850", "Kingshill", 17.7097, -64.7986),
    _vi("00851", "Kingshill", 17.7097, -64.7986),
)}


def normalize_zip(raw: str) -> str:
    zip_code = raw.strip()
    if not ZIP_RE.match(zip_code):
        raise ValueError("Enter a valid 5-digit ZIP code.")
    return zip_code


def local_place(zip_code: str) -> Place | None:
    return TERRITORY_ZIPS.get(zip_code)


def countries_for(zip_code: str) -> list:
    if zip_code.startswith("008"):
        return ["vi", "us"]
    if zip_code.startswith(("006", "007", "009")):
        return ["pr", "us"]
    return ["us"]


def parse_zippopotam(body: str, zip_code: str) -> Place:
    try:
        root = json.loads(body)
    except ValueError:
        raise OSError(f"Bad geocode response for {zip_code}")
    places = root.get("places") if isinstance(root, dict) else None
    if not places:
        raise OSError(f"ZIP code not found: {zip_code}")
    try:
        first = places[0]
        lat = float(first.get("latitude", ""))
        lon = float(first.get("longitude", ""))
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise OSError(f"Bad geocode response for {zip_code}")
    city = (first.get("place name") or "").strip() or zip_code
    state = (first.get("state abbreviation") or "").strip() \
        or (first.get("state") or "").strip()
    return Place(zip=zip_code, city=city, state=state, lat=lat, lon=lon)


def geocode_zip(raw: str, fetch: Callable[[str], str]) -> Place:
    zip_code = normalize_zip(raw)
    local = local_place(zip_code)
    if local is not None:
        return local
    last_error: Exception | None = None
    for country in countries_for(zip_code):
        try:
            return parse_zippopotam(
                fetch(f"https://api.zippopotam.us/{country}/{zip_code}"),
                zip_code)
        except Exception as exc:  # noqa: BLE001 - every country gets a try
            last_error = exc
    raise OSError(str(last_error) if last_error is not None
                  else f"Couldn't look up ZIP {zip_code} — check the code "
                  "and your connection.")


# --- saved-location + trigger codecs --------------------------------------

def encode_places(places: list) -> str:
    return json.dumps([{"zip": p.zip, "city": p.city, "state": p.state,
                        "lat": p.lat, "lon": p.lon} for p in places])


def decode_places(raw: str | None) -> list:
    if not raw or not raw.strip():
        return []
    try:
        items = json.loads(raw)
    except ValueError:
        return []
    out = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        zip_code = entry.get("zip", "")
        if not ZIP_RE.match(zip_code or ""):
            continue
        try:
            lat = float(entry["lat"])
            lon = float(entry["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        city = (entry.get("city") or "").strip() or zip_code
        out.append(Place(zip=zip_code, city=city,
                         state=entry.get("state") or "", lat=lat, lon=lon))
    return out


@dataclass(frozen=True)
class ZipAlertTriggers:
    precip_enabled: bool = False
    precip_hours: int = 6
    temp_enabled: bool = False
    temp_at_or_below_f: int = 32

    @property
    def any_enabled(self) -> bool:
        return self.precip_enabled or self.temp_enabled


DEFAULT_PRECIP_HOURS = 6
DEFAULT_TEMP_F = 32
PRECIP_POP_PCT = 50


def encode_triggers(mapping: dict) -> str:
    return json.dumps({zip_code: {"precip": t.precip_enabled,
                                  "hours": t.precip_hours,
                                  "temp": t.temp_enabled,
                                  "tempF": t.temp_at_or_below_f}
                       for zip_code, t in mapping.items()})


def decode_triggers(raw: str | None) -> dict:
    if not raw or not raw.strip():
        return {}
    try:
        root = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(root, dict):
        return {}
    out = {}
    for zip_code, entry in root.items():
        if not ZIP_RE.match(zip_code or "") or not isinstance(entry, dict):
            continue
        try:
            hours = max(1, min(24, int(entry.get("hours",
                                                DEFAULT_PRECIP_HOURS))))
        except (TypeError, ValueError):
            hours = DEFAULT_PRECIP_HOURS
        try:
            temp_f = max(-80, min(140, int(entry.get("tempF",
                                                    DEFAULT_TEMP_F))))
        except (TypeError, ValueError):
            temp_f = DEFAULT_TEMP_F
        out[zip_code] = ZipAlertTriggers(
            precip_enabled=bool(entry.get("precip", False)),
            precip_hours=hours,
            temp_enabled=bool(entry.get("temp", False)),
            temp_at_or_below_f=temp_f)
    return out


# --- alert evaluation ------------------------------------------------------

class AlertKind(str, Enum):
    PRECIP = "PRECIP"
    TEMP = "TEMP"


@dataclass(frozen=True)
class AlertFire:
    kind: AlertKind
    zip: str
    window: int
    title: str
    text: str

    @property
    def key(self) -> str:
        return dedupe_key(self.zip, self.kind, self.window)


_PRECIP_CONDITIONS = frozenset({
    Condition.DRIZZLE, Condition.RAIN, Condition.HEAVY_RAIN,
    Condition.SHOWERS, Condition.THUNDERSTORM, Condition.SNOW,
    Condition.HEAVY_SNOW, Condition.SLEET,
})


def dedupe_key(zip_code: str, kind: AlertKind, window: int) -> str:
    return f"{zip_code}|{kind.value}|{window}"


def remember_keys(already, new_keys, limit: int = 64) -> list:
    """Ordered dedupe history: Python sets are unordered, so callers keep
    an ordered list (stored as a JSON array, not a set) and this preserves
    insertion order, dropping the oldest past the limit."""
    ordered = list(already)
    fresh = [k for k in new_keys if k not in ordered]
    if not fresh:
        return ordered
    return (ordered + fresh)[-limit:]


def upcoming_hourly(hourly: list, now_ms: int, hours: int, zone=None) -> list:
    now = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    local = zone if zone is not None else datetime.now().astimezone().tzinfo
    now_local = now.astimezone(local).replace(tzinfo=None)
    return [h for h in hourly if h.time >= now_local][:max(1, min(24, hours))]


def evaluate_alerts(zip_code: str, city: str, hourly: list,
                    current_temp_f: float, fetched_at_ms: int, now_ms: int,
                    triggers: ZipAlertTriggers, already_notified: set,
                    zone=None) -> list:
    if not triggers.any_enabled:
        return []
    where = city.strip() or zip_code
    out = []
    if triggers.precip_enabled:
        hours = max(1, min(24, triggers.precip_hours))
        upcoming = upcoming_hourly(hourly, now_ms, hours, zone)
        pops = [h.pop_pct for h in upcoming if h.pop_pct is not None]
        max_pop = max(pops) if pops else None
        precip_now = any(h.condition in _PRECIP_CONDITIONS for h in upcoming)
        if (max_pop is not None and max_pop >= PRECIP_POP_PCT) or precip_now:
            fire = AlertFire(
                kind=AlertKind.PRECIP, zip=zip_code, window=fetched_at_ms,
                title="Precipitation nearby",
                text=(f"Precipitation in the next {hours} hours in {where}"
                      + (f" ({max_pop}% chance)" if max_pop is not None else "")
                      + "."))
            if fire.key not in already_notified:
                out.append(fire)
    if triggers.temp_enabled and current_temp_f <= triggers.temp_at_or_below_f:
        fire = AlertFire(
            kind=AlertKind.TEMP, zip=zip_code, window=fetched_at_ms,
            title="Temperature alert",
            text=(f"{where} is {round(current_temp_f)}°F (at or below "
                  f"{triggers.temp_at_or_below_f}°F)."))
        if fire.key not in already_notified:
            out.append(fire)
    return out


# --- HTTP ------------------------------------------------------------------

def http_get(url: str, user_agent: str | None = None,
             timeout_ms: int = 12000) -> str:
    total = max(500, timeout_ms)
    connect_ms = max(250, total // 3)
    read_s = max(250, total - connect_ms) / 1000.0
    request = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    if user_agent is not None:
        request.add_header("User-Agent", user_agent)
    try:
        with urllib.request.urlopen(request, timeout=read_s) as response:
            status = getattr(response, "status", 200)
            raw = response.read()
            encoding = ""
            try:
                encoding = response.headers.get("Content-Encoding", "")
            except AttributeError:
                pass
            if encoding and encoding.lower() == "gzip":
                raw = gzip.decompress(raw)
            body = raw.decode("utf-8")
            if status is not None and not 200 <= status <= 299:
                raise OSError(f"HTTP {status}: {body[:200]}")
            return body
    except OSError as exc:
        raise OSError(f"HTTP fetch failed for {url}: {exc}")


# --- NWS source ------------------------------------------------------------

NWS_POINTS_URL = "https://api.weather.gov/points/{lat:.4f},{lon:.4f}"


@dataclass(frozen=True)
class NwsBodies:
    daily: str
    hourly: str
    obs: str | None
    city: str | None
    state: str | None


def nws_points_url(place: Place) -> str:
    return NWS_POINTS_URL.format(lat=place.lat, lon=place.lon)


def _nws_bool(entry: dict, key: str, default: bool = True) -> bool:
    value = entry.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return default


def parse_nws_hourly(body: str) -> list:
    try:
        periods = json.loads(body)["properties"]["periods"]
    except (ValueError, KeyError, TypeError):
        return []
    out = []
    for entry in periods:
        if not isinstance(entry, dict):
            continue
        try:
            when = datetime.fromisoformat(entry["startTime"])
        except (KeyError, ValueError, TypeError):
            continue
        if when.tzinfo is not None:
            when = when.replace(tzinfo=None)
        temp = entry.get("temperature")
        if not isinstance(temp, (int, float)) or temp != temp:
            continue
        pop = _nws_pop(entry)
        out.append(HourlyPoint(
            time=when, temp_f=float(temp), pop_pct=pop,
            condition=nws_icon_condition(entry.get("icon"),
                                         entry.get("shortForecast")),
            is_day=_nws_bool(entry, "isDaytime")))
    return out


def _nws_pop(entry: dict) -> int | None:
    prob = entry.get("probabilityOfPrecipitation")
    if not isinstance(prob, dict):
        return None
    try:
        value = int(prob.get("value"))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def parse_nws_daily(body: str) -> list:
    try:
        periods = json.loads(body)["properties"]["periods"]
    except (ValueError, KeyError, TypeError):
        return []
    aggs: dict = {}
    for entry in periods:
        if not isinstance(entry, dict):
            continue
        try:
            day = datetime.fromisoformat(entry["startTime"]).date()
        except (KeyError, ValueError, TypeError):
            continue
        temp = entry.get("temperature")
        if not isinstance(temp, (int, float)) or temp != temp:
            continue
        is_day = _nws_bool(entry, "isDaytime")
        pop = _nws_pop(entry)
        cond = nws_icon_condition(entry.get("icon"),
                                  entry.get("shortForecast"))
        agg = aggs.get(day)
        if agg is None:
            agg = {"hi": None, "lo": None, "pop": None,
                   "first": None, "day": None}
            aggs[day] = agg
        if is_day:
            agg["hi"] = temp if agg["hi"] is None else max(agg["hi"], temp)
            if agg["day"] is None:
                agg["day"] = cond
        else:
            agg["lo"] = temp if agg["lo"] is None else min(agg["lo"], temp)
        if agg["first"] is None:
            agg["first"] = cond
        if pop is not None:
            agg["pop"] = pop if agg["pop"] is None else max(agg["pop"], pop)
    out = []
    for day, agg in list(aggs.items())[:10]:
        hi = agg["hi"]
        lo = agg["lo"] if agg["lo"] is not None else agg["hi"]
        out.append(DailyPoint(
            date=day, hi_f=hi, lo_f=lo if lo is not None else 0.0,
            pop_pct=agg["pop"],
            condition=agg["day"] if agg["day"] is not None
            else (agg["first"] if agg["first"] is not None
                  else Condition.PARTLY_CLOUDY),
            uv_max=None))
    return out


def parse_nws_obs(body: str | None) -> CurrentConditions | None:
    if not body:
        return None
    try:
        props = json.loads(body)["properties"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(props, dict):
        return None

    def num(key: str) -> float | None:
        try:
            value = props[key]["value"]
        except (KeyError, TypeError):
            return None
        if not isinstance(value, (int, float)) or value != value:
            return None
        return float(value)

    def c_to_f(value: float | None) -> float | None:
        return value * 9.0 / 5.0 + 32.0 if value is not None else None

    temp_f = c_to_f(num("temperature"))
    if temp_f is None:
        return None
    feels_c = num("windChill") if temp_f <= 60.0 else num("heatIndex")
    sea_level_pa = num("seaLevelPressure")
    barometric_pa = num("barometricPressure")
    pressure_pa = sea_level_pa if sea_level_pa is not None else barometric_pa
    condition = nws_icon_condition(props.get("icon"),
                                   props.get("textDescription"))
    text = (props.get("textDescription") or "").strip()

    def int_or_none(value: float | None) -> int | None:
        return int(value) if value is not None else None

    return CurrentConditions(
        temp_f=temp_f,
        feels_like_f=c_to_f(feels_c),
        condition=condition,
        condition_text=text or condition_label(condition),
        humidity_pct=int_or_none(num("relativeHumidity")),
        wind_mph=(num("windSpeed") * 0.621371
                  if num("windSpeed") is not None else None),
        wind_dir_deg=int_or_none(num("windDirection")),
        pressure_inhg=(pressure_pa / 3386.389
                       if pressure_pa is not None else None),
        visibility_mi=(num("visibility") / 1609.344
                       if num("visibility") is not None else None),
        uv_index=None,
        dewpoint_f=c_to_f(num("dewpoint")),
        pop_pct=None,
        pressure_station_level=(sea_level_pa is None
                                and barometric_pa is not None))


def assemble_nws(place: Place, bodies: NwsBodies,
                 now_ms: int) -> WeatherData:
    hourly = parse_nws_hourly(bodies.hourly)
    if not hourly:
        raise OSError("NWS hourly forecast was empty")
    daily = []
    for point in parse_nws_daily(bodies.daily):
        if point.hi_f is not None:
            daily.append(point)
            continue
        backfill = [h.temp_f for h in hourly if h.time.date() == point.date]
        daily.append(DailyPoint(date=point.date,
                                hi_f=max(backfill) if backfill else None,
                                lo_f=point.lo_f, pop_pct=point.pop_pct,
                                condition=point.condition, uv_max=None))
    observed = parse_nws_obs(bodies.obs)
    if observed is None:
        first = hourly[0]
        observed = CurrentConditions(
            temp_f=first.temp_f, feels_like_f=None, condition=first.condition,
            condition_text=condition_label(first.condition), humidity_pct=None,
            wind_mph=None, wind_dir_deg=None, pressure_inhg=None,
            visibility_mi=None, uv_index=None, dewpoint_f=None, pop_pct=None)
    current = CurrentConditions(
        temp_f=observed.temp_f, feels_like_f=observed.feels_like_f,
        condition=observed.condition, condition_text=observed.condition_text,
        humidity_pct=observed.humidity_pct, wind_mph=observed.wind_mph,
        wind_dir_deg=observed.wind_dir_deg,
        pressure_inhg=observed.pressure_inhg,
        visibility_mi=observed.visibility_mi, uv_index=observed.uv_index,
        dewpoint_f=observed.dewpoint_f,
        pop_pct=(observed.pop_pct
                 if observed.pop_pct is not None
                 else max_pop_next_hours(hourly, now_ms)),
        pressure_station_level=observed.pressure_station_level)
    sun = suncalc_times(date.today(), place.lat, place.lon)
    sun_times = SunTimes(*sun) if sun is not None else None
    resolved = place
    if bodies.city:
        resolved = Place(zip=place.zip, city=bodies.city,
                         state=bodies.state or place.state,
                         lat=place.lat, lon=place.lon)
    return WeatherData(place=resolved, updated_at_ms=now_ms, current=current,
                       hourly=tuple(hourly), daily=tuple(daily),
                       sun=sun_times, source="NWS")


def fetch_nws_bodies(place: Place, get: Callable[[str, int], str],
                     now_ms: int | None = None,
                     deadline_ms: int | None = None) -> NwsBodies:
    import time as _time

    def remaining() -> int | None:
        if deadline_ms is None:
            return None
        now = now_ms if now_ms is not None else int(_time.time() * 1000)
        return deadline_ms - now

    def assert_budget() -> None:
        if remaining() is not None and remaining() <= 500:
            raise OSError("NWS fetch budget exhausted")

    def timeout() -> int:
        left = remaining()
        if left is None:
            return 12000
        return max(500, min(12000, left))

    points = json.loads(get(nws_points_url(place), timeout()))
    props = points["properties"]
    hourly_url = props["forecastHourly"]
    daily_url = props["forecast"]
    stations_url = props.get("observationStations") or ""
    relative = (props.get("relativeLocation") or {}).get("properties") or {}
    city = relative.get("city") or None
    state = relative.get("state") or None
    assert_budget()
    hourly = get(hourly_url, timeout())
    assert_budget()
    daily = get(daily_url, timeout())
    obs = None
    try:
        if stations_url:
            left = remaining()
            if left is None or left >= 1500:
                features = json.loads(
                    get(stations_url, min(timeout(), 4000)))["features"]
                if features:
                    station = features[0]["properties"]["stationIdentifier"]
                    obs_timeout = remaining()
                    if obs_timeout is None or obs_timeout > 500:
                        obs = get(
                            "https://api.weather.gov/stations/"
                            f"{station}/observations/latest",
                            min(obs_timeout if obs_timeout is not None
                                else 4000, 4000))
    except Exception:  # noqa: BLE001 - observations are best-effort
        obs = None
    return NwsBodies(daily=daily, hourly=hourly, obs=obs,
                     city=city, state=state)


# --- Open-Meteo source -----------------------------------------------------

OPENMETEO_BASE = "https://api.open-meteo.com/v1/forecast"


def openmeteo_forecast_url(place: Place) -> str:
    return (
        f"{OPENMETEO_BASE}?latitude={place.lat}&longitude={place.lon}"
        "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
        "is_day,weather_code,wind_speed_10m,wind_direction_10m,"
        "surface_pressure,pressure_msl,dew_point_2m,uv_index,visibility"
        "&hourly=temperature_2m,precipitation_probability,weather_code,is_day"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max,sunrise,sunset"
        "&temperature_unit=fahrenheit&wind_speed_unit=mph"
        "&precipitation_unit=inch&timezone=auto&forecast_days=10")


def openmeteo_extras_url(place: Place) -> str:
    return (
        f"{OPENMETEO_BASE}?latitude={place.lat}&longitude={place.lon}"
        "&current=dew_point_2m,uv_index,visibility,surface_pressure,"
        "pressure_msl&daily=sunrise,sunset&timezone=auto"
        "&temperature_unit=fahrenheit&precipitation_unit=inch")


def visibility_to_mi(value: float | None, units: dict | None) -> float | None:
    if value is None:
        return None
    label = ""
    if isinstance(units, dict):
        label = str(units.get("visibility", "")).lower().strip()
    if label in ("ft", "feet"):
        return value / 5280.0
    if label in ("mi", "mile", "miles"):
        return value
    return value / 1609.344


def _om_num(entry: dict, key: str) -> float | None:
    try:
        value = entry[key]
    except (KeyError, TypeError):
        return None
    if not isinstance(value, (int, float)) or value != value:
        return None
    return float(value)


def _om_local(entry, idx: int):
    try:
        return datetime.fromisoformat(entry[idx])
    except (ValueError, TypeError, IndexError, KeyError):
        return None


def parse_openmeteo_extras(body: str, place: Place) -> Extras:
    root = json.loads(body)
    current = root.get("current", {})
    units = root.get("current_units")
    daily = root.get("daily") or {}
    sun = _om_sun(daily)
    msl = _om_num(current, "pressure_msl")
    surface = _om_num(current, "surface_pressure")
    if sun is None:
        local = suncalc_times(date.today(), place.lat, place.lon)
        sun = SunTimes(*local) if local is not None else None
    return Extras(
        uv_index=_om_num(current, "uv_index"),
        visibility_mi=visibility_to_mi(_om_num(current, "visibility"), units),
        pressure_inhg=((msl if msl is not None else surface) * 0.02953
                       if (msl if msl is not None else surface) is not None
                       else None),
        dewpoint_f=_om_num(current, "dew_point_2m"),
        sun=sun,
        pressure_station_level=(msl is None and surface is not None))


def _om_sun(daily: dict) -> SunTimes | None:
    try:
        sunrise = daily["sunrise"][0]
        sunset = daily["sunset"][0]
        return SunTimes(datetime.fromisoformat(sunrise),
                        datetime.fromisoformat(sunset))
    except (ValueError, TypeError, KeyError, IndexError):
        return None


def parse_openmeteo(place: Place, body: str, now_ms: int) -> WeatherData:
    root = json.loads(body)
    current = root["current"]
    units = root.get("current_units")
    code = current.get("weather_code", -1)
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = -1
    condition = wmo_condition(code) if code >= 0 else nws_text_condition(None)

    hourly_obj = root["hourly"]
    times = hourly_obj["time"]
    temps = hourly_obj["temperature_2m"]
    pops = hourly_obj.get("precipitation_probability")
    codes = hourly_obj["weather_code"]
    is_day = hourly_obj.get("is_day")
    now_local = datetime.now().replace(tzinfo=None)
    start = 0
    for i in range(len(times)):
        moment = _om_local(times, i)
        if moment is None:
            continue
        if moment >= now_local:
            start = i
            break
        start = i
    hourly = []
    i = start
    while i < len(times) and len(hourly) < 24:
        moment = _om_local(times, i)
        if moment is not None:
            try:
                temp = float(temps[i])
            except (TypeError, ValueError, IndexError):
                temp = None
            code = _om_idx_int(codes, i, 2)
            day_flag = _om_idx_int(is_day, i, 1)
            hourly.append(HourlyPoint(
                time=moment,
                temp_f=(temp if temp is not None and temp == temp else 0.0),
                pop_pct=_om_idx_int(pops, i),
                condition=wmo_condition(code if code is not None else 2),
                is_day=(day_flag if day_flag is not None else 1) == 1))
        i += 1

    daily_obj = root["daily"]
    d_times = daily_obj["time"]
    count = min(len(d_times), 10)
    daily = []
    for idx in range(count):
        try:
            day = date.fromisoformat(d_times[idx])
        except (ValueError, TypeError):
            continue
        hi = _om_idx_float(daily_obj.get("temperature_2m_max"), idx)
        lo = _om_idx_float(daily_obj.get("temperature_2m_min"), idx)
        day_code = _om_idx_int(daily_obj.get("weather_code"), idx, 2)
        daily.append(DailyPoint(
            date=day, hi_f=hi, lo_f=lo if lo is not None else 0.0,
            pop_pct=_om_idx_int(
                daily_obj.get("precipitation_probability_max"), idx),
            condition=wmo_condition(day_code if day_code is not None else 2),
            uv_max=None))

    sun = _om_sun(daily_obj)
    if sun is None:
        local = suncalc_times(date.today(), place.lat, place.lon)
        sun = SunTimes(*local) if local is not None else None

    msl_hpa = _om_num(current, "pressure_msl")
    surface_hpa = _om_num(current, "surface_pressure")
    pressure = msl_hpa if msl_hpa is not None else surface_hpa
    feels = _om_num(current, "apparent_temperature")
    humidity = _om_num(current, "relative_humidity_2m")
    wind = _om_num(current, "wind_speed_10m")
    wind_dir_v = _om_num(current, "wind_direction_10m")
    observed_temp = _om_num(current, "temperature_2m")
    if observed_temp is None:
        observed_temp = hourly[0].temp_f if hourly else 0.0
    current_conditions = CurrentConditions(
        temp_f=observed_temp,
        feels_like_f=feels,
        condition=condition,
        condition_text=condition_label(condition),
        humidity_pct=int(humidity) if humidity is not None else None,
        wind_mph=wind,
        wind_dir_deg=int(wind_dir_v) if wind_dir_v is not None else None,
        pressure_inhg=(pressure * 0.02953
                       if pressure is not None else None),
        visibility_mi=visibility_to_mi(_om_num(current, "visibility"), units),
        uv_index=_om_num(current, "uv_index"),
        dewpoint_f=_om_num(current, "dew_point_2m"),
        pop_pct=max_pop_next_hours(hourly, now_ms),
        pressure_station_level=(msl_hpa is None and surface_hpa is not None))
    return WeatherData(place=place, updated_at_ms=now_ms,
                       current=current_conditions, hourly=tuple(hourly),
                       daily=tuple(daily), sun=sun, source="Open-Meteo")


def _om_idx_int(arr, idx: int, default: int = -1) -> int | None:
    if arr is None:
        return None
    try:
        value = int(arr[idx])
    except (TypeError, ValueError, IndexError, KeyError):
        return default if default >= 0 else None
    return value if value >= 0 else None


def _om_idx_float(arr, idx: int) -> float | None:
    if arr is None:
        return None
    try:
        value = float(arr[idx])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    return value if value == value else None


def merge_extras(data: WeatherData, extra: Extras) -> WeatherData:
    used_pressure = (data.current.pressure_inhg is None
                     and extra.pressure_inhg is not None)
    current = CurrentConditions(
        temp_f=data.current.temp_f,
        feels_like_f=data.current.feels_like_f,
        condition=data.current.condition,
        condition_text=data.current.condition_text,
        humidity_pct=data.current.humidity_pct,
        wind_mph=data.current.wind_mph,
        wind_dir_deg=data.current.wind_dir_deg,
        pressure_inhg=(data.current.pressure_inhg
                       if data.current.pressure_inhg is not None
                       else extra.pressure_inhg),
        visibility_mi=(data.current.visibility_mi
                       if data.current.visibility_mi is not None
                       else extra.visibility_mi),
        uv_index=(data.current.uv_index
                  if data.current.uv_index is not None else extra.uv_index),
        dewpoint_f=(data.current.dewpoint_f
                    if data.current.dewpoint_f is not None
                    else extra.dewpoint_f),
        pop_pct=data.current.pop_pct,
        pressure_station_level=(extra.pressure_station_level
                                if used_pressure
                                else data.current.pressure_station_level))
    return WeatherData(place=data.place, updated_at_ms=data.updated_at_ms,
                       current=current, hourly=data.hourly, daily=data.daily,
                       sun=data.sun if data.sun is not None else extra.sun,
                       source=data.source)


# --- freshness + pager UI state --------------------------------------------

STALE_MS = 15 * 60 * 1000


def is_fresh(fetched_at_ms: int, now_ms: int,
             stale_ms: int = STALE_MS) -> bool:
    return 0 <= now_ms - fetched_at_ms < stale_ms


@dataclass(frozen=True)
class RefreshSuccess:
    data: WeatherData
    from_cache: bool = False


@dataclass(frozen=True)
class RefreshFailure:
    message: str
    stale: WeatherData | None = None


@dataclass(frozen=True)
class ZipRefreshState:
    data_by_zip: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)


def apply_refresh_result(state: ZipRefreshState, zip_code: str,
                         result) -> ZipRefreshState:
    if isinstance(result, RefreshSuccess):
        data = dict(state.data_by_zip)
        data[zip_code] = result.data
        errors = {k: v for k, v in state.errors.items() if k != zip_code}
        return ZipRefreshState(data_by_zip=data, errors=errors)
    data = dict(state.data_by_zip)
    if result.stale is not None:
        data[zip_code] = result.stale
    elif zip_code not in data:
        data[zip_code] = None
    errors = dict(state.errors)
    errors[zip_code] = result.message
    return ZipRefreshState(data_by_zip=data, errors=errors)


def zip_shows_spinner(zip_code: str, data_by_zip: dict,
                      errors: dict) -> bool:
    return zip_code not in data_by_zip and zip_code not in errors


def should_refresh_on_start_tick(start_tick: int) -> bool:
    return start_tick > 0
