"""Seeded demo data for screenshots and smoke tests.

Writes synthetic (but real-format) NWS bodies through the normal cache
files, so the seeded window exercises the genuine offline-load path.
xx-auto's demo.py is the pattern.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

DENVER_ZIP = "80202"
DENVER_CITY = "Denver"
DENVER_STATE = "CO"
DENVER_LAT = 39.7392
DENVER_LON = -104.9903

AUSTIN_ZIP = "78701"
AUSTIN_CITY = "Austin"
AUSTIN_STATE = "TX"
AUSTIN_LAT = 30.2672
AUSTIN_LON = -97.7431


def _hourly_periods(base: datetime) -> list:
    icons = ["day/skc", "day/few", "day/sct", "day/bkn", "day/sct",
             "day/tsra_hi,40", "day/shra", "night/sct", "night/skc"]
    texts = ["Sunny", "Mostly Sunny", "Partly Cloudy", "Mostly Cloudy",
             "Partly Cloudy", "Thunderstorms", "Rain Showers", "Partly Cloudy",
             "Clear"]
    temps = [72, 74, 76, 78, 80, 79, 75, 70, 66, 64, 62, 61,
             60, 59, 58, 58, 60, 64, 68, 72, 75, 77, 78, 79]
    pops = [0, 0, 0, 10, 10, 60, 50, 30, 20, 10, 10, 0,
            0, 0, 0, 0, 0, 0, 0, 0, 10, 10, 20, 20]
    periods = []
    for index in range(24):
        start = base + timedelta(hours=index)
        periods.append({
            "startTime": start.strftime("%Y-%m-%dT%H:00:00-06:00"),
            "temperature": temps[index],
            "probabilityOfPrecipitation": {
                "unitCode": "wmoUnit:percent", "value": pops[index]},
            "shortForecast": texts[index % len(texts)],
            "icon": "https://api.weather.gov/icons/land/"
                    + icons[index % len(icons)],
            "isDaytime": "true" if 6 <= start.hour < 19 else "false",
        })
    return periods


def _daily_periods(today: datetime) -> list:
    rows = [("Today", True, 80), ("Tonight", False, 61),
            ("Tuesday", True, 82), ("Tuesday Night", False, 60),
            ("Wednesday", True, 84), ("Wednesday Night", False, 62)]
    periods = []
    for index, (name, day, temp) in enumerate(rows):
        start = today + timedelta(days=index // 2)
        hour = 12 if day else 0
        periods.append({
            "name": name,
            "startTime": start.replace(hour=hour, minute=0).strftime(
                "%Y-%m-%dT%H:00:00-06:00"),
            "temperature": temp,
            "probabilityOfPrecipitation": {
                "unitCode": "wmoUnit:percent",
                "value": 60 if index == 1 else 20},
            "shortForecast": "Thunderstorms" if index == 1
                             else "Partly Cloudy",
            "icon": "https://api.weather.gov/icons/land/"
                    + ("day/tsra_hi,40" if index == 1 else "day/sct"),
            "isDaytime": "true" if day else "false",
        })
    return periods


def _obs_body() -> str:
    return json.dumps({"properties": {
        "temperature": {"value": 24.0},
        "heatIndex": {"value": 25.5},
        "relativeHumidity": {"value": 41},
        "windSpeed": {"value": 18.0},
        "windDirection": {"value": 225},
        "seaLevelPressure": {"value": 101500},
        "visibility": {"value": 16000},
        "dewpoint": {"value": 10.0},
        "textDescription": "Partly Cloudy",
        "icon": "https://api.weather.gov/icons/land/day/sct?size=medium",
    }})


def seed_current(store, now_ms: int | None = None) -> None:
    """One fresh saved location (Denver) readable with no network."""
    from xx_phone.weather import Place
    from xx_weather.store import NWS_DAILY, NWS_HOURLY, NWS_OBS

    place = Place(zip=DENVER_ZIP, city=DENVER_CITY, state=DENVER_STATE,
                  lat=DENVER_LAT, lon=DENVER_LON)
    store.add_place(place)
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    store.write_cache(DENVER_ZIP, NWS_HOURLY, json.dumps(
        {"properties": {"periods": _hourly_periods(now)}}))
    store.write_cache(DENVER_ZIP, NWS_DAILY, json.dumps(
        {"properties": {"periods": _daily_periods(now)}}))
    store.write_cache(DENVER_ZIP, NWS_OBS, _obs_body())
    store.write_stamp(DENVER_ZIP, now_ms if now_ms is not None
                      else int(time.time() * 1000))


def seed_places(store) -> None:
    """Denver cached plus an uncached second location."""
    from xx_phone.weather import Place

    seed_current(store)
    store.add_place(Place(zip=AUSTIN_ZIP, city=AUSTIN_CITY, state=AUSTIN_STATE,
                          lat=AUSTIN_LAT, lon=AUSTIN_LON))
    store.set_selected_zip(DENVER_ZIP)


def seed_alerts(store) -> None:
    """Alerts on with both triggers for the seeded location."""
    from xx_phone.weather import ZipAlertTriggers

    seed_current(store)
    store.set_alerts_enabled(True)
    store.set_triggers(DENVER_ZIP, ZipAlertTriggers(
        precip_enabled=True, precip_hours=6,
        temp_enabled=True, temp_at_or_below_f=32))
