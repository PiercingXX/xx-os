"""Tests for weather.py — cache staleness and formatting, injected clock/fetcher."""
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from config import ShellConfig
from weather import PLACEHOLDER, WeatherProvider, describe, format_current


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    cfg = ShellConfig()
    cfg.data['weather_lat'] = 52.52
    cfg.data['weather_lon'] = 13.4
    return cfg


def _provider(config, tmp_path, now, fetch_result=None, fail=False):
    calls = []

    def fetcher(lat, lon, unit):
        calls.append((lat, lon, unit))
        if fail:
            raise OSError('offline')
        return dict(fetch_result)

    state = {'now': now}
    provider = WeatherProvider(
        config,
        cache_path=tmp_path / 'weather.json',
        fetcher=fetcher,
        location_provider=lambda: None,
        clock=lambda: state['now'],
        dispatch=lambda fn: fn(),
    )
    provider._test_state = state
    provider._test_calls = calls
    return provider


def _refresh_sync(provider, force=False):
    """Run refresh and wait for its worker thread to deliver."""
    results = []
    provider.refresh(results.append, force=force)
    for _ in range(200):
        if results:
            break
        import time
        time.sleep(0.01)
    return results[0] if results else None


class TestStaleCache:
    def test_offline_cache_older_than_three_hours_is_not_shown(
            self, config, tmp_path):
        import weather
        provider = _provider(config, tmp_path, now=1000.0,
                             fetch_result={'temp': 18.0, 'code': 0})
        assert _refresh_sync(provider) == '18°F Clear'
        provider._test_state['now'] = 1000.0 + weather.STALE_SECONDS - 60
        assert provider.current_text() == '18°F Clear'
        provider._test_state['now'] = 1000.0 + weather.STALE_SECONDS + 60
        assert provider.current_text() == PLACEHOLDER


class TestFormatting:
    def test_format(self):
        assert format_current(17.6, 0) == '18°F Clear'
        assert format_current(-3.4, 71) == '-3°F Snow'
        assert format_current(22.0, 0, 'celsius') == '22°C Clear'

    def test_unknown_code_omits_text(self):
        assert format_current(20.0, 42) == '20°F'
        assert describe(61) == 'Rain'


class TestProvider:
    def test_unset_coordinates_silent_placeholder(self, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path / 'home'))
        cfg = ShellConfig()
        provider = _provider(cfg, tmp_path, now=1000.0,
                             fetch_result={'temp': 20.0, 'code': 0})
        assert _refresh_sync(provider) == PLACEHOLDER
        assert provider._test_calls == []

    def test_fetch_stores_cache_and_formats(self, config, tmp_path):
        provider = _provider(config, tmp_path, now=1000.0,
                             fetch_result={'temp': 17.6, 'code': 0})
        assert _refresh_sync(provider) == '18°F Clear'
        assert provider._test_calls == [(52.52, 13.4, 'fahrenheit')]
        assert provider.current_text() == '18°F Clear'

    def test_fresh_cache_skips_fetch(self, config, tmp_path):
        provider = _provider(config, tmp_path, now=1000.0,
                             fetch_result={'temp': 17.6, 'code': 0})
        _refresh_sync(provider)
        provider._test_state['now'] = 1000.0 + 14 * 60
        assert _refresh_sync(provider) == '18°F Clear'
        assert len(provider._test_calls) == 1

    def test_stale_cache_refetches(self, config, tmp_path):
        provider = _provider(config, tmp_path, now=1000.0,
                             fetch_result={'temp': 17.6, 'code': 0})
        _refresh_sync(provider)
        provider._test_state['now'] = 1000.0 + 16 * 60
        _refresh_sync(provider)
        assert len(provider._test_calls) == 2

    def test_force_refetches_even_when_fresh(self, config, tmp_path):
        provider = _provider(config, tmp_path, now=1000.0,
                             fetch_result={'temp': 17.6, 'code': 0})
        _refresh_sync(provider)
        _refresh_sync(provider, force=True)
        assert len(provider._test_calls) == 2

    def test_offline_keeps_cached_text(self, config, tmp_path):
        provider = _provider(config, tmp_path, now=1000.0,
                             fetch_result={'temp': 17.6, 'code': 0})
        _refresh_sync(provider)
        provider._test_state['now'] = 1000.0 + 20 * 60
        provider._fetch = lambda lat, lon: (_ for _ in ()).throw(OSError('offline'))
        assert _refresh_sync(provider) == '18°F Clear'

    def test_offline_no_cache_placeholder(self, config, tmp_path):
        provider = _provider(config, tmp_path, now=1000.0, fail=True)
        assert _refresh_sync(provider) == PLACEHOLDER

    def test_corrupt_cache_placeholder(self, config, tmp_path):
        (tmp_path / 'weather.json').write_text('{not json')
        provider = _provider(config, tmp_path, now=1000.0,
                             fetch_result={'temp': 1.0, 'code': 0})
        assert provider.current_text() == PLACEHOLDER
        assert not provider.cache_is_fresh()
