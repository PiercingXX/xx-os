"""Lock screen lifecycle: fingerprint verify only on a lit, visible lock and
only on verify-match; lockout state failures fail closed and are logged;
the clock tick runs only while the lock is shown; unlock wipes the PIN."""
import inspect
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
