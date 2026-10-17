"""Port of NwsSourceTest, OpenMeteoSourceTest and CacheStampTest: recorded
fixtures in fixtures/weather/ stand in for the network, exactly like the
JVM resource files."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from xx_phone import weather as w  # noqa: E402
from xx_phone.weather import NwsBodies, Place  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "weather"
DENVER = Place(zip="80202", city="Denver", state="CO",
               lat=39.7392, lon=-104.9903)
NOW = 1700000000000


def _res(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- NWS ---------------------------------------------------------------------

def test_current_pop_falls_back_to_max_of_next_twelve():
    bodies = NwsBodies(daily=_res("nws_hourly.json"),
                       hourly=_res("nws_hourly.json"),
                       obs=_res("nws_obs_msl.json"), city=None, state=None)
    data = w.assemble_nws(DENVER, bodies, NOW)
    assert data.current.pop_pct == 40


def test_evening_only_date_does_not_reuse_low_as_high():
    parsed = w.parse_nws_daily(_res("nws_daily_evening.json"))
    assert len(parsed) == 2
    tonight = parsed[0]
    assert tonight.lo_f == 61.0
    assert tonight.hi_f is None or tonight.hi_f != 61.0


def test_evening_high_backfilled_from_hourly_max():
    bodies = NwsBodies(daily=_res("nws_daily_evening.json"),
                       hourly=_res("nws_hourly.json"), obs=None,
                       city=None, state=None)
    data = w.assemble_nws(DENVER, bodies, NOW)
    assert data.daily[0].hi_f == 92.0


def test_obs_station_flag_only_without_sea_level():
    baro = w.parse_nws_obs(_res("nws_obs_baro_only.json"))
    assert baro is not None
    assert baro.pressure_station_level is True
    msl = w.parse_nws_obs(_res("nws_obs_msl.json"))
    assert msl is not None
    assert msl.pressure_station_level is False


def test_obs_prefers_sea_level_pressure():
    obs = w.parse_nws_obs(_res("nws_obs_msl.json"))
    assert obs is not None
    assert obs.pressure_inhg == 29.93 or abs(obs.pressure_inhg - 29.93) < 0.05


def test_obs_with_only_barometric_pressure_parses():
    obs = w.parse_nws_obs(_res("nws_obs_baro_only.json"))
    assert obs is not None
    assert abs(obs.pressure_inhg - 101360.0 / 3386.389) < 0.05


def test_assemble_stamps_updated_at():
    bodies = NwsBodies(daily=_res("nws_hourly.json"),
                       hourly=_res("nws_hourly.json"), obs=None,
                       city="Denver", state="CO")
    data = w.assemble_nws(DENVER, bodies, NOW)
    assert data.updated_at_ms == NOW
    assert data.place.city == "Denver"


def test_parse_openmeteo_current_payload():
    assert w.parse_openmeteo_current(
        {"current": {"temperature_2m": 17.6, "weather_code": 0}}) == {
        "temp": 17.6, "code": 0}


# --- Open-Meteo ---------------------------------------------------------------

def test_visibility_meters_to_miles():
    data = w.parse_openmeteo(DENVER, _res("om_current_m.json"), NOW)
    assert abs(data.current.visibility_mi - 35.8) < 0.05


def test_visibility_feet_to_miles():
    data = w.parse_openmeteo(DENVER, _res("om_current_ft.json"), NOW)
    assert abs(data.current.visibility_mi - 35.8) < 0.05


def test_pressure_prefers_msl():
    data = w.parse_openmeteo(DENVER, _res("om_current_m.json"), NOW)
    assert abs(data.current.pressure_inhg - 29.93) < 0.05
    assert data.current.pressure_station_level is False


def test_forecast_url_requests_msl():
    assert "pressure_msl" in w.openmeteo_forecast_url(DENVER)


def test_surface_only_pressure_flagged_station():
    data = w.parse_openmeteo(DENVER, _res("om_current_surface_only.json"),
                             NOW)
    assert abs(data.current.pressure_inhg - 846.5 * 0.02953) < 0.05
    assert data.current.pressure_station_level is True


def test_openmeteo_stamps_updated_at():
    data = w.parse_openmeteo(DENVER, _res("om_current_m.json"),
                             1700000123000)
    assert data.updated_at_ms == 1700000123000


# --- freshness (CacheStampTest.isFresh) ----------------------------------------

def test_fresh_fourteen_minutes_after_fetch():
    assert w.is_fresh(NOW, NOW + 14 * 60000) is True


def test_stale_sixteen_minutes_after_fetch():
    assert w.is_fresh(NOW, NOW + 16 * 60000) is False


def test_fresh_at_zero_elapsed():
    assert w.is_fresh(NOW, NOW) is True


def test_epoch_zero_stamp_is_stale():
    assert w.is_fresh(0, NOW) is False


def test_extras_merge_fills_only_missing_fields():
    bodies = NwsBodies(daily=_res("nws_hourly.json"),
                       hourly=_res("nws_hourly.json"),
                       obs=_res("nws_obs_msl.json"), city=None, state=None)
    data = w.assemble_nws(DENVER, bodies, NOW)
    extra = w.Extras(uv_index=5.5, visibility_mi=None, pressure_inhg=None,
                     dewpoint_f=None, sun=None)
    merged = w.merge_extras(data, extra)
    assert merged.current.uv_index == 5.5
    assert merged.current.pressure_inhg == data.current.pressure_inhg
