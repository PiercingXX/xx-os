"""Startup never comes up unlocked while the root helper holds a PIN.

A deleted, reset or corrupt config.json loses the marker: the app asks
the helper before the wizard or the shell shows anything, and locks
first. A helper that does not answer fails closed when it is installed
(its D-Bus activation file exists) and the PIN pad verifies through it
once it answers; a helper that was never installed locks nobody out. A
hot reload can neither drop the marker nor swap in a local hash.

Headless: real ShellConfig on a tmp HOME, the helper's real PinStore
behind scripted helper_client transports, fake lock/wizard windows.
"""
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'launcher' / 'helper'))
sys.path.insert(0, str(ROOT / 'launcher' / 'src'))

import config as config_mod  # noqa: E402
import helper_client  # noqa: E402
import xx_wm_helper as helper  # noqa: E402
from config import HELPER_PIN_MARKER, ShellConfig  # noqa: E402
from test_lock_hardening import (  # noqa: E402
    UID, FakeHelper, _FakeLock, _FakeWizard, _lock_screen, _no_window_leak,
    _Reporter, _window)

PIN = '246810'

assert _no_window_leak  # fixture, used by name below


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    sets = [getattr(config_mod, name, set())
            for name in ('_HELPER_PIN_PATHS', '_FAILED_PATHS')]
    saved = [set(s) for s in sets]
    yield
    for live, before in zip(sets, saved):
        live.clear()
        live.update(before)


def _config_path() -> Path:
    path = Path.home() / '.config' / 'xx-wm' / 'config.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture()
def store(tmp_path):
    pins = helper.PinStore(root=str(tmp_path / 'pin'),
                           now=lambda: 1_800_000_000.0, scrypt_n=2 ** 4)
    pins.set_pin(UID, '', PIN)
    return pins


@pytest.fixture()
def service_dir(tmp_path, monkeypatch):
    """Where helper_installed looks; empty = never installed."""
    path = tmp_path / 'system-services'
    path.mkdir()
    monkeypatch.setattr(helper_client, 'HELPER_SERVICE_DIRS', (str(path),))
    return path


def _install_helper(service_dir: Path) -> None:
    (service_dir / (helper_client.BUS_NAME + '.service')).write_text(
        '[D-BUS Service]\nName=%s\n' % helper_client.BUS_NAME)


@pytest.fixture()
def timers(monkeypatch):
    pending = []
    monkeypatch.setattr(helper_client, '_add_timeout',
                        lambda ms, fn: pending.append((ms, fn)))
    return pending


def _fire(timers) -> None:
    for _ms, fn in list(timers):
        fn()
    timers.clear()


class SilentHelper(FakeHelper):
    """HasPin is sent but its reply only arrives when the test says so."""

    def __init__(self, store):
        super().__init__(store)
        self.waiting = []

    def has_pin(self, on_done):
        self.calls.append('HasPin')
        self.waiting.append(on_done)

    def answer(self) -> None:
        for on_done in self.waiting:
            on_done(True, self.store.has_pin(UID))
        self.waiting.clear()


def _verify(config, pin):
    out = []
    helper_client.pin_verify(config, pin, lambda *a: out.append(a))
    assert len(out) == 1
    return out[0]


# ------------------------------------------------- app startup (main.py)

class _App:
    """XXWMApplication's startup methods on a plain object."""

    def __init__(self, monkeypatch):
        try:
            import main
        except Exception as exc:  # pragma: no cover - env without GTK
            pytest.skip('main not importable: %s' % exc)
        import first_boot
        import font_theme
        ls = _lock_screen()
        _FakeLock.instances = []
        _FakeLock.fail = False
        _FakeWizard.instances = []
        self.reporter = _Reporter()
        self.idle = []
        self.shells = []
        self.holds = 0
        self.telephony = None
        wizard_cls = type('Wizard', (_FakeWizard,), {
            'is_needed': staticmethod(lambda: first_boot.setup_pending(
                Path.home() / '.config' / 'xx-wm' / 'config.json'))})
        monkeypatch.setattr(main, 'FirstBootWizard', wizard_cls)
        for name in ('drop_portal_opt_out', 'IPCServer', '_setup_logind',
                     'NotificationDaemon'):
            monkeypatch.setattr(main, name, lambda *a, **k: None)
        monkeypatch.setattr(font_theme, 'apply_global_font', lambda *_a: None)
        monkeypatch.setattr(ls, 'LockScreen', _FakeLock)
        monkeypatch.setattr(ls, '_shell_state', lambda: self.reporter)
        monkeypatch.setattr(main.GLib, 'idle_add',
                            lambda fn, *a: self.idle.append(fn) or 1)
        self._shell = None
        self._wizard = None
        self._wizard_lock = None
        self._pin_gate_pending = False
        self._pin_gate_done = False
        self.props = SimpleNamespace(active_window=None)
        self._on_ipc_command = self._on_sleep = None
        self._on_notification = self._on_notification_closed = None
        for name in ('do_activate', '_gate_startup', '_on_late_helper_pin',
                     '_start_first_boot', '_present_wizard',
                     '_lock_before_wizard', '_wizard_unlocked',
                     '_wizard_done'):
            setattr(self, name, types.MethodType(
                getattr(main.XXWMApplication, name), self))

    def hold(self):
        self.holds += 1

    def _start_wizard_telephony(self):
        # Ordered against the lock and the wizard it must ring above.
        self.telephony = (len(_FakeLock.instances), len(_FakeWizard.instances))

    def _stop_wizard_telephony(self):
        pass

    def release(self):
        self.holds -= 1

    def _show_shell(self):
        # What the shell window's own ShellConfig reads at construction.
        self.shells.append(ShellConfig().pin_hash)
        self._shell = SimpleNamespace(
            locks=[], _show_lock_screen=lambda: self._shell.locks.append(1))


@pytest.fixture()
def app(monkeypatch):
    return _App(monkeypatch)


class TestResetConfigLocksFirst:
    """Hole 1: the wizard after a lost config.json ran with no lock."""

    @pytest.mark.parametrize('raw', [None, b'{"pin_hash": "helper$", oops',
                                     b'\xff\xfe junk'])
    def test_helper_pin_locks_before_the_wizard(self, app, store,
                                                monkeypatch, raw):
        FakeHelper(store).install(monkeypatch)
        if raw is not None:
            _config_path().write_bytes(raw)
        app.do_activate()
        assert _FakeWizard.instances == []
        (lock,) = _FakeLock.instances
        assert lock.config.pin_hash == HELPER_PIN_MARKER
        assert app.reporter.events == [('require_lock', True), 'ready']
        # The lock asks the helper's PIN, not any PIN.
        assert _verify(lock.config, '000000')[0] is False
        assert _verify(lock.config, PIN)[0] is True
        lock.on_unlock()
        (wizard,) = _FakeWizard.instances
        assert app._wizard is wizard and app.holds == 0

    def test_telephony_runs_for_the_locked_wizard(self, app, store,
                                                  monkeypatch, service_dir,
                                                  timers):
        _install_helper(service_dir)
        SilentHelper(store).install(monkeypatch)
        app.do_activate()
        assert app.telephony is None  # not before the lock decision
        _fire(timers)
        assert app.telephony == (0, 0)  # started, then the lock went up
        assert len(_FakeLock.instances) == 1

    def test_no_telephony_stand_in_for_the_shell(self, app, store,
                                                 monkeypatch):
        FakeHelper(store).install(monkeypatch)
        _config_path().write_text(json.dumps({'theme': 'ocean'}))
        app.do_activate()
        assert app.telephony is None and app.shells

    def test_wizard_then_skips_its_pin_step(self, app, store, monkeypatch):
        import first_boot
        FakeHelper(store).install(monkeypatch)
        app.do_activate()
        wiz = SimpleNamespace(_config=ShellConfig())
        assert first_boot.FirstBootWizard._after_keyboard_step(wiz) == 'theme'

    def test_restored_marker_keeps_setup_pending(self, app, store,
                                                 monkeypatch):
        import first_boot
        FakeHelper(store).install(monkeypatch)
        app.do_activate()
        data = json.loads(_config_path().read_text(encoding='utf-8'))
        assert data['pin_hash'] == HELPER_PIN_MARKER
        assert first_boot.setup_pending(_config_path())

    def test_finished_setup_without_marker_shows_the_shell_locked(
            self, app, store, monkeypatch):
        FakeHelper(store).install(monkeypatch)
        _config_path().write_text(json.dumps({'theme': 'ocean'}))
        app.do_activate()
        assert app.shells == [HELPER_PIN_MARKER]
        assert _FakeWizard.instances == []

    def test_no_pin_anywhere_goes_straight_to_the_wizard(self, app, store,
                                                         monkeypatch):
        store.clear_pin(UID, PIN)
        FakeHelper(store).install(monkeypatch)
        app.do_activate()
        assert _FakeLock.instances == []
        assert len(_FakeWizard.instances) == 1
        assert ShellConfig().pin_hash is None

    def test_reactivation_while_waiting_builds_nothing(
            self, app, store, monkeypatch, service_dir, timers):
        helper_ = SilentHelper(store).install(monkeypatch)
        app.do_activate()
        app.do_activate()
        assert helper_.calls == ['HasPin']
        assert app.holds == 1
        assert _FakeLock.instances == [] and _FakeWizard.instances == []
        helper_.answer()
        assert len(_FakeLock.instances) == 1 and app.holds == 0


class TestSilentHelperFailsClosed:
    """Hole 2: no answer within 3 s let the shell up unlocked."""

    def test_nothing_shows_while_waiting(self, app, store, monkeypatch,
                                         service_dir, timers):
        _install_helper(service_dir)
        SilentHelper(store).install(monkeypatch)
        app.do_activate()
        assert [ms for ms, _fn in timers] == [3000]
        assert _FakeLock.instances == [] and _FakeWizard.instances == []
        assert app.reporter.events == []

    def test_installed_helper_silence_locks_first(self, app, store,
                                                  monkeypatch, service_dir,
                                                  timers):
        _install_helper(service_dir)
        helper_ = SilentHelper(store).install(monkeypatch)
        app.do_activate()
        _fire(timers)
        assert _FakeWizard.instances == []
        (lock,) = _FakeLock.instances
        assert lock.config.pin_hash == HELPER_PIN_MARKER
        # Assumed, not written: a crash before the answer asks again.
        assert not _config_path().exists()
        # The PIN pad verifies through the helper as soon as it answers.
        assert _verify(lock.config, '000000')[0] is False
        assert _verify(lock.config, PIN)[0] is True
        helper_.answer()  # late HasPin: the PIN is real, keep it
        assert ShellConfig().pin_hash == HELPER_PIN_MARKER

    def test_installed_helper_silence_locks_the_shell(
            self, app, store, monkeypatch, service_dir, timers):
        _install_helper(service_dir)
        SilentHelper(store).install(monkeypatch)
        _config_path().write_text(json.dumps({'theme': 'ocean'}))
        app.do_activate()
        assert app.shells == []
        _fire(timers)
        assert app.shells == [HELPER_PIN_MARKER]

    def test_unreachable_installed_helper_locks_without_waiting(
            self, app, store, monkeypatch, service_dir, timers):
        _install_helper(service_dir)
        FakeHelper(store, reachable=False).install(monkeypatch)
        app.do_activate()
        assert timers == []
        (lock,) = _FakeLock.instances
        matched, _retry, error = _verify(lock.config, PIN)
        assert matched is False and error  # fail closed, no miss counted

    def test_late_no_pin_answer_drops_the_assumed_marker(
            self, app, store, monkeypatch, service_dir, timers):
        _install_helper(service_dir)
        store.clear_pin(UID, PIN)
        helper_ = SilentHelper(store).install(monkeypatch)
        app.do_activate()
        _fire(timers)
        (lock,) = _FakeLock.instances
        helper_.answer()
        # The lock that is up now opens with a swipe.
        assert lock.config.pin_hash is None
        assert ShellConfig().pin_hash is None

    def test_assumed_marker_clears_when_verify_finds_no_record(
            self, app, store, monkeypatch, service_dir, timers):
        _install_helper(service_dir)
        store.clear_pin(UID, PIN)
        SilentHelper(store).install(monkeypatch)
        app.do_activate()
        _fire(timers)
        (lock,) = _FakeLock.instances
        assert _verify(lock.config, 'anything') == (True, 0, None)
        assert ShellConfig().pin_hash is None

    def test_never_installed_helper_never_locks(self, app, store,
                                                monkeypatch, service_dir,
                                                timers):
        SilentHelper(store).install(monkeypatch)
        app.do_activate()
        _fire(timers)
        assert _FakeLock.instances == []
        assert len(_FakeWizard.instances) == 1

    def test_missing_helper_decides_at_once(self, app, store, monkeypatch,
                                            service_dir, timers):
        FakeHelper(store, reachable=False).install(monkeypatch)
        app.do_activate()
        assert timers == [] and _FakeLock.instances == []
        assert len(_FakeWizard.instances) == 1

    def test_helper_answering_after_an_uninstalled_guess_locks(
            self, app, store, monkeypatch, service_dir, timers):
        helper_ = SilentHelper(store).install(monkeypatch)
        _config_path().write_text(json.dumps({'theme': 'ocean'}))
        app.do_activate()
        _fire(timers)
        assert app.shells == [None]
        helper_.answer()
        assert app._shell.locks == [1]
        assert ShellConfig().pin_hash == HELPER_PIN_MARKER

    def test_pin_helper_off_never_waits(self, app, store, monkeypatch,
                                        service_dir, timers):
        _install_helper(service_dir)
        FakeHelper(store).install(monkeypatch)
        monkeypatch.setenv('XX_WM_PIN_HELPER', '0')
        app.do_activate()
        assert timers == [] and _FakeLock.instances == []

    def test_gate_crash_fails_closed(self, app, store, monkeypatch,
                                     service_dir):
        _install_helper(service_dir)

        def boom(*_a, **_k):
            raise RuntimeError('no bus')

        monkeypatch.setattr(helper_client, 'startup_pin_gate', boom)
        app.do_activate()
        assert len(_FakeLock.instances) == 1 and app.holds == 0

    def test_shell_failure_still_surfaces(self, app, store, monkeypatch):
        FakeHelper(store).install(monkeypatch)
        _config_path().write_text(json.dumps({'theme': 'ocean'}))

        def broken():
            raise RuntimeError('shell build failed')

        app._show_shell = broken
        with pytest.raises(RuntimeError, match='shell build failed'):
            app.do_activate()
        assert app.holds == 0

    def test_helper_installed_reads_the_activation_file(self, service_dir):
        assert helper_client.helper_installed() is False
        _install_helper(service_dir)
        assert helper_client.helper_installed() is True
        assert helper_client.helper_installed(dirs=()) is False


def _local_hash(pin: str) -> str:
    other = ShellConfig.__new__(ShellConfig)
    other.data = {}
    other.config_path = Path('/nonexistent/config.json')
    other.save = lambda: None
    other.set_pin(pin)
    return other.data['pin_hash']


class TestLocalHashAtStartup:
    """A local pbkdf2 hash in config at process start (written while the
    marker was lost) must not replace a PIN the helper holds."""

    LOCAL = '111111'

    @pytest.fixture(autouse=True)
    def _config_with_local_hash(self):
        _config_path().write_text(json.dumps(
            {'theme': 'ocean', 'pin_hash': _local_hash(self.LOCAL)}))

    def test_helper_pin_wins(self, app, store, monkeypatch, service_dir):
        _install_helper(service_dir)
        fake = FakeHelper(store).install(monkeypatch)
        app.do_activate()
        assert fake.calls[:1] == ['HasPin']
        assert app.shells == [HELPER_PIN_MARKER]
        config = ShellConfig()
        assert _verify(config, self.LOCAL)[0] is False
        assert _verify(config, PIN)[0] is True
        data = json.loads(_config_path().read_text(encoding='utf-8'))
        assert data['pin_hash'] == HELPER_PIN_MARKER

    def test_no_helper_record_keeps_the_local_pin(self, app, store,
                                                  monkeypatch, service_dir):
        _install_helper(service_dir)
        store.clear_pin(UID, PIN)
        FakeHelper(store).install(monkeypatch)
        app.do_activate()
        config = ShellConfig()
        assert config.pin_hash.startswith('pbkdf2$')
        assert _verify(config, '000000')[0] is False
        assert _verify(config, self.LOCAL)[0] is True

    def test_silent_installed_helper_verifies_through_the_helper(
            self, app, store, monkeypatch, service_dir, timers):
        _install_helper(service_dir)
        SilentHelper(store).install(monkeypatch)
        app.do_activate()
        assert app.shells == []
        _fire(timers)
        assert app.shells == [HELPER_PIN_MARKER]
        config = ShellConfig()
        assert _verify(config, self.LOCAL)[0] is False
        assert _verify(config, PIN)[0] is True

    def test_silent_helper_without_a_record_falls_back_to_local(
            self, app, store, monkeypatch, service_dir, timers):
        _install_helper(service_dir)
        store.clear_pin(UID, PIN)
        SilentHelper(store).install(monkeypatch)
        app.do_activate()
        _fire(timers)
        config = ShellConfig()
        # Never "any PIN": the local hash stands once the helper says
        # it holds none.
        assert _verify(config, '000000')[0] is False
        assert _verify(config, self.LOCAL)[0] is True

    def test_late_answer_replaces_the_local_hash(self, app, store,
                                                 monkeypatch, service_dir,
                                                 timers):
        _install_helper(service_dir)
        helper_ = SilentHelper(store).install(monkeypatch)
        app.do_activate()
        _fire(timers)
        helper_.answer()
        data = json.loads(_config_path().read_text(encoding='utf-8'))
        assert data['pin_hash'] == HELPER_PIN_MARKER

    def test_late_no_pin_answer_keeps_the_local_hash(self, app, store,
                                                     monkeypatch, service_dir,
                                                     timers):
        _install_helper(service_dir)
        store.clear_pin(UID, PIN)
        helper_ = SilentHelper(store).install(monkeypatch)
        app.do_activate()
        _fire(timers)
        helper_.answer()
        config = ShellConfig()
        assert config.pin_hash.startswith('pbkdf2$')
        assert _verify(config, self.LOCAL)[0] is True

    def test_silent_uninstalled_helper_keeps_the_local_pin(
            self, app, store, monkeypatch, service_dir, timers):
        SilentHelper(store).install(monkeypatch)
        app.do_activate()
        _fire(timers)
        assert app.shells and app.shells[0].startswith('pbkdf2$')

    def test_pin_helper_off_never_asks(self, app, store, monkeypatch,
                                       service_dir, timers):
        _install_helper(service_dir)
        fake = FakeHelper(store).install(monkeypatch)
        monkeypatch.setenv('XX_WM_PIN_HELPER', '0')
        app.do_activate()
        assert fake.calls == [] and timers == []
        assert app.shells[0].startswith('pbkdf2$')


@pytest.mark.usefixtures('_no_window_leak')
class TestWindowGateFailsClosed:
    """The shell window's own gate (no app gate ran) behaves the same."""

    def _run(self, monkeypatch):
        window = _window()
        ls = _lock_screen()
        reporter = _Reporter()
        monkeypatch.setattr(ls, '_shell_state', lambda: reporter)
        session = SimpleNamespace(set_on_lock=lambda cb: None)
        monkeypatch.setattr(ls, 'logind_session', lambda: session)
        shown = []
        shell = SimpleNamespace(config=ShellConfig(),
                                _show_lock_screen=lambda: shown.append(1))
        shell._lock_at_startup = lambda rep: (
            window.ShellWindow._lock_at_startup(shell, rep))
        window.ShellWindow._startup_lock_gate(shell)
        return shell, shown, reporter

    def test_silent_installed_helper_locks(self, store, monkeypatch,
                                           service_dir, timers):
        _install_helper(service_dir)
        SilentHelper(store).install(monkeypatch)
        shell, shown, reporter = self._run(monkeypatch)
        assert shown == [] and reporter.events == []  # shield stays up
        _fire(timers)
        assert shown == [1]
        assert reporter.events == [('require_lock', True), 'ready']
        assert _verify(shell.config, PIN)[0] is True

    def test_silent_uninstalled_helper_reports_ready(self, store, monkeypatch,
                                                     service_dir, timers):
        SilentHelper(store).install(monkeypatch)
        _shell, shown, reporter = self._run(monkeypatch)
        _fire(timers)
        assert shown == [] and reporter.events == ['ready']

    def test_app_gate_already_found_no_pin(self, store, monkeypatch):
        helper_ = FakeHelper(store).install(monkeypatch)
        window = _window()
        ls = _lock_screen()
        reporter = _Reporter()
        monkeypatch.setattr(ls, '_shell_state', lambda: reporter)
        monkeypatch.setattr(ls, 'logind_session', lambda: SimpleNamespace(
            set_on_lock=lambda cb: None))
        shell = SimpleNamespace(
            config=SimpleNamespace(pin_hash=None),
            get_application=lambda: SimpleNamespace(_pin_gate_done=True))
        window.ShellWindow._startup_lock_gate(shell)
        assert reporter.events == ['ready'] and helper_.calls == []


# ------------------------------------------------------- config overlay

class TestAssumedMarker:
    def test_corrupt_config_refuses_saves_but_keeps_the_marker(self):
        _config_path().write_bytes(b'{oops')
        first = ShellConfig()
        assert first.load_failed
        first.assume_pin_in_helper()
        assert ShellConfig().pin_hash == HELPER_PIN_MARKER
        assert ShellConfig().pin_in_helper
        assert ShellConfig().verify_pin(PIN) is False  # sync path closed

    def test_clear_pin_drops_it_everywhere(self):
        a = ShellConfig()
        a.assume_pin_in_helper()
        b = ShellConfig()
        a.clear_pin()
        assert b.pin_hash is None and ShellConfig().pin_hash is None


# ----------------------------------------------- hot reload (window.py)

class _Revealer:
    def __init__(self):
        self.revealed = False

    def set_reveal_child(self, value):
        self.revealed = value


class _LockHarness:
    def __init__(self, config):
        self._config = config
        self.unlocked = 0
        self._pin_revealer = _Revealer()
        self._swipe_hint = SimpleNamespace(set_visible=lambda _v: None)

    def _unlock(self):
        self.unlocked += 1

    def _smart_lock_requires_pin(self):
        return True

    def _set_clock_compact(self, _v):
        pass

    def _fit_keypad_to_screen(self):
        pass


@pytest.mark.usefixtures('_no_window_leak')
class TestHotReloadKeepsThePin:
    """Hole 3: a valid config.json without the marker, hot-reloaded."""

    @pytest.fixture()
    def shell(self, store, monkeypatch):
        FakeHelper(store).install(monkeypatch)
        _config_path().write_text(json.dumps(
            {'theme': 'ocean', 'pin_hash': HELPER_PIN_MARKER}))
        shell = SimpleNamespace(config=ShellConfig(), changes=[],
                                _config_reload_timer=None,
                                _reload_gestures=lambda: None)
        shell._apply_config_change = lambda: shell.changes.append(1)
        return shell

    def _reload(self, shell, data):
        _config_path().write_text(json.dumps(data))
        # Other suites leave a window module bound to fake gi; reload
        # against the real one (_no_window_leak restores theirs).
        sys.modules.pop('window', None)
        _window().ShellWindow._reload_config(shell)

    def _assert_pin_still_guards(self, shell):
        lock = _LockHarness(shell.config)
        _lock_screen().LockScreen._swipe_up(lock)
        assert lock.unlocked == 0 and lock._pin_revealer.revealed
        assert _verify(shell.config, '000000')[0] is False
        assert _verify(shell.config, PIN)[0] is True

    def test_marker_dropped_from_the_file(self, shell):
        self._reload(shell, {'theme': 'graphite'})
        assert shell.changes == [1]  # the edit itself applied
        assert shell.config.data['theme'] == 'graphite'
        self._assert_pin_still_guards(shell)

    def test_marker_set_to_null(self, shell):
        self._reload(shell, {'theme': 'graphite', 'pin_hash': None})
        self._assert_pin_still_guards(shell)

    def test_two_reloads_in_a_row(self, shell):
        self._reload(shell, {'theme': 'graphite'})
        self._reload(shell, {'theme': 'ocean'})
        self._assert_pin_still_guards(shell)

    def test_local_hash_swapped_in_does_not_replace_the_pin(self, shell):
        self._reload(shell, {'theme': 'graphite',
                             'pin_hash': _local_hash('111111')})
        assert _verify(shell.config, '111111')[0] is False
        self._assert_pin_still_guards(shell)


# --------------------------- river: no second PIN right after setup

@pytest.mark.usefixtures('_no_window_leak')
class TestNoSecondLockAfterSetup:
    """A wizard resumed behind the lock: the PIN was just entered, so the
    shell built when it finishes does not lock again on river; anything
    else still locks."""

    @pytest.fixture()
    def river(self, app, store, monkeypatch):
        import main
        import session_lock
        FakeHelper(store).install(monkeypatch)
        monkeypatch.setattr(session_lock, 'backend',
                            lambda: session_lock.SESSION_LOCK)
        window = _window()
        for name in ('take_setup_unlock', '_wizard_done'):
            setattr(app, name, types.MethodType(
                getattr(main.XXWMApplication, name), app))
        app._pin_proven_at = None
        app._setup_finishing = False
        app.early_locks = []

        def show_shell():
            shell = SimpleNamespace(
                config=ShellConfig(), get_application=lambda: app,
                _show_lock_screen=lambda: app.early_locks.append(1))
            window.ShellWindow._lock_before_first_frame(shell)
            app._shell = shell

        app._show_shell = show_shell
        app.clock = [1000.0]
        monkeypatch.setattr(main.time, 'monotonic', lambda: app.clock[0])
        return app

    def _setup_behind_the_lock(self, app):
        app.do_activate()
        (lock,) = _FakeLock.instances
        lock.on_unlock()          # the PIN, entered on the resumed lock
        assert len(_FakeWizard.instances) == 1

    def test_shell_after_the_unlocked_wizard_does_not_lock_again(self, river):
        self._setup_behind_the_lock(river)
        river.clock[0] += 90
        river._wizard_done()
        assert river.early_locks == []

    def test_slow_setup_locks_again(self, river):
        import main
        self._setup_behind_the_lock(river)
        river.clock[0] += main.SETUP_UNLOCK_GRACE_S + 1
        river._wizard_done()
        assert river.early_locks == [1]

    def test_wizard_without_a_lock_first_still_locks(self, river, store):
        store.clear_pin(UID, PIN)
        river.do_activate()
        assert _FakeLock.instances == []
        ShellConfig().set_pin_in_helper()  # PIN set during setup
        river._wizard_done()
        assert river.early_locks == [1]

    def test_the_skip_is_one_shot_and_only_at_setup_end(self, river):
        self._setup_behind_the_lock(river)
        assert river.take_setup_unlock() is False  # not finishing setup
        river._setup_finishing = True
        assert river.take_setup_unlock() is False  # consumed above

    def test_restart_with_a_pin_locks(self, river):
        _config_path().write_text(json.dumps(
            {'theme': 'ocean', 'pin_hash': HELPER_PIN_MARKER}))
        river.do_activate()
        assert river.early_locks == [1]
