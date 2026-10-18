"""Lane lp_info: weather unit, auto location, clock 12/24h, weather tap,
mute-clears-notifications, and home screen-reader labels.

Pure-function tests run headless; widget paths use recording fakes or
source assertions (the test_app_item_actions / test_w1_notifications idiom),
plus a Broadway probe for the real Gtk.Accessible call shape.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from config import ShellConfig  # noqa: E402


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    return ShellConfig()


class TestInfoConfigKeys:
    def test_defaults(self, config):
        assert config.data['weather_temp_unit'] == 'fahrenheit'
        assert config.data['clock_24h'] is True
        assert config.data['weather_auto_location'] is True
        assert config.weather_temp_unit == 'fahrenheit'
        assert config.clock_24h is True
        assert config.weather_auto_location is True

    def test_get_passthrough(self, config):
        assert config.get('weather_temp_unit', 'celsius') == 'fahrenheit'
        assert config.get('missing_key', 'fallback') == 'fallback'

    def test_weather_temp_unit_validation(self, config):
        config.set_weather_temp_unit('celsius')
        assert config.weather_temp_unit == 'celsius'
        config.set_weather_temp_unit('kelvin')
        assert config.weather_temp_unit == 'celsius'
        config.data['weather_temp_unit'] = 'kelvin'
        assert config.weather_temp_unit == 'fahrenheit'

    def test_clock_24h_validation(self, config):
        config.set_clock_24h(True)
        assert config.clock_24h is True
        config.set_clock_24h(False)
        assert config.clock_24h is False
        config.set_clock_24h(None)
        assert config.clock_24h is None
        config.set_clock_24h('yes')
        assert config.clock_24h is None
        config.data['clock_24h'] = 'yes'
        assert config.clock_24h is None

    def test_weather_auto_location_validation(self, config):
        config.set_weather_auto_location(False)
        assert config.weather_auto_location is False
        config.data['weather_auto_location'] = 'yes'
        assert config.weather_auto_location is True

    def test_muted_until(self, config):
        now = 1_000_000.0
        assert config.muted_until('org.example.Chat', now=now) is None
        config.set_app_muted('org.example.Chat.desktop', now + 3600, now=now)
        assert config.muted_until('org.example.Chat', now=now) == now + 3600
        assert config.muted_until('org.example.Chat.desktop',
                                  now=now) == now + 3600
        assert config.muted_until('org.example.Chat',
                                  now=now + 3601) is None


class TestInfoBackup:
    def test_export_covers_info_keys(self, config):
        from backup import export_backup
        config.set_weather_temp_unit('celsius')
        config.set_clock_24h(True)
        config.set_weather_auto_location(False)
        backup = export_backup(config)
        assert backup['weather_temp_unit'] == 'celsius'
        assert backup['clock_24h'] is True
        assert backup['weather_auto_location'] is False

    def test_round_trip(self, config, tmp_path, monkeypatch):
        import copy
        from backup import export_backup, restore_backup
        config.set_weather_temp_unit('celsius')
        config.set_clock_24h(False)
        backup = export_backup(config)
        monkeypatch.setenv('HOME', str(tmp_path / 'home2'))
        fresh = ShellConfig()
        assert restore_backup(fresh, copy.deepcopy(backup))
        assert fresh.weather_temp_unit == 'celsius'
        assert fresh.clock_24h is False
        assert export_backup(fresh) == backup

    @pytest.mark.parametrize('mutate', [
        lambda p: p.update(weather_temp_unit='kelvin'),
        lambda p: p.update(clock_24h='yes'),
        lambda p: p.update(weather_auto_location='yes'),
    ])
    def test_malformed_info_keys_rejected(self, config, tmp_path,
                                          monkeypatch, mutate):
        import copy
        from backup import export_backup, restore_backup, validate_backup
        backup = export_backup(config)
        mutate(backup)
        ok, _error = validate_backup(backup)
        assert not ok
        monkeypatch.setenv('HOME', str(tmp_path / 'home2'))
        fresh = ShellConfig()
        before = copy.deepcopy(fresh.data)
        assert not restore_backup(fresh, backup)
        assert fresh.data == before


def _weather_provider(config, tmp_path, now, fetch_result=None,
                      fix=None, fail=False):
    from weather import WeatherProvider
    calls = []

    def fetcher(lat, lon, unit):
        calls.append((lat, lon, unit))
        if fail:
            raise OSError('offline')
        return dict(fetch_result)

    fixes = []

    def location_provider():
        fixes.append(True)
        return fix

    state = {'now': now}
    provider = WeatherProvider(
        config,
        cache_path=tmp_path / 'weather.json',
        location_cache_path=tmp_path / 'weather-location.json',
        fetcher=fetcher,
        location_provider=location_provider,
        clock=lambda: state['now'],
        dispatch=lambda fn: fn(),
    )
    provider._test_state = state
    provider._test_calls = calls
    provider._test_fixes = fixes
    return provider


def _refresh_sync(provider, force=False):
    results = []
    provider.refresh(results.append, force=force)
    for _ in range(400):
        if results:
            break
        import time
        time.sleep(0.01)
    assert results, 'refresh never delivered'
    return results[0]


def _manual_config(config):
    config.data['weather_lat'] = 52.52
    config.data['weather_lon'] = 13.4
    return config


class TestWeatherTempUnit:
    def test_format_fahrenheit(self):
        from weather import format_current
        assert format_current(72.0, 0, 'fahrenheit') == '72°F Clear'
        assert format_current(22.0, 0, 'celsius') == '22°C Clear'
        assert format_current(20.0, 42, 'celsius') == '20°C'

    def test_unit_passed_to_open_meteo(self, config, tmp_path):
        from weather import _http_fetch
        import urllib.request

        seen = {}

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return b'{"current": {"temperature_2m": 22.5}}'

        def _fake_urlopen(url, timeout):
            seen['url'] = url
            return _FakeResponse()

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(urllib.request, 'urlopen', _fake_urlopen)
        try:
            assert _http_fetch(1.0, 2.0, 'celsius') == {
                'temp': 22.5, 'code': -1}
        finally:
            monkeypatch.undo()
        assert 'temperature_unit=celsius' in seen['url']

    def test_fetcher_receives_unit_and_cache_stores_it(
            self, config, tmp_path):
        _manual_config(config)
        config.set_weather_temp_unit('celsius')
        provider = _weather_provider(
            config, tmp_path, now=1000.0,
            fetch_result={'temp': 22.5, 'code': 0})
        assert _refresh_sync(provider) == '22°C Clear'
        assert provider._test_calls == [(52.52, 13.4, 'celsius')]
        assert provider.current_text() == '22°C Clear'

    def test_cache_keyed_by_unit(self, config, tmp_path):
        _manual_config(config)
        provider = _weather_provider(
            config, tmp_path, now=1000.0,
            fetch_result={'temp': 72.0, 'code': 0})
        assert _refresh_sync(provider) == '72°F Clear'
        provider._test_state['now'] = 1000.0 + 60
        assert _refresh_sync(provider) == '72°F Clear'
        assert len(provider._test_calls) == 1
        config.set_weather_temp_unit('celsius')

        def _celsius_fetch(lat, lon, unit):
            provider._test_calls.append((lat, lon, unit))
            return {'temp': 22.0, 'code': 0}

        provider._fetch = _celsius_fetch
        assert _refresh_sync(provider) == '22°C Clear'
        assert provider._test_calls[-1][2] == 'celsius'

    def test_legacy_cache_without_unit_reads_celsius(
            self, config, tmp_path):
        import json
        _manual_config(config)
        (tmp_path / 'weather.json').write_text(json.dumps({
            'temp_c': 22.0, 'code': 0, 'fetched_at': 1000.0}))
        from weather import WeatherProvider
        provider = WeatherProvider(
            config, cache_path=tmp_path / 'weather.json',
            clock=lambda: 1000.0)
        assert provider.current_text() == '22°C Clear'


class TestWeatherAutoLocation:
    def test_manual_coordinates_win_over_fix(self, config, tmp_path):
        _manual_config(config)
        provider = _weather_provider(
            config, tmp_path, now=1000.0,
            fetch_result={'temp': 70.0, 'code': 0}, fix=(1.0, 2.0))
        assert provider.coordinates() == (52.52, 13.4)

    def test_cached_fix_used_when_fresh(self, config, tmp_path):
        import json
        (tmp_path / 'weather-location.json').write_text(json.dumps({
            'lat': 48.85, 'lon': 2.35, 'fetched_at': 1000.0}))
        provider = _weather_provider(
            config, tmp_path, now=1000.0 + 10 * 60,
            fetch_result={'temp': 70.0, 'code': 0})
        assert provider.coordinates() == (48.85, 2.35)
        assert _refresh_sync(provider) == '70°F Clear'
        assert provider._test_fixes == []
        assert provider._test_calls == [(48.85, 2.35, 'fahrenheit')]

    def test_stale_fix_refetched_at_most_every_30_min(
            self, config, tmp_path):
        provider = _weather_provider(
            config, tmp_path, now=1000.0,
            fetch_result={'temp': 70.0, 'code': 0}, fix=(48.85, 2.35))
        assert _refresh_sync(provider) == '70°F Clear'
        assert len(provider._test_fixes) == 1
        provider._test_state['now'] = 1000.0 + 10 * 60
        assert _refresh_sync(provider) == '70°F Clear'
        assert len(provider._test_fixes) == 1
        assert len(provider._test_calls) == 1
        provider._test_state['now'] = 1000.0 + 31 * 60
        provider._fetch = lambda lat, lon, unit: {'temp': 71.0, 'code': 0}
        assert _refresh_sync(provider) == '71°F Clear'
        assert len(provider._test_fixes) == 2

    def test_force_refetches_weather_not_fix(self, config, tmp_path):
        provider = _weather_provider(
            config, tmp_path, now=1000.0,
            fetch_result={'temp': 70.0, 'code': 0}, fix=(48.85, 2.35))
        assert _refresh_sync(provider) == '70°F Clear'
        provider._test_state['now'] = 1000.0 + 60
        assert _refresh_sync(provider, force=True) == '70°F Clear'
        assert len(provider._test_calls) == 2
        assert len(provider._test_fixes) == 1

    def test_no_fix_hides_line(self, config, tmp_path):
        from weather import PLACEHOLDER
        provider = _weather_provider(
            config, tmp_path, now=1000.0,
            fetch_result={'temp': 70.0, 'code': 0}, fix=None)
        assert provider.coordinates() is None
        assert _refresh_sync(provider) == PLACEHOLDER
        assert provider._test_calls == []

    def test_auto_location_off_hides_line(self, config, tmp_path):
        from weather import PLACEHOLDER
        config.set_weather_auto_location(False)
        provider = _weather_provider(
            config, tmp_path, now=1000.0,
            fetch_result={'temp': 70.0, 'code': 0}, fix=(48.85, 2.35))
        assert provider.coordinates() is None
        assert _refresh_sync(provider) == PLACEHOLDER
        assert provider._test_fixes == []

    def test_geoclue_fetch_fails_soft_without_bus(self):
        from weather import _geoclue_fix_sync
        assert _geoclue_fix_sync(
            bus_factory=lambda: (_ for _ in ()).throw(OSError('no bus'))
        ) is None

    def test_geoclue_fetch_parses_fix(self):
        from weather import _geoclue_fix_sync

        class _Reply:
            def __init__(self, value):
                self._value = value

            def unpack(self):
                return (self._value,)

        calls = []

        class _Bus:
            def call_sync(self, *args):
                calls.append(args[3])
                if args[3] == 'GetClient':
                    return _Reply('/org/freedesktop/GeoClue2/Client/3')
                if args[3] == 'Get':
                    return _Reply('/org/freedesktop/GeoClue2/Location/0')
                if args[3] == 'GetAll':
                    return _Reply({'Latitude': 48.85, 'Longitude': 2.35})
                return _Reply(None)

        assert _geoclue_fix_sync(bus_factory=_Bus) == (48.85, 2.35)
        for name in ('GetClient', 'Start', 'Get', 'GetAll', 'Stop'):
            assert name in calls


class TestClockFormat:
    def test_format_24h(self):
        from clock_format import format_clock
        from datetime import datetime
        assert format_clock(datetime(2026, 1, 2, 9, 5), True) == '09:05'
        assert format_clock(datetime(2026, 1, 2, 23, 5), True) == '23:05'
        assert format_clock(datetime(2026, 1, 2, 0, 0), True) == '00:00'

    def test_format_12h_has_no_ampm(self):
        from clock_format import format_clock
        from datetime import datetime
        assert format_clock(datetime(2026, 1, 2, 9, 5), False) == '9:05'
        assert format_clock(datetime(2026, 1, 2, 0, 0), False) == '12:00'
        assert format_clock(datetime(2026, 1, 2, 12, 0), False) == '12:00'
        assert format_clock(datetime(2026, 1, 2, 23, 5), False) == '11:05'

    def test_resolve_explicit(self, config):
        from clock_format import resolve_24h
        config.set_clock_24h(True)
        assert resolve_24h(config) is True
        config.set_clock_24h(False)
        assert resolve_24h(config) is False

    def test_resolve_null_follows_gnome(self, config, monkeypatch):
        import clock_format
        from clock_format import resolve_24h
        assert config.clock_24h is True  # 24-hour by default
        config.set_clock_24h(None)       # 'System' still follows GNOME
        assert config.clock_24h is None
        assert resolve_24h(config, gnome_24h=True) is True
        assert resolve_24h(config, gnome_24h=False) is False
        monkeypatch.setattr(clock_format, 'gnome_24h', lambda: None)
        assert resolve_24h(config) is True
        monkeypatch.setattr(clock_format, 'gnome_24h', lambda: False)
        assert resolve_24h(config) is False

    def test_live_gnome_lookup_never_raises(self):
        from clock_format import gnome_24h
        assert gnome_24h() in (True, False, None)

    def test_clocks_use_resolver(self):
        import pathlib
        home_src = pathlib.Path(__file__).parent.parent.joinpath(
            'launcher', 'src', 'home_page.py').read_text(encoding='utf-8')
        lock_src = pathlib.Path(__file__).parent.parent.joinpath(
            'launcher', 'src', 'lock_screen.py').read_text(encoding='utf-8')
        assert 'resolve_24h' in home_src
        assert 'resolve_24h' in lock_src


def _home_page(win):
    from home_page import HomePage
    return HomePage(win)


def _tap_win(config, entries=(), refreshed=None, launched=None,
             searched=None):
    win = SimpleNamespace(
        config=config,
        app_index=SimpleNamespace(entries=list(entries)),
        _refresh_weather=(lambda force=False: refreshed.append(force)
                          if refreshed is not None else None),
        _launch_entry=(lambda entry: launched.append(entry)
                       if launched is not None else None),
        _open_web_search=(lambda query: searched.append(query)
                          if searched is not None else None),
    )
    return win


def _app_entry(app_id, name):
    from app_index import AppEntry
    return AppEntry(app_id=app_id, name=name, description='',
                    executable='', search_text=name.casefold(),
                    desktop_app=None)


class TestWeatherTap:
    def test_default_opens_weather_app_and_refreshes(self, config):
        refreshed, launched, searched = [], [], []
        entries = [_app_entry('org.gnome.Weather.desktop', 'Weather'),
                   _app_entry('org.gnome.Calendar.desktop', 'Calendar')]
        win = _tap_win(config, entries, refreshed, launched, searched)
        _home_page(win)._dispatch_widget_tap('weather', 'default')
        assert launched == [entries[0]]
        assert searched == []
        assert refreshed == [False]

    def test_gnome_weather_preferred_over_name_match(self, config):
        refreshed, launched, searched = [], [], []
        other = _app_entry('com.example.Forecast.desktop', 'Weather Now')
        exact = _app_entry('org.gnome.Weather.desktop', 'Weather')
        win = _tap_win(config, [other, exact], refreshed, launched, searched)
        _home_page(win)._dispatch_widget_tap('weather', 'default')
        assert launched == [exact]

    def test_name_fallback_when_no_gnome_weather(self, config):
        refreshed, launched, searched = [], [], []
        entries = [_app_entry('org.gnome.Calendar.desktop', 'Calendar'),
                   _app_entry('com.example.Storm.desktop', 'My Weather')]
        win = _tap_win(config, entries, refreshed, launched, searched)
        _home_page(win)._dispatch_widget_tap('weather', 'default')
        assert launched == [entries[1]]
        assert refreshed == [False]

    def test_web_search_fallback(self, config):
        refreshed, launched, searched = [], [], []
        entries = [_app_entry('org.gnome.Calendar.desktop', 'Calendar')]
        win = _tap_win(config, entries, refreshed, launched, searched)
        _home_page(win)._dispatch_widget_tap('weather', 'default')
        assert launched == []
        assert searched == ['weather']
        assert refreshed == [False]

    def test_refresh_tap_stays_force_only(self, config):
        refreshed, launched, searched = [], [], []
        entries = [_app_entry('org.gnome.Weather.desktop', 'Weather')]
        win = _tap_win(config, entries, refreshed, launched, searched)
        _home_page(win)._dispatch_widget_tap('weather', 'refresh')
        assert refreshed == [True]
        assert launched == [] and searched == []

    def test_weather_default_tap_is_default(self, config):
        from config import DEFAULT_CONFIG
        assert DEFAULT_CONFIG['widgets']['weather']['tap'] == 'default'


class TestMuteClearsNotifications:
    def test_mute_app_callback_fires(self, config):
        from app_item_actions import AppItemActions
        muted = []
        act = AppItemActions(
            config, on_changed=lambda: None, on_status=lambda _m: None,
            on_muted=muted.append)
        act._mute_app('chat.desktop', 'Chat', 1, '1 hour')
        assert config.is_app_muted('chat.desktop')
        assert muted == ['chat.desktop']

    def test_muted_subtitle(self, config):
        from app_item_actions import muted_menu_subtitle
        now = 1_000_000.0
        assert muted_menu_subtitle(config, 'chat.desktop', now=now) is None
        import time
        real_now = time.time()
        config.set_app_muted('chat.desktop', real_now + 3600)
        subtitle = muted_menu_subtitle(config, 'chat.desktop')
        assert subtitle is not None
        assert subtitle.startswith('Muted until ')
        assert muted_menu_subtitle(config, 'other.app') is None

    def test_app_menus_show_muted_subtitle(self):
        import pathlib
        src = pathlib.Path(__file__).parent.parent.joinpath(
            'launcher', 'src',
            'app_item_actions.py').read_text(encoding='utf-8')
        assert 'muted_menu_subtitle' in src
        assert src.count('muted_menu_subtitle(') >= 3

    def test_notifs_for_app_matching(self):
        from notification_shade import notifs_for_app
        notifs = [
            SimpleNamespace(id=1, app_name='Chat',
                            desktop_entry='chat.desktop'),
            SimpleNamespace(id=2, app_name='Mail',
                            desktop_entry='mail'),
            SimpleNamespace(id=3, app_name='chat',
                            desktop_entry=''),
        ]
        assert notifs_for_app(notifs, 'chat.desktop') == [1, 3]
        assert notifs_for_app(notifs, 'CHAT') == [1, 3]
        assert notifs_for_app(notifs, 'missing.app') == []

    def test_daemon_close_for_mute_emits_reason_3(self):
        import notif_daemon
        from gi.repository import GLib as live_glib

        emitted = []

        class _Bus:
            def emit_signal(self, *args):
                emitted.append(args)

        daemon = notif_daemon.NotificationDaemon.__new__(
            notif_daemon.NotificationDaemon)
        daemon._bus = _Bus()
        daemon._expire_timers = {}
        daemon._notif_hints = {}
        daemon._issued_ids = {7: None}
        daemon._on_close = lambda _id: None
        # Sibling suites import this module under a stub gi during
        # collection; rebind the live GLib so the payload is a real
        # Variant (the trap XXWM_RULES.md warns fakes hide).
        saved_glib, saved_idle = notif_daemon.GLib, live_glib.idle_add
        notif_daemon.GLib = live_glib
        live_glib.idle_add = lambda *a, **k: None
        try:
            daemon.close_for_mute(7)
        finally:
            notif_daemon.GLib = saved_glib
            live_glib.idle_add = saved_idle
        assert len(emitted) == 1
        _dest, _path, _iface, signal, payload = emitted[0]
        assert signal == 'NotificationClosed'
        assert payload.unpack() == (7, 3)
        assert 7 not in daemon._issued_ids

    def test_window_wires_mute_to_shade(self):
        import pathlib
        src = pathlib.Path(__file__).parent.parent.joinpath(
            'launcher', 'src', 'window.py').read_text(encoding='utf-8')
        assert 'on_muted' in src
        assert 'close_app_notifications' in src
        assert 'close_for_mute' in src


class TestHomeAccessibility:
    def test_slot_label(self):
        from home_launcher import slot_accessible_label
        assert slot_accessible_label('Phone', 2) == 'Phone, home slot 3'

    def test_folder_label(self):
        from home_launcher import folder_accessible_label
        assert folder_accessible_label('Comms', 3) == 'Folder Comms, 3 apps'
        assert folder_accessible_label('Comms', 1) == 'Folder Comms, 1 app'

    def test_home_uses_update_property(self):
        import pathlib
        src = pathlib.Path(__file__).parent.parent.joinpath(
            'launcher', 'src', 'home_launcher.py').read_text(
                encoding='utf-8')
        assert 'Gtk.Accessible.update_property' in src
        assert 'AccessibleProperty.LABEL' in src
        assert 'Open app drawer' in src
        assert 'Open launcher settings' in src

    def test_broadway_real_accessible_labels(self, tmp_path):
        import shutil
        import subprocess
        if shutil.which('gtk4-broadwayd') is None:
            pytest.skip('needs gtk4-broadwayd')
        pytest.importorskip('gi')
        script = tmp_path / 'lp_info_a11y.py'
        script.write_text(
            'import glob, os, subprocess, sys, time\n'
            'src_dir, run_dir = sys.argv[1], sys.argv[2]\n'
            "os.environ['GDK_BACKEND'] = 'broadway'\n"
            "os.environ['XDG_RUNTIME_DIR'] = run_dir\n"
            # A probed free display (a fixed :71 collided with parallel
            # runs).
            f"sys.path.insert(0, {str(Path(__file__).resolve().parents[2] / 'common')!r})\n"
            'from xx_testing import broadway_display\n'
            'daemon, display = broadway_display.start(run_dir=run_dir)\n'
            "os.environ['BROADWAY_DISPLAY'] = display\n"
            'for _i in range(200):\n'
            "    if glob.glob(os.path.join(run_dir, 'broadway*.socket')):\n"
            '        break\n'
            '    time.sleep(0.05)\n'
            'else:\n'
            "    print('NO BROADWAY SOCKET')\n"
            '    raise SystemExit(1)\n'
            'try:\n'
            '    import gi\n'
            "    gi.require_version('Gtk', '4.0')\n"
            '    from gi.repository import Gtk\n'
            '    sys.path.insert(0, src_dir)\n'
            '    import home_launcher\n'
            '    b = Gtk.Button(label="x")\n'
            '    b.update_property([Gtk.AccessibleProperty.LABEL], '
            '["Phone, home slot 3"])\n'
            '    b.update_property([Gtk.AccessibleProperty.DESCRIPTION], '
            '["Open app drawer. Open launcher settings."])\n'
            '    box = home_launcher.HomeLauncher(\n'
            '        get_slots_fn=lambda: ['
            "            {'type': 'app', 'label': 'Phone'},\n"
            "            {'type': 'folder', 'label': 'Comms', 'folder': [\n"
            "                {'label': 'a'}, {'label': 'b'}, {'label': 'c'}]}],\n"
            '        get_alignment_fn=lambda: "center")\n'
            '    count = 0\n'
            '    child = box.get_first_child()\n'
            '    while child is not None:\n'
            '        count += 1\n'
            '        child = child.get_next_sibling()\n'
            '    assert count == 2\n'
            '    print("LP INFO A11Y OK")\n'
            'finally:\n'
            '    daemon.terminate()\n')
        run_dir = tmp_path / 'broadway'
        run_dir.mkdir()
        src = str(Path(__file__).parent.parent / 'launcher' / 'src')
        proc = subprocess.run(
            [sys.executable, str(script), src, str(run_dir)],
            capture_output=True, text=True, timeout=120)
        assert 'LP INFO A11Y OK' in proc.stdout, (
            proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])


