"""Coalesced config saves: a burst of setters is one write, off the GTK
thread; PIN and lockout state still write at once; other instances in
the process read what is queued; a monitor event for our own write is not
re-parsed; the night-light slider restarts wlsunset once per drag."""
import json
import os
import threading
import time
import types

import pytest


@pytest.fixture
def deferred(tmp_path, monkeypatch):
    import config
    monkeypatch.setenv('HOME', str(tmp_path))
    writes = []
    real_replace = os.replace

    def _spy(src, dst, **kwargs):
        writes.append(threading.current_thread())
        return real_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, 'replace', _spy)
    config.enable_deferred_saves(True)
    yield types.SimpleNamespace(config=config, writes=writes)
    config.flush_saves()
    config.enable_deferred_saves(False)


def _pump(until, timeout=3.0):
    from gi.repository import GLib
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while not until() and time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.01)


class TestCoalescedSaves:
    def test_burst_of_setters_is_one_write_off_the_main_thread(
            self, deferred):
        cfg = deferred.config.ShellConfig()
        for step in range(20):  # a dragged slider, a first-boot burst
            cfg.set_text_size_scale(1.0 + step / 100)
            cfg.set_home_alignment('left' if step % 2 else 'center')
        assert deferred.writes == []
        _pump(lambda: deferred.writes)
        time.sleep(0.2)
        assert len(deferred.writes) == 1
        assert deferred.writes[0] is not threading.main_thread()
        on_disk = json.loads(cfg.config_path.read_text())
        assert on_disk['text_size_scale'] == pytest.approx(1.19)

    def test_pin_and_lockout_write_at_once(self, deferred):
        cfg = deferred.config.ShellConfig()
        cfg.set_lockout_state(3, 123.0)
        assert len(deferred.writes) == 1
        assert json.loads(cfg.config_path.read_text())[
            deferred.config._LOCKOUT_STATE_KEY]['fails'] == 3
        cfg.set_pin('123456')
        assert len(deferred.writes) == 2
        assert 'pin_hash' in json.loads(cfg.config_path.read_text())

    def test_another_instance_reads_the_queued_save(self, deferred):
        first = deferred.config.ShellConfig()
        first.set_theme('ocean')
        assert deferred.writes == []
        second = deferred.config.ShellConfig()
        assert second.data['theme'] == 'ocean'

    def test_failed_load_queues_nothing(self, deferred):
        cfg = deferred.config.ShellConfig()
        cfg.load_failed = True
        cfg.set_theme('ocean')
        deferred.config.flush_saves()
        assert deferred.writes == []

    def test_shutdown_flush_writes_the_queue(self, deferred):
        cfg = deferred.config.ShellConfig()
        cfg.set_theme('ocean')
        deferred.config.flush_saves()
        assert len(deferred.writes) == 1
        assert json.loads(cfg.config_path.read_text())['theme'] == 'ocean'


class TestOwnWrites:
    def test_own_write_is_recognised_and_an_edit_is_not(self, deferred):
        cfg = deferred.config.ShellConfig()
        cfg.set_theme('ocean')
        assert cfg.is_own_write()  # queued
        deferred.config.flush_saves()
        assert cfg.is_own_write()  # written by us
        time.sleep(0.01)
        cfg.config_path.write_text(json.dumps({'theme': 'forest-green'}))
        assert not cfg.is_own_write()

    def test_reload_does_not_reparse_our_own_save(self, deferred):
        import window
        cfg = deferred.config.ShellConfig()
        cfg.set_theme('ocean')
        deferred.config.flush_saves()
        reads = []
        cfg.read_fresh = lambda: reads.append(1)
        shell = types.SimpleNamespace(
            config=cfg, _config_reload_timer=1,
            _reload_gestures=lambda: None,
            _apply_config_change=lambda: None)
        window.ShellWindow._reload_config(shell)
        assert reads == []


class TestNightLightSlider:
    def test_drag_restarts_wlsunset_once(self, monkeypatch):
        import settings_display
        from gi.repository import GLib
        spawned = []
        monkeypatch.setattr(settings_display, 'night_light_running',
                            lambda: True)
        monkeypatch.setattr(
            settings_display, 'set_night_light',
            lambda enabled, temp, lat=None, lon=None: spawned.append(temp))
        timers = {}

        def _add(_ms, cb, *args):
            timers[len(timers) + 1] = (cb, args)
            return len(timers)
        monkeypatch.setattr(GLib, 'timeout_add', _add)
        monkeypatch.setattr(GLib, 'source_remove',
                            lambda src: timers.pop(src, None) is not None)
        data = {}
        win = types.SimpleNamespace(config=types.SimpleNamespace(
            set=data.__setitem__, get=lambda k, d=None: data.get(k, d)))
        section = settings_display.DisplaySection()
        section._coords = lambda _w: (None, None)
        section._sunset_active = lambda _w: False
        for temp in range(3000, 4000, 100):
            section._on_temp_changed(
                win, types.SimpleNamespace(get_value=lambda t=temp: t))
        assert spawned == []
        assert len(timers) == 1
        cb, args = next(iter(timers.values()))
        cb(*args)
        assert spawned == [3900]
