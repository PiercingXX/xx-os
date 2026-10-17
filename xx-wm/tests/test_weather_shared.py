"""xx-weather sharing: the home widget uses common/xx_phone/weather, the
widget tap prefers xx-weather, and the first-boot layout seats it."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'common'))

import weather  # noqa: E402
from default_layout import DefaultLayoutResolver  # noqa: E402
from home_page import HomePage  # noqa: E402


def test_shared_short_text_matches_local_table():
    for code in list(range(-2, 110)) + [135, 999]:
        assert weather.describe(code) == weather._local_describe(code)


def test_shared_line_format_matches_local():
    for temp, code, unit in ((17.6, 0, 'fahrenheit'), (-3.4, 71, 'celsius'),
                             (22.0, -1, 'fahrenheit'), (30.5, 95, 'celsius')):
        assert weather.format_current(temp, code, unit) == \
            weather._local_format_current(temp, code, unit)


def test_http_fetch_parses_shared_shape(monkeypatch):
    payload = {"current": {"temperature_2m": 17.6, "weather_code": 2}}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode('utf-8')

    monkeypatch.setattr(weather.urllib.request, 'urlopen',
                        lambda url, timeout: _FakeResponse())
    assert weather._http_fetch(52.5, 13.4, 'fahrenheit') == {
        'temp': 17.6, 'code': 2}


def _page_with(entries):
    launched = []
    searched = []
    win = SimpleNamespace(
        app_index=SimpleNamespace(entries=list(entries)),
        _launch_entry=launched.append,
        _open_web_search=searched.append,
    )
    return HomePage(win), launched, searched


def _entry(app_id, name):
    return SimpleNamespace(app_id=app_id, name=name)


def test_tap_prefers_xx_weather():
    page, launched, _ = _page_with([
        _entry('org.gnome.Weather.desktop', 'Weather'),
        _entry('io.piercingxx.XXWeather.desktop', 'xx-weather'),
    ])
    page._open_weather_app()
    assert [e.app_id for e in launched] == ['io.piercingxx.XXWeather.desktop']


def test_tap_falls_back_to_gnome_weather():
    page, launched, _ = _page_with([
        _entry('org.gnome.Weather.desktop', 'Weather'),
    ])
    page._open_weather_app()
    assert [e.app_id for e in launched] == ['org.gnome.Weather.desktop']


def test_tap_falls_back_to_web_search():
    page, launched, searched = _page_with([])
    page._open_weather_app()
    assert launched == [] and searched == ['weather']


def test_layout_seats_xx_weather_in_tools():
    resolver = DefaultLayoutResolver(
        find_desktop=lambda x: x if x == 'io.piercingxx.XXWeather' else None,
        find_cmd=lambda x: False,
        find_browser=lambda: None,
        find_by_name=lambda name: None,
    )
    tools = resolver.resolve_tools_folder()
    assert [m['label'] for m in tools['folder']] == ['Weather']
    assert tools['folder'][0]['app_id'] == 'io.piercingxx.XXWeather'


def test_layout_falls_back_to_gnome_weather():
    resolver = DefaultLayoutResolver(
        find_desktop=lambda x: x if x == 'org.gnome.Weather' else None,
        find_cmd=lambda x: False,
        find_browser=lambda: None,
        find_by_name=lambda name: None,
    )
    tools = resolver.resolve_tools_folder()
    assert [m['label'] for m in tools['folder']] == ['Weather']
