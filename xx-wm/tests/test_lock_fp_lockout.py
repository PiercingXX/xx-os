"""Lock screen lifecycle: fingerprint verify only on a lit, visible lock and
only on verify-match; lockout state failures fail closed and are logged;
the clock tick runs only while the lock is shown; unlock wipes the PIN."""
import inspect
import logging
import subprocess
import types

import pytest


def _ls():
    # Imported lazily: sibling test modules stub sys.modules['gi'] during
    # collection; conftest restores the real bindings before tests run.
    import lock_screen
    return lock_screen


class _Label:
    def __init__(self, text=''):
        self.text = text

    def set_text(self, text):
        self.text = text

    def set_label(self, text):
        self.text = text

    def add_css_class(self, _name):
        pass

    def remove_css_class(self, _name):
        pass


class _Key:
    def __init__(self):
        self.sensitive = True

    def set_sensitive(self, value):
        self.sensitive = bool(value)


class _Proc:
    def __init__(self, out='', returncode=0, hang=False):
        self.out = out
        self.returncode = None
        self._rc = returncode
        self.hang = hang
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode

    def communicate(self, timeout=None):
        if self.hang:
            raise subprocess.TimeoutExpired('fprintd-verify', timeout)
        self.returncode = self._rc
        return self.out, None


class _Config:
    clock_24h = True

    def __init__(self, fails=0, until=0.0, read_error=None,
                 write_error=None, clear_error=None):
        self.fails = fails
        self.until = until
        self.read_error = read_error
        self.write_error = write_error
        self.clear_error = clear_error
        self.writes = []
        self.cleared = 0

    def get_lockout_state(self):
        if self.read_error:
            raise self.read_error
        return (self.fails, self.until)

    def get_lockout_start(self):
        return 0.0

    def set_lockout_state(self, fails, until, start=None):
        if self.write_error:
            raise self.write_error
        self.writes.append((fails, until))

    def clear_lockout_state(self):
        if self.clear_error:
            raise self.clear_error
        self.cleared += 1


_BOUND = (
    '_unlock', '_start_lockout', '_tick_lockout', '_set_keypad_sensitive',
    '_refresh_dots', '_shake', '_persist_lockout_state', '_update_fp_hint',
    '_fp_result', '_fp_thread', '_refresh_clock', '_tick_clock',
)


def _lock(config=None, blanked=False, fp=True):
    ls = _ls()
    lock = types.SimpleNamespace(
        _config=config or _Config(), _pin='', _fail_count=0,
        _lockout_src=None, _fp_running=False, _open_shade_after_unlock=False,
        dots_label=_Label(), error_label=_Label(), lockout_label=_Label(),
        _fp_hint=_Label(), clock_label=_Label(), date_label=_Label(),
        _keypad_btns=[_Key() for _ in range(3)],
        visible=True, unlocked=0, verifies=0,
        app=types.SimpleNamespace(
            _display_mgr=types.SimpleNamespace(blanked=blanked)))
    lock.get_visible = lambda: lock.visible
    lock.set_visible = lambda v: setattr(lock, 'visible', bool(v))
    lock.get_application = lambda: lock.app
    lock._on_unlock = lambda: setattr(lock, 'unlocked', lock.unlocked + 1)
    lock._on_open_shade = lambda: None
    lock._fp_available = lambda: fp
    lock._fit_keypad_to_screen = lambda: None
    lock.refresh_fp_state = lambda: None
    for name in _BOUND:
        if hasattr(ls.LockScreen, name):
            setattr(lock, name, types.MethodType(
                getattr(ls.LockScreen, name), lock))
    if hasattr(ls.LockScreen, 'try_fingerprint_unlock'):
        lock.try_fingerprint_unlock = types.MethodType(
            ls.LockScreen.try_fingerprint_unlock, lock)
    return lock


@pytest.fixture
def glib(monkeypatch):
    ls = _ls()
    rec = types.SimpleNamespace(timeouts=[], removed=[], idle=[], next=100)

    def _timeout(_secs, cb, *a):
        rec.next += 1
        rec.timeouts.append((rec.next, cb))
        return rec.next

    monkeypatch.setattr(ls.GLib, 'timeout_add', _timeout)
    monkeypatch.setattr(ls.GLib, 'timeout_add_seconds', _timeout)
    monkeypatch.setattr(ls.GLib, 'source_remove',
                        lambda src: rec.removed.append(src) or True)
    monkeypatch.setattr(ls.GLib, 'idle_add',
                        lambda cb, *a: rec.idle.append((cb, a)) or 1)
    # Sibling modules may bind lock_screen to a minimal fake GLib.
    monkeypatch.setattr(ls.GLib, 'get_monotonic_time', lambda: 0,
                        raising=False)
    monkeypatch.setattr(ls.GLib, 'SOURCE_REMOVE', False, raising=False)
    monkeypatch.setattr(ls.GLib, 'SOURCE_CONTINUE', True, raising=False)
    return rec


@pytest.fixture
def threads(monkeypatch):
    ls = _ls()
    started = []

    def _thread(*_a, **kw):
        return types.SimpleNamespace(start=lambda: started.append(kw))

    monkeypatch.setattr(ls.threading, 'Thread', _thread)
    return started


# ---------------------------------------------------------------- clock

class TestClockTick:
    def test_construction_registers_no_tick(self):
        src = inspect.getsource(_ls().LockScreen.__init__)
        assert '_tick_clock' not in src

    def test_shown_starts_one_tick(self, glib, threads):
        ls = _ls()
        lock = _lock()
        ls.LockScreen.on_shown(lock)
        ls.LockScreen.on_shown(lock)
        ticks = [cb for _id, cb in glib.timeouts if cb == lock._tick_clock]
        assert len(ticks) == 1
        assert lock._clock_src is not None

    def test_shown_on_dark_screen_starts_no_tick(self, glib, threads):
        ls = _ls()
        lock = _lock(blanked=True)
        ls.LockScreen.on_shown(lock)
        assert glib.timeouts == []

    def test_unlock_stops_tick(self, glib, threads):
        ls = _ls()
        lock = _lock()
        ls.LockScreen.on_shown(lock)
        src = lock._clock_src
        lock._unlock()
        assert src in glib.removed
        assert lock._clock_src is None

    def test_blank_stops_and_wake_restarts_tick(self, glib, threads):
        ls = _ls()
        lock = _lock()
        ls.LockScreen.on_shown(lock)
        src = lock._clock_src
        ls.LockScreen.on_blank_changed(lock, True)
        assert src in glib.removed
        ls.LockScreen.on_blank_changed(lock, False)
        assert lock._clock_src is not None and lock._clock_src != src

    def test_tick_stops_itself_once_hidden(self, glib, threads):
        ls = _ls()
        lock = _lock()
        ls.LockScreen.on_shown(lock)
        assert lock._tick_clock() is True
        lock.visible = False
        assert lock._tick_clock() is False
        assert lock._clock_src is None


# ---------------------------------------------------------------- PIN buffer

class TestUnlockWipesPin:
    def test_unlock_clears_buffer_and_dots(self, glib, threads):
        lock = _lock()
        lock._pin = '1234'
        lock.dots_label.set_text('●●●●')
        lock._unlock()
        assert lock._pin == ''
        assert lock.dots_label.text == ''


# ---------------------------------------------------------------- lockout

class TestLockoutFailsClosed:
    def test_unreadable_state_arms_lockout(self, glib, caplog):
        ls = _ls()
        lock = _lock(_Config(read_error=OSError('disk')))
        with caplog.at_level(logging.ERROR, logger='lock_screen'):
            ls.LockScreen._resume_lockout_state(lock)
        assert lock._fail_count >= ls._FAIL_THRESHOLD
        assert lock._lockout_src is not None
        assert all(not k.sensitive for k in lock._keypad_btns)
        assert 'lockout state unreadable' in caplog.text

    def test_persist_failure_is_logged_and_lockout_holds(self, glib, caplog):
        ls = _ls()
        lock = _lock(_Config(write_error=OSError('read-only')))
        lock._fail_count = ls._FAIL_THRESHOLD - 1
        with caplog.at_level(logging.ERROR, logger='lock_screen'):
            ls.LockScreen._on_pin_result(lock, False, 0, None)
        assert lock._lockout_src is not None
        assert 'lockout state not saved' in caplog.text

    def test_clear_failure_on_unlock_is_logged(self, glib, caplog):
        lock = _lock(_Config(clear_error=OSError('read-only')))
        with caplog.at_level(logging.WARNING, logger='lock_screen'):
            lock._unlock()
        assert lock.unlocked == 1
        assert 'lockout state not cleared' in caplog.text


# ---------------------------------------------------------------- fingerprint

class TestFingerprintLifecycle:
    def test_wake_while_locked_starts_verify(self, glib, threads):
        lock = _lock()
        _ls().LockScreen.on_blank_changed(lock, False)
        assert [t['name'] for t in threads] == ['fp-verify']
        assert lock._fp_running is True

    def test_wake_with_lock_hidden_starts_nothing(self, glib, threads):
        lock = _lock()
        lock.visible = False
        _ls().LockScreen.on_blank_changed(lock, False)
        assert threads == []

    def test_probe_done_on_dark_screen_does_not_verify(self, glib, threads):
        lock = _lock(blanked=True)
        _ls().LockScreen._fp_probe_done(lock, True)
        assert threads == []
        assert lock._fp_running is False

    def test_try_on_dark_screen_does_not_verify(self, glib, threads):
        lock = _lock(blanked=True)
        _ls().LockScreen.try_fingerprint_unlock(lock)
        assert threads == []

    def test_blank_kills_running_verify(self, glib, threads):
        lock = _lock()
        proc = _Proc()
        lock._fp_proc = proc
        lock._fp_running = True
        _ls().LockScreen.on_blank_changed(lock, True)
        assert proc.terminated
        assert lock._fp_running is False

    def test_unlock_kills_running_verify(self, glib, threads):
        lock = _lock()
        proc = _Proc()
        lock._fp_proc = proc
        lock._fp_running = True
        lock._unlock()
        assert proc.terminated
        assert lock._fp_running is False

    def test_lockout_kills_running_verify(self, glib, threads, monkeypatch):
        lock = _lock()
        proc = _Proc()
        lock._fp_proc = proc
        lock._fp_running = True
        lock._start_lockout(30)
        assert proc.terminated
        assert lock._fp_running is False
        assert lock._fp_hint.text == ''

    def test_lockout_end_restarts_verify(self, glib, threads, monkeypatch):
        ls = _ls()
        lock = _lock()
        now = [0]
        monkeypatch.setattr(ls.GLib, 'get_monotonic_time', lambda: now[0])
        lock._start_lockout(30)
        assert threads == []
        now[0] = 31 * 1_000_000
        lock._tick_lockout()
        assert lock._lockout_src is None
        assert [t['name'] for t in threads] == ['fp-verify']

    def test_cancelled_run_result_never_unlocks(self, glib, threads):
        ls = _ls()
        lock = _lock()
        ls.LockScreen.try_fingerprint_unlock(lock)
        (gen,) = threads[0]['args']
        ls.LockScreen.on_blank_changed(lock, True)
        lock._fp_result(True, gen)
        assert lock.unlocked == 0

    def test_current_run_match_unlocks(self, glib, threads):
        ls = _ls()
        lock = _lock()
        ls.LockScreen.try_fingerprint_unlock(lock)
        (gen,) = threads[0]['args']
        lock._fp_result(True, gen)
        assert lock.unlocked == 1
        assert lock.visible is False

    def test_fingerprint_unlock_mid_typing_clears_buffer(self, glib, threads):
        ls = _ls()
        lock = _lock()
        ls.LockScreen.try_fingerprint_unlock(lock)
        (gen,) = threads[0]['args']
        lock._pin = '12'
        lock.dots_label.set_text('●●')
        lock._fp_result(True, gen)
        assert lock._pin == ''
        assert lock.dots_label.text == ''


class TestVerifyMatch:
    def test_only_verify_match_counts(self):
        matched = _ls().fp_verify_matched
        assert matched(0, 'Verify result: verify-match (done)\n')
        assert not matched(0, 'Verify result: verify-no-match (done)\n')
        assert not matched(0, '')
        assert not matched(0, 'Verify result: verify-retry-scan\n')
        assert not matched(1, 'Verify result: verify-match (done)\n')
        assert not matched(None, 'verify-match')

    def _run(self, monkeypatch, glib, proc, gen=7):
        ls = _ls()
        monkeypatch.setattr(ls.subprocess, 'Popen',
                            lambda cmd, **kw: proc)
        lock = _lock()
        lock._fp_gen = gen
        ls.LockScreen._fp_thread(lock, gen)
        assert glib.idle
        cb, args = glib.idle[-1]
        return args

    def test_clean_exit_without_match_is_no_match(self, monkeypatch, glib):
        assert self._run(monkeypatch, glib, _Proc(out='', returncode=0)) \
            == (False, 7)

    def test_match_line_unlocks(self, monkeypatch, glib):
        proc = _Proc(out='Verify result: verify-match (done)\n')
        assert self._run(monkeypatch, glib, proc) == (True, 7)

    def test_timeout_kills_and_is_no_match(self, monkeypatch, glib):
        proc = _Proc(hang=True)
        assert self._run(monkeypatch, glib, proc) == (False, 7)
        assert proc.killed

    def test_argv_is_bare_fprintd_verify(self, monkeypatch, glib):
        ls = _ls()
        calls = []

        def _popen(cmd, **kw):
            calls.append((cmd, kw))
            return _Proc(out='verify-match')

        monkeypatch.setattr(ls.subprocess, 'Popen', _popen)
        ls.LockScreen._fp_thread(_lock(), 0)
        assert [cmd for cmd, _kw in calls] == [['fprintd-verify']]
        assert calls[0][1]['stdout'] == subprocess.PIPE

    def test_missing_fprintd_is_no_match(self, monkeypatch, glib):
        ls = _ls()

        def _popen(cmd, **kw):
            raise FileNotFoundError(cmd[0])

        monkeypatch.setattr(ls.subprocess, 'Popen', _popen)
        ls.LockScreen._fp_thread(_lock(), 3)
        assert glib.idle[-1][1] == (False, 3)


class TestRealVerifyProcess:
    """A real child process stands in for fprintd-verify on PATH."""

    @staticmethod
    def _fake_verify(tmp_path, monkeypatch, body):
        script = tmp_path / 'fprintd-verify'
        script.write_text('#!/bin/sh\n' + body + '\n')
        script.chmod(0o755)
        monkeypatch.setenv('PATH', f'{tmp_path}:/usr/bin:/bin')

    def test_match_output_reports_match(self, tmp_path, monkeypatch, glib):
        self._fake_verify(tmp_path, monkeypatch,
                          'echo "Verify result: verify-match (done)"')
        lock = _lock()
        lock._fp_gen = 2
        _ls().LockScreen._fp_thread(lock, 2)
        assert glib.idle[-1][1] == (True, 2)

    def test_no_match_exit_zero_reports_no_match(
            self, tmp_path, monkeypatch, glib):
        self._fake_verify(tmp_path, monkeypatch,
                          'echo "Verify result: verify-no-match (done)"')
        lock = _lock()
        _ls().LockScreen._fp_thread(lock, 0)
        assert glib.idle[-1][1] == (False, 0)

    def test_stop_terminates_waiting_verify(self, tmp_path, monkeypatch,
                                            glib):
        import threading
        import time
        ls = _ls()
        self._fake_verify(tmp_path, monkeypatch, 'exec sleep 60')
        lock = _lock()
        lock._fp_gen = 1
        lock._fp_running = True
        worker = threading.Thread(target=ls.LockScreen._fp_thread,
                                  args=(lock, 1))
        worker.start()
        deadline = time.monotonic() + 5
        while getattr(lock, '_fp_proc', None) is None:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        proc = lock._fp_proc
        ls.LockScreen._stop_fingerprint(lock)
        worker.join(5)
        assert not worker.is_alive()
        assert proc.returncode is not None
        cb, args = glib.idle[-1]
        assert args == (False, 1)
        cb(*args)
        assert lock.unlocked == 0
