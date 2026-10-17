"""Port of the Android xx-weather JVM suites: LogicTests, FmtHiloTest,
ZipGeocoderTest, PlacesCodecTest, AlertEvaluatorTest (+ZipAlertCodecTest),
RefreshUiTest and HourlyOnDateTest. Same cases, same pins."""

import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from xx_phone import weather as w  # noqa: E402
from xx_phone.weather import (  # noqa: E402
    AlertKind,
    Condition,
    CurrentConditions,
    HourlyPoint,
    Place,
    RefreshFailure,
    RefreshSuccess,
    Units,
    WeatherData,
    ZipAlertTriggers,
    ZipRefreshState,
)

UTC = timezone.utc
DENVER = Place(zip="80202", city="Denver", state="CO",
               lat=39.7392, lon=-104.9903)


def _sample(zip_code="00802"):
    return WeatherData(
        place=Place(zip_code, "Charlotte Amalie", "VI", 18.34, -64.93),
        updated_at_ms=1,
        current=CurrentConditions(
            temp_f=84.0, feels_like_f=90.0,
            condition=Condition.PARTLY_CLOUDY, condition_text="Partly Cloudy",
            humidity_pct=70, wind_mph=10.0, wind_dir_deg=90,
            pressure_inhg=30.0, visibility_mi=10.0, uv_index=8.0,
            dewpoint_f=72.0, pop_pct=20),
        hourly=(), daily=(), sun=None, source="test")


# --- SunCalc (LogicTests.SunCalcTest) ---------------------------------------

def test_denver_solstice_sun_times_plausible():
    times = w.suncalc_utc(date(2026, 6, 21), 39.7392, -104.9903)
    assert times is not None
    rise = datetime.fromtimestamp(times[0] / 1000, tz=UTC)
    settled = datetime.fromtimestamp(times[1] / 1000, tz=UTC)
    assert 10 <= rise.hour <= 13, rise
    set_hour = settled.hour + 24 if settled.day == 22 else settled.hour
    assert 24 <= set_hour <= 28, settled


def test_polar_night_returns_none():
    assert w.suncalc_utc(date(2026, 1, 10), 78.22, 15.65) is None


# --- Conditions (LogicTests.ConditionsTest + NwsIconTest) -------------------

def test_wmo_codes_map():
    assert w.wmo_condition(0) is Condition.CLEAR
    assert w.wmo_condition(3) is Condition.OVERCAST
    assert w.wmo_condition(45) is Condition.FOG
    assert w.wmo_condition(95) is Condition.THUNDERSTORM
    assert w.wmo_condition(75) is Condition.HEAVY_SNOW


def test_nws_icon_urls_map():
    assert w.nws_icon_condition(
        "https://api.weather.gov/icons/land/day/tsra_hi,40?size=medium"
    ) is Condition.THUNDERSTORM
    assert w.nws_icon_condition(
        "https://api.weather.gov/icons/land/night/sct"
    ) is Condition.PARTLY_CLOUDY


def test_nws_text_fallback_maps():
    assert w.nws_text_condition("Mostly Sunny") is Condition.MOSTLY_CLEAR
    assert w.nws_text_condition(
        "Slight Chance Rain Showers") is Condition.SHOWERS
    assert w.nws_text_condition("Rain And Snow likely") is Condition.SLEET


def test_dual_condition_icon_resolves_severe_token():
    assert w.nws_icon_condition(
        "https://api.weather.gov/icons/land/day/sct/tsra_hi,40?size=medium"
    ) is Condition.THUNDERSTORM
    assert w.nws_icon_condition(
        "https://api.weather.gov/icons/land/day/rain_showers_hi,50"
    ) is Condition.SHOWERS
    assert w.nws_icon_condition(
        "https://api.weather.gov/icons/land/night/sct/tsra_hi,40"
    ) is Condition.THUNDERSTORM
    assert w.nws_icon_condition(
        "https://api.weather.gov/icons/land/day/wind_skc"
    ) is Condition.CLEAR
    assert w.nws_icon_condition(
        "https://api.weather.gov/icons/land/day/wind_sct"
    ) is Condition.PARTLY_CLOUDY


# --- Fmt (LogicTests.FmtTest + FmtHiloTest) ----------------------------------

def test_wind_cardinal_directions():
    assert w.wind_dir(0) == "N"
    assert w.wind_dir(350) == "N"
    assert w.wind_dir(90) == "E"
    assert w.wind_dir(225) == "SW"
    assert w.wind_dir(337) == "NNW"


def test_temperature_conversion():
    assert round(w.convert_temp(32.0, Units.CELSIUS)) == 0
    assert round(w.convert_temp(212.0, Units.CELSIUS)) == 100
    assert w.convert_temp(212.0, Units.FAHRENHEIT) == 212.0


def test_uv_labels():
    assert w.uv_label(1.5) == "Low"
    assert w.uv_label(5.0) == "Moderate"
    assert w.uv_label(10.0) == "Very High"
    assert w.uv_label(11.5) == "Extreme"


def test_hilo_missing_high_omitted():
    assert w.hilo(None, 61.0, Units.FAHRENHEIT) == "L:61°"


def test_hilo_both_bounds_render():
    assert w.hilo(88.0, 61.0, Units.FAHRENHEIT) == "H:88° L:61°"


def test_hilo_both_missing_renders_empty():
    assert w.hilo(None, None, Units.FAHRENHEIT) == ""


def test_hilo_celsius_converts():
    assert w.hilo(88.0, None, Units.CELSIUS) == "H:31°"


# --- ZipGeocoder (ZipGeocoderTest) -------------------------------------------

def test_virgin_islands_resolve_locally():
    st_thomas = w.local_place("00802")
    assert st_thomas is not None
    assert st_thomas.city == "Charlotte Amalie"
    assert st_thomas.state == "VI"
    assert 18.0 <= st_thomas.lat <= 19.0
    assert -65.5 <= st_thomas.lon <= -64.0
    assert w.local_place("00830").city == "Cruz Bay"
    assert w.local_place("00820").city == "Christiansted"


def test_country_order_per_zip():
    assert w.countries_for("00802") == ["vi", "us"]
    assert w.countries_for("80202") == ["us"]
    assert w.countries_for("00901") == ["pr", "us"]


def test_normalize_accepts_padded_five_digit():
    assert w.normalize_zip("00802") == "00802"
    assert w.normalize_zip(" 00802 ") == "00802"


def test_normalize_rejects_short_codes():
    try:
        w.normalize_zip("802")
    except ValueError:
        return
    raise AssertionError("short ZIP accepted")


def test_geocode_uses_local_table_without_fetch():
    def _fail(url):
        raise AssertionError("network used for a local ZIP")
    assert w.geocode_zip("00802", _fail).city == "Charlotte Amalie"


def test_geocode_parses_zippopotam_body():
    body = ('{"places": [{"place name": "Denver", '
            '"state abbreviation": "CO", "latitude": "39.7392", '
            '"longitude": "-104.9903"}]}')
    seen = []

    def _fetch(url):
        seen.append(url)
        return body
    place = w.geocode_zip("80202", _fetch)
    assert seen == ["https://api.zippopotam.us/us/80202"]
    assert (place.city, place.state) == ("Denver", "CO")


# --- PlacesCodec (PlacesCodecTest) -------------------------------------------

def test_places_round_trip_preserves_order_and_fields():
    denver = DENVER
    austin = Place("78701", "Austin", "TX", 30.2672, -97.7431)
    decoded = w.decode_places(w.encode_places([denver, austin]))
    assert decoded == [denver, austin]


def test_places_blank_and_invalid_payloads_empty():
    assert w.decode_places(None) == []
    assert w.decode_places("") == []
    assert w.decode_places("not-json") == []
    assert w.decode_places('[{"zip": "12", "lat": 1, "lon": 2}]') == []


# --- AlertEvaluator (AlertEvaluatorTest + ZipAlertCodecTest) ------------------

_NOW = datetime(2026, 1, 15, 12, 0)
_NOW_MS = int(_NOW.replace(tzinfo=UTC).timestamp() * 1000)
_WINDOW = 1700000000000


def _hour(offset, pop, temp=40.0, cond=Condition.CLOUDY):
    from datetime import timedelta
    return HourlyPoint(_NOW + timedelta(hours=offset), temp, pop, cond, True)


def _eval(hourly, temp_f=40.0, triggers=None, notified=frozenset(),
          fetched_at=_WINDOW):
    return w.evaluate_alerts(
        zip_code="80202", city="Denver", hourly=hourly,
        current_temp_f=temp_f, fetched_at_ms=fetched_at, now_ms=_NOW_MS,
        triggers=triggers or ZipAlertTriggers(),
        already_notified=set(notified), zone=UTC)


def test_precip_pop_above_threshold_fires():
    hourly = [_hour(h, 70 if h == 2 else 10) for h in range(12)]
    fires = _eval(hourly, triggers=ZipAlertTriggers(
        precip_enabled=True, precip_hours=6))
    assert len(fires) == 1
    assert fires[0].kind is AlertKind.PRECIP
    assert fires[0].zip == "80202"
    assert fires[0].window == _WINDOW
    assert fires[0].key == f"80202|PRECIP|{_WINDOW}"
    assert "6 hours" in fires[0].text
    assert "70%" in fires[0].text


def test_low_pop_does_not_fire():
    hourly = [_hour(h, 20) for h in range(12)]
    assert _eval(hourly, triggers=ZipAlertTriggers(
        precip_enabled=True, precip_hours=6)) == []


def test_pop_outside_window_does_not_fire():
    hourly = [_hour(h, 90 if h == 8 else 10) for h in range(12)]
    assert _eval(hourly, triggers=ZipAlertTriggers(
        precip_enabled=True, precip_hours=6)) == []


def test_pop_of_exactly_50_fires():
    hourly = [_hour(h, 50 if h == 0 else 0) for h in range(6)]
    assert len(_eval(hourly, triggers=ZipAlertTriggers(
        precip_enabled=True, precip_hours=6))) == 1


def test_rain_condition_with_null_pop_fires():
    hourly = [_hour(0, None, cond=Condition.RAIN),
              _hour(1, None, cond=Condition.CLOUDY)]
    fires = _eval(hourly, triggers=ZipAlertTriggers(precip_enabled=True,
                                                    precip_hours=6))
    assert len(fires) == 1
    assert fires[0].kind is AlertKind.PRECIP


def test_empty_hourly_no_precip():
    assert _eval([], triggers=ZipAlertTriggers(
        precip_enabled=True)) == []


def test_precip_disabled_ignores_high_pop():
    hourly = [_hour(h, 90) for h in range(6)]
    assert _eval(hourly, triggers=ZipAlertTriggers()) == []


def test_temp_at_or_below_threshold_fires():
    fires = _eval([], temp_f=28.0, triggers=ZipAlertTriggers(
        temp_enabled=True, temp_at_or_below_f=32))
    assert len(fires) == 1
    assert fires[0].kind is AlertKind.TEMP
    assert "28°F" in fires[0].text
    assert "32°F" in fires[0].text


def test_temp_exactly_at_threshold_fires():
    assert len(_eval([], temp_f=32.0, triggers=ZipAlertTriggers(
        temp_enabled=True, temp_at_or_below_f=32))) == 1


def test_temp_above_threshold_silent():
    assert _eval([], temp_f=33.0, triggers=ZipAlertTriggers(
        temp_enabled=True, temp_at_or_below_f=32)) == []


def test_temp_disabled_ignores_freeze():
    assert _eval([], temp_f=10.0, triggers=ZipAlertTriggers(
        temp_enabled=False)) == []


def test_both_triggers_fire_in_one_window():
    hourly = [_hour(h, 80) for h in range(6)]
    fires = _eval(hourly, temp_f=20.0, triggers=ZipAlertTriggers(
        precip_enabled=True, precip_hours=6,
        temp_enabled=True, temp_at_or_below_f=32))
    assert {f.kind for f in fires} == {AlertKind.PRECIP, AlertKind.TEMP}
    assert len(fires) == 2


def test_dedupe_suppresses_same_window():
    hourly = [_hour(h, 80) for h in range(6)]
    key = w.dedupe_key("80202", AlertKind.PRECIP, _WINDOW)
    fires = _eval(hourly, temp_f=20.0,
                  triggers=ZipAlertTriggers(precip_enabled=True,
                                            temp_enabled=True,
                                            temp_at_or_below_f=32),
                  notified={key})
    assert len(fires) == 1
    assert fires[0].kind is AlertKind.TEMP


def test_new_fetch_window_notifies_again():
    hourly = [_hour(h, 80) for h in range(6)]
    old = w.dedupe_key("80202", AlertKind.PRECIP, _WINDOW)
    fires = _eval(hourly, triggers=ZipAlertTriggers(precip_enabled=True),
                  notified={old}, fetched_at=_WINDOW + 1)
    assert len(fires) == 1
    assert fires[0].window == _WINDOW + 1
    assert fires[0].key != old


def test_remember_keys_drops_oldest_past_limit():
    kept = w.remember_keys(["a", "b"], ["c", "d"], limit=3)
    assert set(kept) == {"b", "c", "d"}
    assert kept == ["b", "c", "d"]


def test_default_precip_hours_is_6():
    assert w.DEFAULT_PRECIP_HOURS == 6
    assert ZipAlertTriggers().precip_hours == 6


def test_trigger_codec_round_trip():
    original = {
        "80202": ZipAlertTriggers(True, 6, False, 32),
        "05751": ZipAlertTriggers(False, 3, True, 20),
    }
    assert w.decode_triggers(w.encode_triggers(original)) == original


def test_trigger_codec_clamps_and_skips_bad_zips():
    decoded = w.decode_triggers(
        '{"80202": {"precip": true, "hours": 99, "temp": true, "tempF": 999},'
        ' "12": {"precip": true}}')
    assert set(decoded) == {"80202"}
    assert decoded["80202"].precip_hours == 24
    assert decoded["80202"].temp_at_or_below_f == 140
    assert w.decode_triggers("nope") == {}


# --- RefreshUi (RefreshUiTest) ------------------------------------------------

def test_failure_without_stale_inserts_placeholder():
    state = w.apply_refresh_result(
        ZipRefreshState(), "00802", RefreshFailure("offline", None))
    assert "00802" in state.data_by_zip
    assert state.data_by_zip["00802"] is None
    assert state.errors["00802"] == "offline"
    assert w.zip_shows_spinner("00802", state.data_by_zip,
                               state.errors) is False


def test_failure_with_stale_keeps_last_good():
    sample = _sample()
    state = w.apply_refresh_result(
        ZipRefreshState(), "00802", RefreshFailure("offline", sample))
    assert state.data_by_zip["00802"] == sample
    assert state.errors["00802"] == "offline"


def test_failure_keeps_in_memory_data_without_disk_stale():
    sample = _sample()
    start = w.apply_refresh_result(
        ZipRefreshState(), "00802", RefreshSuccess(sample, False))
    state = w.apply_refresh_result(
        start, "00802", RefreshFailure("offline", None))
    assert state.data_by_zip["00802"] == sample
    assert state.errors["00802"] == "offline"


def test_success_replaces_placeholder_and_clears_error():
    sample = _sample()
    failed = w.apply_refresh_result(
        ZipRefreshState(), "00802", RefreshFailure("offline", None))
    state = w.apply_refresh_result(
        failed, "00802", RefreshSuccess(sample, False))
    assert state.data_by_zip["00802"] == sample
    assert state.errors == {}
    assert w.zip_shows_spinner("00802", state.data_by_zip,
                               state.errors) is False


def test_missing_zip_spins_until_result():
    assert w.zip_shows_spinner("00802", {}, {}) is True


def test_start_tick_zero_does_not_fetch():
    assert w.should_refresh_on_start_tick(0) is False
    assert w.should_refresh_on_start_tick(1) is True


# --- HourlyOnDate (HourlyOnDateTest) ------------------------------------------

def test_hourly_on_keeps_only_tapped_date():
    day = date(2026, 8, 26)
    nxt = date(2026, 8, 27)
    base = _sample()
    data = WeatherData(
        place=base.place, updated_at_ms=1, current=base.current,
        hourly=(
            HourlyPoint(datetime(2026, 8, 26, 9, 0), 82.0, 10,
                        Condition.CLEAR, True),
            HourlyPoint(datetime(2026, 8, 26, 15, 0), 86.0, 20,
                        Condition.PARTLY_CLOUDY, True),
            HourlyPoint(datetime(2026, 8, 27, 9, 0), 80.0, 40,
                        Condition.SHOWERS, True),
        ),
        daily=(), sun=None, source="test")
    assert len(data.hourly_on(day)) == 2
    assert len(data.hourly_on(nxt)) == 1
    assert data.hourly_on(day)[1].time.hour == 15


def test_widget_short_text_matches_shell_line():
    assert w.wmo_text(0) == "Clear"
    assert w.wmo_text(2) == "Partly cloudy"
    assert w.wmo_text(95) == "Storm"
    assert w.wmo_text(-1) == ""
    assert w.widget_format_current(17.6, 0) == "18°F Clear"
    assert w.widget_format_current(-3.4, 71) == "-3°F Snow"
