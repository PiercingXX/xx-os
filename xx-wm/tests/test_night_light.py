"""Night light schedules (wm_power_night lane).

NOAA solar calculation against known values, boundary math across
midnight and DST, the scheduler's next-boundary choice, manual override
until the next boundary, and the no-location fallback. No network, no
D-Bus, no display: the clock, timezone and timer are faked.
"""
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import night_light
from night_light import NightScheduler

# Page-first (see test_power_profiles.py): settings_page must win the
# import race with settings_display.
import settings_page  # noqa: F401
import settings_display  # noqa: F401

NY = ZoneInfo('America/New_York')


class FakeConfig:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class Timers:
    def __init__(self):
        self.armed = {}
        self._next = 0

    def arm(self, delay_s, callback):
        self._next += 1
        self.armed[self._next] = (delay_s, callback)
        return self._next

    def disarm(self, handle):
        self.armed.pop(handle, None)

    def only(self):
        assert len(self.armed) == 1
        return next(iter(self.armed.values()))


def local_minutes(utc_minutes, offset_min):
    return night_light.sun_local_minutes(utc_minutes, offset_min)


# ------------------------------------------------------------- solar math


class TestSolarCalculation:
    """Known almanac values, ±2 min as the task requires."""

    def test_new_york_summer_solstice(self):
        day = date(2024, 6, 21).timetuple().tm_yday
        rise, set_, kind = night_light.solar_utc(day, 40.7128, -74.0060)
        assert kind == 'normal'
        assert local_minutes(rise, -240) == pytest.approx(5 * 60 + 25, abs=2)
        assert local_minutes(set_, -240) == pytest.approx(20 * 60 + 30, abs=2)

    def test_paris_winter_solstice(self):
        day = date(2024, 12, 21).timetuple().tm_yday
        rise, set_, kind = night_light.solar_utc(day, 48.85, 2.35)
        assert kind == 'normal'
        assert local_minutes(rise, 60) == pytest.approx(8 * 60 + 41, abs=2)
        assert local_minutes(set_, 60) == pytest.approx(16 * 60 + 56, abs=2)

    def test_tokyo_equinox(self):
        day = date(2024, 3, 20).timetuple().tm_yday
        rise, set_, kind = night_light.solar_utc(day, 35.68, 139.69)
        assert kind == 'normal'
        assert local_minutes(rise, 540) == pytest.approx(5 * 60 + 45, abs=2)
        assert local_minutes(set_, 540) == pytest.approx(17 * 60 + 52, abs=2)

    def test_equator_equinox_symmetric(self):
        day = date(2024, 3, 20).timetuple().tm_yday
        rise, set_, kind = night_light.solar_utc(day, 0.0, 0.0)
        assert kind == 'normal'
        assert rise / 60.0 == pytest.approx(6.0, abs=0.5)
        assert set_ / 60.0 == pytest.approx(18.0, abs=0.5)

    def test_polar_day_and_night(self):
        summer = date(2024, 6, 21).timetuple().tm_yday
        winter = date(2024, 12, 21).timetuple().tm_yday
        assert night_light.solar_utc(summer, 78.22, 15.63)[2] == 'polar-day'
        assert night_light.solar_utc(winter, 78.22, 15.63)[2] == \
            'polar-night'

    def test_bad_input_degrades(self):
        assert night_light.solar_utc(80, None, None)[:2] == (360.0, 1080.0)
        assert night_light.solar_utc(80, 91.0, 0.0)[:2] == (360.0, 1080.0)


# ---------------------------------------------------------- boundary math


class TestBoundaryMath:
    def test_custom_overnight_window(self):
        assert night_light.in_custom(23 * 60, 22 * 60, 6 * 60) is True
        assert night_light.in_custom(5 * 60 + 59, 22 * 60, 6 * 60) is True
        assert night_light.in_custom(6 * 60, 22 * 60, 6 * 60) is False
        assert night_light.in_custom(12 * 60, 22 * 60, 6 * 60) is False
        assert night_light.in_custom(21 * 60 + 59, 22 * 60, 6 * 60) is False

    def test_custom_daytime_window(self):
        assert night_light.in_custom(9 * 60, 8 * 60, 20 * 60) is True
        assert night_light.in_custom(20 * 60, 8 * 60, 20 * 60) is False
        assert night_light.in_custom(7 * 60, 8 * 60, 20 * 60) is False

    def test_degenerate_range_never_on(self):
        assert night_light.in_custom(12 * 60, 8 * 60, 8 * 60) is False

    def test_is_night_wraps(self):
        assert night_light.is_night(22.0, 6.0, 18.0) is True
        assert night_light.is_night(12.0, 6.0, 18.0) is False
        assert night_light.is_night(5.0, 6.0, 18.0) is True

    def test_next_edge_across_midnight(self):
        now = datetime(2024, 6, 21, 23, 0, tzinfo=timezone.utc).timestamp()
        edges, constant = night_light.collect_edges(
            now, 'custom', None, None, 22 * 60, 6 * 60, timezone.utc)
        assert constant is None
        assert night_light.wanted_at(now, edges) is True
        when, state = night_light.next_edge(now, edges)
        assert state is False
        assert datetime.fromtimestamp(when, tz=timezone.utc).hour == 6

    def test_dst_spring_forward_edges(self):
        """Custom 01:30–03:30 on 2024-03-10 in New York: 02:00–03:00
        never exists, so the window lasts one wall hour, not two."""
        edges, constant = night_light.custom_edges_for_local_date(
            date(2024, 3, 10), 90, 210, NY)
        assert constant is None
        (on_at, on_state), (off_at, off_state) = sorted(edges)
        assert (on_state, off_state) == (True, False)
        assert off_at - on_at == 3600
        assert datetime.fromtimestamp(on_at, tz=NY).utcoffset() == \
            timedelta(hours=-5)
        assert datetime.fromtimestamp(off_at, tz=NY).utcoffset() == \
            timedelta(hours=-4)

    def test_dst_day_sunset_edges_stay_ordered(self):
        now = datetime(2024, 3, 10, 12, 0, tzinfo=NY).timestamp()
        edges, constant = night_light.collect_edges(
            now, 'sunset', 40.7128, -74.0060, 0, 0, NY)
        assert constant is None
        assert night_light.wanted_at(now, edges) is False
        when, state = night_light.next_edge(now, edges)
        assert state is True
        assert when > now


# -------------------------------------------------------------- scheduler


def make_scheduler(cfg_data, coords, applied, clock, timers, tz=None):
    cfg = FakeConfig(cfg_data)
    return NightScheduler(
        get_mode=lambda: night_light.mode_of(cfg),
        get_custom=lambda: night_light.custom_range(cfg),
        get_coords=lambda: coords,
        apply=applied.append,
        clock=clock, local_tz=lambda: tz or timezone.utc,
        arm=timers.arm, disarm=timers.disarm,
    )


class TestScheduler:
    def test_custom_next_boundary(self):
        applied = []
        clock = Clock(datetime(2024, 6, 21, 12, 0,
                               tzinfo=timezone.utc).timestamp())
        timers = Timers()
        sched = make_scheduler(
            {'night_light_mode': 'custom', 'night_light_from': '22:00',
             'night_light_to': '06:00'}, None, applied, clock, timers)
        assert sched.refresh('boot') is False
        assert applied == [False]
        assert timers.only()[0] == pytest.approx(10 * 3600, abs=1)
        clock.now = datetime(2024, 6, 21, 22, 0,
                             tzinfo=timezone.utc).timestamp()
        _, fire = timers.only()
        timers.armed.clear()
        fire()
        assert applied[-1] is True
        assert timers.only()[0] == pytest.approx(8 * 3600, abs=1)

    def test_sunset_next_boundary(self):
        applied = []
        clock = Clock(datetime(2024, 6, 21, 12, 0, tzinfo=NY).timestamp())
        timers = Timers()
        sched = make_scheduler(
            {'night_light_mode': 'sunset'}, (40.7128, -74.0060),
            applied, clock, timers, tz=NY)
        assert sched.refresh('boot') is False
        delay, _fire = timers.only()
        assert 7 * 3600 < delay < 10 * 3600

    def test_manual_override_holds_until_boundary(self):
        applied = []
        noon = datetime(2024, 6, 21, 12, 0, tzinfo=timezone.utc).timestamp()
        clock = Clock(noon)
        timers = Timers()
        sched = make_scheduler(
            {'night_light_mode': 'custom', 'night_light_from': '22:00',
             'night_light_to': '06:00'}, None, applied, clock, timers)
        sched.refresh('boot')
        assert sched.manual_set(True) is True
        assert applied[-1] is True
        assert timers.only()[0] == pytest.approx(10 * 3600, abs=1)
        clock.now = datetime(2024, 6, 21, 15, 0,
                             tzinfo=timezone.utc).timestamp()
        assert sched.refresh('tick') is True
        assert timers.only()[0] == pytest.approx(7 * 3600, abs=1)
        clock.now = datetime(2024, 6, 21, 22, 0,
                             tzinfo=timezone.utc).timestamp()
        _, fire = timers.only()
        timers.armed.clear()
        fire()
        assert applied[-1] is True
        clock.now = datetime(2024, 6, 21, 23, 0,
                             tzinfo=timezone.utc).timestamp()
        assert sched.refresh('tick') is True
        assert sched.manual_set(False) is False
        assert applied[-1] is False

    def test_manual_mode_applies_immediately(self):
        applied = []
        clock = Clock(datetime(2024, 6, 21, 12, 0,
                               tzinfo=timezone.utc).timestamp())
        timers = Timers()
        sched = make_scheduler({'night_light_mode': 'off'}, None,
                               applied, clock, timers)
        assert sched.refresh('boot') is None
        assert applied == []
        assert timers.armed == {}
        assert sched.manual_set(True) is True
        assert applied == [True]
        assert timers.armed == {}

    def test_polar_constant_with_backstop(self):
        applied = []
        clock = Clock(datetime(2024, 6, 21, 12, 0,
                               tzinfo=timezone.utc).timestamp())
        timers = Timers()
        sched = make_scheduler(
            {'night_light_mode': 'sunset'}, (78.22, 15.63),
            applied, clock, timers)
        assert sched.refresh('boot') is False
        assert timers.only()[0] == pytest.approx(6 * 3600, abs=1)

    def test_no_location_falls_back_to_custom(self):
        applied = []
        clock = Clock(datetime(2024, 6, 21, 23, 30,
                               tzinfo=timezone.utc).timestamp())
        timers = Timers()
        sched = make_scheduler(
            {'night_light_mode': 'sunset', 'night_light_from': '22:00',
             'night_light_to': '06:00'}, None, applied, clock, timers)
        assert sched.refresh('boot') is True
        assert applied == [True]


# -------------------------------------------------- config and shell glue


class TestConfigGlue:
    def test_mode_of(self):
        assert night_light.mode_of(FakeConfig({})) == 'off'
        assert night_light.mode_of(
            FakeConfig({'night_light_mode': 'custom'})) == 'custom'
        assert night_light.mode_of(
            FakeConfig({'night_light_mode': 'bogus',
                        'night_light_schedule': True})) == 'sunset'
        assert night_light.mode_of(
            FakeConfig({'night_light_mode': 'bogus'})) == 'off'
        assert night_light.mode_of(
            FakeConfig({'night_light_schedule': True})) == 'sunset'

    def test_custom_range_defaults(self):
        assert night_light.custom_range(FakeConfig({})) == (1320, 360)
        assert night_light.custom_range(
            FakeConfig({'night_light_from': '21:30',
                        'night_light_to': 'nope'})) == (1290, 360)

    def test_parse_format_round_trip(self):
        assert night_light.parse_hhmm('22:00') == 1320
        assert night_light.format_hhmm(1320) == '22:00'
        assert night_light.format_hhmm(6 * 60) == '06:00'
        assert night_light.parse_hhmm('24:00') is None
        assert night_light.parse_hhmm('22:60') is None
        assert night_light.parse_hhmm('') is None
        assert night_light.parse_hhmm(None) is None

    def test_no_location_text(self):
        assert night_light.NO_LOCATION_TEXT == 'Set a location in Settings'

    def test_schedule_subtitles(self):
        section = settings_display.DisplaySection()
        win = SimpleNamespace(config=FakeConfig(
            {'night_light_from': '22:00', 'night_light_to': '06:00'}))
        assert section._schedule_subtitle(win, 'off', None) == 'Off'
        assert section._schedule_subtitle(
            win, 'custom', None) == '22:00 – 06:00'
        assert section._schedule_subtitle(
            win, 'sunset', None) == night_light.NO_LOCATION_TEXT
        assert section._schedule_subtitle(
            win, 'sunset', (48.85, 2.35)) == 'Sunset to sunrise'

    def test_set_mode_persists_both_keys(self):
        section = settings_display.DisplaySection()
        section._render_night = lambda win: None
        win = SimpleNamespace(config=FakeConfig({}))
        section._set_mode(win, 'sunset')
        assert win.config.data['night_light_mode'] == 'sunset'
        assert win.config.data['night_light_schedule'] is True
        section._set_mode(win, 'bogus')
        assert win.config.data['night_light_mode'] == 'sunset'

    def test_tile_routes_through_scheduler(self, monkeypatch):
        seen = []
        fake = SimpleNamespace(manual_set=lambda on: seen.append(on))
        monkeypatch.setattr(night_light, '_scheduler', fake)
        settings_display.tile_set_night(True)
        assert seen == [True]

    def test_tile_without_scheduler_sets_directly(self, monkeypatch):
        monkeypatch.setattr(night_light, '_scheduler', None)
        calls = []
        monkeypatch.setattr(settings_display, 'set_night_light',
                            lambda *args: calls.append(args) or True)
        settings_display.tile_set_night(True)
        assert calls == [(True,)]

    def test_tile_hides_without_tool(self, monkeypatch):
        monkeypatch.setattr(settings_display, 'night_tool',
                            lambda: None)
        assert settings_display.tile_get_night() is None


class TestBackupRoundTrip:
    def test_new_keys_validate_and_restore(self, tmp_path, monkeypatch):
        import backup
        import config
        monkeypatch.setenv('HOME', str(tmp_path))
        cfg = config.ShellConfig()
        cfg.set('night_light_mode', 'custom')
        cfg.set('night_light_from', '21:30')
        cfg.set('night_light_to', '05:30')
        cfg.set('power_profile', 'performance')
        exported = backup.export_backup(cfg)
        assert exported['night_light_mode'] == 'custom'
        assert exported['power_profile'] == 'performance'
        ok, err = backup.validate_backup(dict(exported, version=1))
        assert ok, err
        assert backup.validate_backup(
            dict(exported, night_light_mode='dawn'))[0] is False
        assert backup.validate_backup(
            dict(exported, night_light_from='25:00'))[0] is False
        assert backup.validate_backup(
            dict(exported, power_profile='powersave'))[0] is False
