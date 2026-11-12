"""Blank, wake and volume keys never block the GTK loop; the backlight is
always named (LED-class panels included, never bare brightnessctl).

brightnessctl, wlopm, pactl and logind's SetBrightness are process execs or
D-Bus round trips. They run on worker threads (_run_io / _run_volume_io /
als_brightness.run_backlight_io); the main loop only queues them.
"""
import subprocess
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import als_brightness  # noqa: E402
import backlight  # noqa: E402
import display_manager  # noqa: E402


class _GLib:
    SOURCE_REMOVE = False
    SOURCE_CONTINUE = True

    def __init__(self):
        self.idles: list = []

    def idle_add(self, fn, *a):
        self.idles.append((fn, a))
        return 0

    @staticmethod
    def timeout_add(_ms, _cb, *_a):
        return 1

    timeout_add_seconds = timeout_add

    @staticmethod
    def source_remove(_tid):
        return None


@pytest.fixture
def env(monkeypatch):
    glib = _GLib()
    monkeypatch.setattr(display_manager, 'GLib', glib)
    monkeypatch.setattr(display_manager, '_all_event_devices',
                        lambda: ['/dev/input/event0'])
    monkeypatch.setattr(display_manager, '_watch_evdev', lambda *a, **k: None)
    monkeypatch.setattr(display_manager, '_monitor_input_dir',
                        lambda cb: None, raising=False)
    monkeypatch.setattr(display_manager, '_detect_fp_node', lambda: '')
    monkeypatch.setattr(display_manager, '_make_output_power', lambda: None)
    blocking: list = []
    monkeypatch.setattr(display_manager, '_brightnessctl',
                        lambda *a: blocking.append(('bl', a)) or '120')
    monkeypatch.setattr(display_manager, '_wlopm',
                        lambda mode: blocking.append(('wlopm', mode)) or True)
    monkeypatch.setattr(display_manager, '_set_volume',
                        lambda d: blocking.append(('pactl-set', d)))
    monkeypatch.setattr(display_manager, '_get_volume_pct',
                        lambda: blocking.append(('pactl-get',)) or 40)
    io_jobs: list = []
    volume_jobs: list = []
    monkeypatch.setattr(display_manager, '_run_io', io_jobs.append,
                        raising=False)
    monkeypatch.setattr(display_manager, '_run_volume_io', volume_jobs.append,
                        raising=False)
    return glib, blocking, io_jobs, volume_jobs


class _Hud:
    def __init__(self):
        self.shown: list = []

    def show_volume(self, pct):
        self.shown.append(pct)


def _drain(jobs):
    while jobs:
        jobs.pop(0)()


def test_blank_queues_backlight_and_wlopm_off_the_main_loop(env):
    _glib, blocking, io_jobs, _volume_jobs = env
    mgr = display_manager.DisplayManager()
    mgr._blank()
    assert mgr.blanked is True
    assert blocking == []
    _drain(io_jobs)
    assert blocking == [('bl', ('get',)), ('bl', ('set', '0')),
                        ('wlopm', 'off')]


def test_wake_queues_backlight_restore_off_the_main_loop(env):
    _glib, blocking, io_jobs, _volume_jobs = env
    mgr = display_manager.DisplayManager()
    mgr._blank()
    _drain(io_jobs)
    blocking.clear()
    mgr._wake()
    assert mgr.blanked is False
    assert blocking == []
    _drain(io_jobs)
    assert blocking == [('wlopm', 'on'), ('bl', ('set', '120'))]


def test_power_key_blank_and_wake_never_block(env):
    _glib, blocking, io_jobs, _volume_jobs = env
    mgr = display_manager.DisplayManager()
    for value in (1, 0, 1, 0):
        mgr._on_event('/dev/input/event0', display_manager.EV_KEY,
                      display_manager.KEY_POWER, value)
    assert blocking == []
    assert len(io_jobs) == 2


def test_volume_key_runs_pactl_on_the_worker_then_flashes_hud(env):
    glib, blocking, _io_jobs, volume_jobs = env
    hud = _Hud()
    mgr = display_manager.DisplayManager(hud=hud)
    mgr._on_event('/dev/input/event0', display_manager.EV_KEY,
                  display_manager.KEY_VOLUMEUP, 1)
    assert blocking == []
    assert hud.shown == []
    _drain(volume_jobs)
    assert blocking == [('pactl-set', '+5%'), ('pactl-get',)]
    assert hud.shown == []          # the flash hops back to the main loop
    for fn, args in glib.idles:
        fn(*args)
    assert hud.shown == [40]


class TestBrightnessctlNamesTheDevice:
    def test_no_backlight_found_never_runs_bare_brightnessctl(
            self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(backlight, 'find', lambda root=None: None)
        monkeypatch.setattr(subprocess, 'check_output',
                            lambda cmd, **k: calls.append(cmd) or '')
        assert display_manager._brightnessctl('get') is None
        assert calls == []

    def test_led_class_panel_is_named(self, monkeypatch, tmp_path):
        leds = tmp_path / 'leds'
        for name in ('lcd-backlight', 'vibrator'):
            (leds / name).mkdir(parents=True)
            (leds / name / 'brightness').write_text('0\n')
            (leds / name / 'max_brightness').write_text('2047\n')
        (tmp_path / 'backlight').mkdir()
        real_find = backlight.find
        monkeypatch.setattr(backlight, 'find',
                            lambda root=None: real_find(tmp_path))
        calls: list = []
        monkeypatch.setattr(subprocess, 'check_output',
                            lambda cmd, **k: calls.append(cmd) or '900\n')
        assert display_manager._brightnessctl('get') == '900'
        assert calls == [['brightnessctl', '--device', 'lcd-backlight', 'get']]

    def test_refused_set_falls_back_to_logind(self, monkeypatch, tmp_path):
        found = backlight.Backlight('lcd-backlight', 'leds',
                                    tmp_path / 'brightness')
        monkeypatch.setattr(backlight, 'find', lambda root=None: found)

        def _fail(cmd, **k):
            raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(subprocess, 'check_output', _fail)
        seen: list = []
        monkeypatch.setattr(
            display_manager, 'login1_set_brightness',
            lambda name, value, subsystem='backlight', bus=None:
            seen.append((name, value, subsystem)) or True, raising=False)
        assert display_manager._brightnessctl('set', '300') == ''
        assert seen == [('lcd-backlight', 300, 'leds')]

    def test_als_chain_skips_brightnessctl_without_a_device(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(subprocess, 'run',
                            lambda cmd, **k: calls.append(cmd))
        assert als_brightness._brightnessctl_set(100, None) is False
        assert calls == []


class TestBacklightWorker:
    def test_jobs_run_in_order_on_another_thread(self):
        worker = als_brightness.SerialWorker('test-io')
        seen: list = []
        done = threading.Event()
        main = threading.get_ident()
        for i in range(5):
            worker.submit(lambda i=i: seen.append((i, threading.get_ident())))
        worker.submit(done.set)
        assert done.wait(2)
        assert [i for i, _ in seen] == [0, 1, 2, 3, 4]
        assert all(tid != main for _, tid in seen)

    def test_a_failing_job_does_not_stop_the_worker(self):
        worker = als_brightness.SerialWorker('test-io')
        done = threading.Event()
        worker.submit(lambda: 1 / 0)
        worker.submit(done.set)
        assert done.wait(2)

    def test_slider_fallback_returns_at_once_and_queues(self, monkeypatch):
        queued: list = []
        monkeypatch.setattr(als_brightness, 'run_backlight_io', queued.append)
        ran: list = []
        monkeypatch.setattr(als_brightness, 'apply_backlight_pct_sync',
                            lambda pct: ran.append(pct) or True,
                            raising=False)
        assert als_brightness.apply_backlight_pct(60) is True
        assert ran == []
        queued[0]()
        assert ran == [60]
