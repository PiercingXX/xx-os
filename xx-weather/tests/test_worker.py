"""Worker + notify: refresh orchestration and alert delivery with fakes.
Never the network: demo bodies and inline JSON stand in for HTTP."""

import json

import pytest

from conftest import make_store

def _clean_store():
    from xx_phone.weather import Place

    store = make_store()
    store.add_place(Place("80202", "Denver", "CO", 39.7392, -104.9903))
    return store


def test_nws_success_caches_and_labels():
    from xx_phone.weather import RefreshSuccess
    from xx_weather import demo
    from xx_weather.store import NWS_DAILY, NWS_HOURLY, NWS_OBS
    from xx_weather.worker import refresh_place

    demo_store = make_store()
    demo.seed_current(demo_store)
    bodies = {
        "hourly": demo_store.read_cache("80202", NWS_HOURLY),
        "daily": demo_store.read_cache("80202", NWS_DAILY),
        "obs": demo_store.read_cache("80202", NWS_OBS),
    }
    store = _clean_store()

    def _get(url, timeout_ms):
        if "api.weather.gov/points" in url:
            return json.dumps({"properties": {
                "forecastHourly": "https://api.weather.gov/h",
                "forecast": "https://api.weather.gov/d",
                "observationStations": "https://api.weather.gov/st",
                "relativeLocation": {"properties": {
                    "city": "Denver", "state": "CO"}}}})
        if url == "https://api.weather.gov/h":
            return bodies["hourly"]
        if url == "https://api.weather.gov/d":
            return bodies["daily"]
        if url == "https://api.weather.gov/st":
            return json.dumps({"features": [{"properties": {
                "stationIdentifier": "KDEN"}}]})
        if "observations/latest" in url:
            return bodies["obs"]
        if "open-meteo" in url:
            return json.dumps({"current": {"uv_index": 5.5},
                               "daily": {"sunrise": [], "sunset": []}})
        raise AssertionError(f"unexpected url {url}")

    result = refresh_place(store, store.selected(), force=True, get=_get,
                           clock_ms=lambda: 1700000000000)
    assert isinstance(result, RefreshSuccess)
    assert result.data.source == "NWS"
    assert result.data.updated_at_ms == 1700000000000
    assert result.data.current.uv_index == 5.5
    assert store.read_stamp("80202") == 1700000000000
    assert store.selected().city == "Denver"


def test_fresh_cache_short_circuits_network():
    from xx_phone.weather import RefreshSuccess
    from xx_weather import demo
    from xx_weather.worker import refresh_place

    store = make_store()
    demo.seed_current(store, now_ms=1700000000000)

    def _fail(url, timeout_ms):
        raise AssertionError("network used for a fresh cache")

    result = refresh_place(store, store.selected(), force=False, get=_fail,
                           clock_ms=lambda: 1700000000000 + 1000)
    assert isinstance(result, RefreshSuccess)
    assert result.from_cache is True


def test_openmeteo_fallback_when_nws_down():
    from xx_phone.weather import RefreshSuccess
    from xx_weather.worker import refresh_place

    store = _clean_store()
    body = json.dumps({
        "current": {"temperature_2m": 70.0, "relative_humidity_2m": 40,
                    "apparent_temperature": 71.0, "is_day": 1,
                    "weather_code": 2, "wind_speed_10m": 5.0,
                    "wind_direction_10m": 180, "pressure_msl": 1015.0,
                    "visibility": 20000.0, "dew_point_2m": 50.0,
                    "uv_index": 4.0},
        "current_units": {"visibility": "m"},
        "hourly": {"time": ["2026-01-15T12:00"],
                   "temperature_2m": [70.0],
                   "precipitation_probability": [10],
                   "weather_code": [2], "is_day": [1]},
        "daily": {"time": ["2026-01-15"],
                  "temperature_2m_max": [72.0],
                  "temperature_2m_min": [55.0],
                  "weather_code": [2],
                  "precipitation_probability_max": [10],
                  "sunrise": ["2026-01-15T07:00"],
                  "sunset": ["2026-01-15T17:00"]},
    })

    def _get(url, timeout_ms):
        if "weather.gov" in url:
            raise OSError("NWS down")
        return body

    result = refresh_place(store, store.selected(), force=True, get=_get,
                           clock_ms=lambda: 1700000000000)
    assert isinstance(result, RefreshSuccess)
    assert result.data.source == "Open-Meteo"
    assert result.data.current.temp_f == 70.0


def test_total_outage_reports_failure_with_stale():
    from xx_phone.weather import RefreshFailure
    from xx_weather import demo
    from xx_weather.worker import refresh_place

    store = make_store()
    demo.seed_current(store, now_ms=1000)

    def _down(url, timeout_ms):
        raise OSError("offline")

    result = refresh_place(store, store.selected(), force=True, get=_down,
                           clock_ms=lambda: 1700000000000)
    assert isinstance(result, RefreshFailure)
    assert result.stale is not None
    assert "offline" in result.message


def test_total_outage_without_cache_has_no_stale():
    from xx_phone.weather import RefreshFailure
    from xx_weather.worker import refresh_place

    store = _clean_store()

    def _down(url, timeout_ms):
        raise OSError("offline")

    result = refresh_place(store, store.selected(), force=True, get=_down,
                           clock_ms=lambda: 1700000000000)
    assert isinstance(result, RefreshFailure)
    assert result.stale is None


def test_add_place_geocodes_and_selects():
    from xx_weather.worker import add_place

    store = make_store()
    body = ('{"places": [{"place name": "Denver", '
            '"state abbreviation": "CO", "latitude": "39.7392", '
            '"longitude": "-104.9903"}]}')
    place = add_place(store, "80202", fetch=lambda url: body)
    assert place.city == "Denver"
    assert store.selected_zip() == "80202"


def test_add_place_local_zip_uses_no_network():
    from xx_weather.worker import add_place

    store = make_store()

    def _fail(url):
        raise AssertionError("network used for a local ZIP")

    place = add_place(store, "00802", fetch=_fail)
    assert place.state == "VI"


def test_add_place_rejects_bad_zip_without_fetch():
    from xx_weather.worker import add_place

    store = make_store()

    def _fail(url):
        raise AssertionError("network used for a bad ZIP")

    with pytest.raises(ValueError):
        add_place(store, "12", fetch=_fail)
    assert store.places() == []


def test_offline_env_reports_failure(monkeypatch):
    from xx_phone.weather import RefreshFailure
    from xx_weather import demo
    from xx_weather.worker import refresh_place

    monkeypatch.setenv("XX_WEATHER_OFFLINE", "1")
    store = make_store()
    demo.seed_current(store, now_ms=1000)
    result = refresh_place(store, store.selected(), force=True,
                           clock_ms=lambda: 1700000000000)
    assert isinstance(result, RefreshFailure)
    assert result.stale is not None


# --- notify ---------------------------------------------------------------

def _fired_store():
    from xx_phone.weather import ZipAlertTriggers
    from xx_weather import demo

    store = make_store()
    demo.seed_current(store)
    store.set_alerts_enabled(True)
    store.set_triggers("80202", ZipAlertTriggers(
        precip_enabled=True, precip_hours=6,
        temp_enabled=True, temp_at_or_below_f=100))
    return store


def test_pending_fires_when_enabled():
    from xx_weather.notify import pending_fires

    store = _fired_store()
    data = store.load_cached(store.selected())
    fires = pending_fires(store, data, 1700000000000)
    assert {f.kind.value for f in fires} == {"PRECIP", "TEMP"}


def test_no_fires_when_alerts_disabled():
    from xx_weather.notify import pending_fires

    store = _fired_store()
    store.set_alerts_enabled(False)
    data = store.load_cached(store.selected())
    assert pending_fires(store, data, 1700000000000) == []


def test_recorded_fires_do_not_refire():
    from xx_weather.notify import pending_fires, record_fires

    store = _fired_store()
    data = store.load_cached(store.selected())
    first = pending_fires(store, data, 1700000000000)
    assert first
    record_fires(store, first)
    assert pending_fires(store, data, 1700000000000) == []


def test_send_fire_posts_notification():
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    from xx_phone.weather import AlertFire, AlertKind
    from xx_weather.notify import send_fire

    posted = []

    class _FakeApp:
        def send_notification(self, key, notification):
            posted.append((key, notification))

    fire = AlertFire(AlertKind.TEMP, "80202", 1, "Temperature alert", "cold")
    send_fire(_FakeApp(), fire)
    assert len(posted) == 1
    assert posted[0][0] == "80202|TEMP|1"
    assert isinstance(posted[0][1], Gio.Notification)
