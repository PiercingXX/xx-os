"""The privacy monitor's thread sleeps while the screen is dark: it
woke every 3 s whether or not anything could show its indicators, and
catches up at once when the screen comes back."""
import threading
import types

import privacy_monitor
from privacy_monitor import PrivacyMonitor


class _Proc:
    """A pw-dump --monitor child that never prints."""

    def __init__(self):
        self.stdout = types.SimpleNamespace(fileno=lambda: 7)

    def wait(self, timeout=None):
        return 0


class TestPrivacyMonitorDark:
    def _watch(self, monkeypatch, screen_on, passes=3):
        located = []
        monitor = PrivacyMonitor(
            on_change=lambda _s: None,
            location_provider=lambda: located.append(1) or ())
        monitor.set_screen_on(screen_on)
        timeouts = []

        def _select(_r, _w, _x, timeout=None):
            timeouts.append(timeout)
            if len(timeouts) >= passes:
                monitor._stop_event.set()
            return [], [], []
        monkeypatch.setattr(privacy_monitor.select, 'select', _select)
        monitor._watch(_Proc())
        return timeouts, located

    def test_lit_screen_polls_location(self, monkeypatch):
        timeouts, located = self._watch(monkeypatch, True)
        assert timeouts == [privacy_monitor.POLL_INTERVAL_SECONDS] * 3
        assert len(located) == 3

    def test_dark_screen_sleeps_and_skips_location(self, monkeypatch):
        timeouts, located = self._watch(monkeypatch, False)
        assert timeouts == [privacy_monitor.DARK_POLL_SECONDS] * 3
        assert located == []

    def test_waking_catches_up_at_once(self):
        located = threading.Event()
        monitor = PrivacyMonitor(
            on_change=lambda _s: None,
            location_provider=lambda: located.set() or ())
        monitor.set_screen_on(False)
        monitor._thread = types.SimpleNamespace(is_alive=lambda: True)
        monitor.set_screen_on(True)
        assert located.wait(2)
