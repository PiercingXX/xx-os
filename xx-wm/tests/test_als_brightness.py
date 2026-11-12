"""Automatic brightness: paused while blanked, smoothed, ramped.

The ALS poll used to keep running while the display was blanked (writing
brightness >= 1 into a panel meant to be dark), applied any single sample
past an absolute threshold, and jumped straight to the new level.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import als_brightness  # noqa: E402


class _GLib:
    SOURCE_REMOVE = False
    SOURCE_CONTINUE = True

    def __init__(self):
        self.timers: dict[int, tuple[int, object]] = {}
        self._next = 1

    def timeout_add(self, ms, cb, *_a):
        tid = self._next
        self._next += 1
        self.timers[tid] = (ms, cb)
        return tid

    def source_remove(self, tid):
        self.timers.pop(tid, None)

    def run_ramps(self):
        """Fire ramp timers (short interval) until none are left."""
        while True:
            ramps = [(tid, cb) for tid, (ms, cb) in self.timers.items()
                     if ms == als_brightness._RAMP_STEP_MS]
            if not ramps:
                return
            for tid, cb in ramps:
                if not cb():
                    self.timers.pop(tid, None)

    def poll_timers(self):
        return [tid for tid, (ms, _cb) in self.timers.items()
                if ms == als_brightness._POLL_INTERVAL_MS]


@pytest.fixture(autouse=True)
def _lit_display(monkeypatch):
    monkeypatch.setattr(als_brightness, '_display_blanked', False,
                        raising=False)


@pytest.fixture
def als_env(monkeypatch, tmp_path):
    glib = _GLib()
    monkeypatch.setattr(als_brightness, 'GLib', glib)
    sensor = tmp_path / 'in_illuminance_input'
    sensor.write_text('50\n')
    panel = tmp_path / 'leds' / 'lcd-backlight'
    panel.mkdir(parents=True)
    (panel / 'brightness').write_text('300\n')
    (panel / 'max_brightness').write_text('2047\n')
    monkeypatch.setattr(als_brightness, '_find_path', lambda _g: sensor)
    monkeypatch.setattr(als_brightness, '_backlight_path',
                        lambda: panel / 'brightness')
    writes: list[int] = []
    monkeypatch.setattr(als_brightness, 'apply_backlight',
                        lambda value, **_k: writes.append(value) or True)
    jobs: list = []
    monkeypatch.setattr(als_brightness, 'run_backlight_io', jobs.append,
                        raising=False)

    def run_jobs():
        while jobs:
            jobs.pop(0)()

    def lux(value):
        sensor.write_text(f'{value}\n')

    als = als_brightness.ALSBrightness()
    yield als, glib, writes, run_jobs, lux, jobs
    # A live instance would follow later tests' blank/wake and write
    # through the real backlight chain.
    als.stop()
    als_brightness._INSTANCES.discard(als)


def _settle(als, glib, run_jobs):
    glib.run_ramps()
    run_jobs()


def test_blanked_display_pauses_polling_and_writes(als_env):
    als, glib, writes, run_jobs, lux, _jobs = als_env
    als.start()
    _settle(als, glib, run_jobs)
    assert glib.poll_timers()
    writes.clear()
    als_brightness.set_display_blanked(True)
    assert glib.poll_timers() == []
    lux(5000)
    assert als._poll() is False
    _settle(als, glib, run_jobs)
    assert writes == []


def test_wake_resumes_from_a_fresh_reading(als_env):
    als, glib, writes, run_jobs, lux, _jobs = als_env
    als.start()
    _settle(als, glib, run_jobs)
    als_brightness.set_display_blanked(True)
    lux(1500)
    writes.clear()
    als_brightness.set_display_blanked(False)
    _settle(als, glib, run_jobs)
    assert glib.poll_timers()
    assert writes and writes[-1] == round(
        als_brightness._lux_to_ratio(1500) * 2047)


def test_start_while_blanked_waits_for_wake(als_env):
    als, glib, writes, run_jobs, _lux, _jobs = als_env
    als_brightness.set_display_blanked(True)
    als.start()
    _settle(als, glib, run_jobs)
    assert writes == []
    assert glib.poll_timers() == []
    als_brightness.set_display_blanked(False)
    _settle(als, glib, run_jobs)
    assert writes


def test_single_sample_spike_is_ignored(als_env):
    als, glib, writes, run_jobs, lux, _jobs = als_env
    als.start()
    _settle(als, glib, run_jobs)
    writes.clear()
    lux(5000)
    als._poll()
    lux(50)
    als._poll()
    als._poll()
    _settle(als, glib, run_jobs)
    assert writes == []


def test_sustained_change_applies_after_settling(als_env):
    als, glib, writes, run_jobs, lux, _jobs = als_env
    als.start()
    _settle(als, glib, run_jobs)
    writes.clear()
    lux(1500)
    als._poll()
    _settle(als, glib, run_jobs)
    assert writes == []
    als._poll()
    _settle(als, glib, run_jobs)
    assert writes and writes[-1] > 307


def test_new_level_ramps_instead_of_jumping(als_env, tmp_path):
    als, glib, writes, run_jobs, _lux, _jobs = als_env
    (tmp_path / 'leds' / 'lcd-backlight' / 'brightness').write_text('1500\n')
    als.start()
    target = round(als_brightness._lux_to_ratio(50) * 2047)
    steps: list[int] = []
    for _ in range(als_brightness._RAMP_STEPS + 2):
        run_jobs()
        steps.extend(writes)
        writes.clear()
        ramps = [(tid, cb) for tid, (ms, cb) in list(glib.timers.items())
                 if ms == als_brightness._RAMP_STEP_MS]
        for tid, cb in ramps:
            if not cb():
                glib.timers.pop(tid, None)
    run_jobs()
    steps.extend(writes)
    assert steps[-1] == target
    assert len(steps) == als_brightness._RAMP_STEPS
    assert steps == sorted(steps, reverse=True)
    assert all(target <= v < 1500 for v in steps)


def test_write_queued_before_a_blank_is_dropped(als_env):
    als, glib, writes, run_jobs, _lux, jobs = als_env
    als.start()
    assert jobs, 'a write is queued on the backlight worker'
    als_brightness.set_display_blanked(True)
    run_jobs()
    glib.run_ramps()
    assert writes == []


def test_writes_coalesce_while_the_worker_is_busy(als_env):
    als, glib, writes, run_jobs, _lux, jobs = als_env
    als.start()
    glib.run_ramps()        # every ramp step lands before the worker runs
    assert len(jobs) == 1
    run_jobs()
    assert len(writes) == 1


def test_display_manager_blank_pauses_als(monkeypatch):
    import display_manager
    seen: list = []
    monkeypatch.setattr(display_manager, 'set_display_blanked', seen.append,
                        raising=False)
    mgr = display_manager.DisplayManager.__new__(display_manager.DisplayManager)
    mgr._blanked = True
    mgr._on_blank_changed = None
    mgr._notify_blank_changed()
    mgr._blanked = False
    mgr._notify_blank_changed()
    assert seen == [True, False]
