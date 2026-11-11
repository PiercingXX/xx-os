"""Lock-screen hardening: root-side PIN store, migration/fallback, phoc
shell mode (-S guard + set_shell_state sequencing) and logind lock state.

Nothing here needs root, a compositor, a bus or pywayland: the helper's
PinStore runs on a tmp dir with a fake clock and a cheap scrypt cost,
the bus/uid lookups and helper calls are scripted fakes, the session
script runs against fake phoc/python3/gsettings, and the wayland
reporter gets a fake display/registry.
"""
import os
import stat
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'launcher' / 'helper'))
sys.path.insert(0, str(ROOT / 'launcher' / 'src'))

import xx_wm_helper as helper  # noqa: E402
import helper_client  # noqa: E402
import wayland_bindings  # noqa: E402
from lock_lines import _lockout_secs  # noqa: E402

UID = 1000
DATA = ROOT / 'launcher' / 'data'


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture()
def clock():
    return Clock()


@pytest.fixture()
def store(tmp_path, clock):
    return helper.PinStore(root=str(tmp_path / 'pin'), now=clock,
                           scrypt_n=2 ** 4)


# --------------------------------------------------------------- PinStore

class TestPinStore:
    def test_set_verify_has_clear(self, store):
        assert store.has_pin(UID) is False
        assert store.verify_pin(UID, '1234') == (False, 0, False)
        assert store.set_pin(UID, '', '123456') == (True, 0)
        assert store.has_pin(UID) is True
        assert store.verify_pin(UID, '123456') == (True, 0, True)
        assert store.verify_pin(UID, '000000')[0] is False
        assert store.clear_pin(UID, '123456') == (True, 0)
        assert store.has_pin(UID) is False

    def test_record_is_root_private_and_holds_no_plaintext(self, store):
        store.set_pin(UID, '', '246810')
        path = Path(store.record_path(UID))
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        text = path.read_text()
        assert '246810' not in text
        assert '"hash": "scrypt$' in text or '"hash": "pbkdf2$' in text

    def test_records_are_per_uid(self, store):
        store.set_pin(UID, '', '111111')
        assert store.has_pin(UID + 1) is False
        assert store.verify_pin(UID + 1, '111111') == (False, 0, False)
        # Another uid sets its own record without knowing UID's PIN.
        assert store.set_pin(UID + 1, '', '222222') == (True, 0)
        assert store.verify_pin(UID, '111111')[0] is True

    def test_change_requires_old_pin(self, store):
        store.set_pin(UID, '', '111111')
        assert store.set_pin(UID, '999999', '222222') == (False, 0)
        assert store.verify_pin(UID, '111111')[0] is True
        assert store.set_pin(UID, '111111', '222222') == (True, 0)
        assert store.verify_pin(UID, '222222')[0] is True

    def test_clear_requires_old_pin(self, store):
        store.set_pin(UID, '', '111111')
        assert store.clear_pin(UID, 'nope')[0] is False
        assert store.has_pin(UID) is True

    def test_invalid_new_pin_rejected(self, store):
        for bad in ('', 'x' * (helper.PIN_MAX_LEN + 1), 'a\x00b'):
            with pytest.raises(helper.HelperError):
                store.set_pin(UID, '', bad)

    def test_lockout_escalates_like_the_shell(self, store, clock):
        store.set_pin(UID, '', '123456')
        retries = []
        for _ in range(7):
            matched, retry, _has = store.verify_pin(UID, '000000')
            assert matched is False
            retries.append(retry)
            clock.now += retry + 1  # wait out each lockout
        assert retries == [_lockout_secs(n) for n in range(1, 8)]

    def test_lockout_refuses_without_running_kdf(self, store, clock,
                                                 monkeypatch):
        store.set_pin(UID, '', '123456')
        for _ in range(helper.PIN_FAIL_THRESHOLD):
            store.verify_pin(UID, '000000')

        def explosive(*_a, **_k):
            raise AssertionError('KDF must not run while locked out')

        monkeypatch.setattr(helper.PinStore, 'check_hash',
                            staticmethod(explosive))
        matched, retry, has = store.verify_pin(UID, '123456')
        assert (matched, has) == (False, True)
        assert 0 < retry <= _lockout_secs(helper.PIN_FAIL_THRESHOLD)
        # SetPin/ClearPin share the same gate.
        assert store.set_pin(UID, '123456', '654321')[0] is False
        assert store.clear_pin(UID, '123456')[0] is False

    def test_lockout_persists_across_helper_restarts(self, store, clock,
                                                     tmp_path):
        store.set_pin(UID, '', '123456')
        for _ in range(helper.PIN_FAIL_THRESHOLD):
            store.verify_pin(UID, '000000')
        fresh = helper.PinStore(root=store.root, now=clock, scrypt_n=2 ** 4)
        assert fresh.verify_pin(UID, '123456')[1] > 0

    def test_success_resets_counter(self, store, clock):
        store.set_pin(UID, '', '123456')
        for _ in range(helper.PIN_FAIL_THRESHOLD - 1):
            store.verify_pin(UID, '000000')
        assert store.verify_pin(UID, '123456')[0] is True
        # Fresh budget: one more miss is not a lockout.
        assert store.verify_pin(UID, '000000')[1] == 0

    def test_clock_jump_back_clears_running_lockout(self, store, clock):
        store.set_pin(UID, '', '123456')
        for _ in range(helper.PIN_FAIL_THRESHOLD):
            store.verify_pin(UID, '000000')
        clock.now -= 10 * 365 * 86400  # RTC reset
        assert store.verify_pin(UID, '123456')[0] is True

    def test_far_future_deadline_capped(self, store, clock):
        store.set_pin(UID, '', '123456')
        for _ in range(helper.PIN_FAIL_THRESHOLD):
            store.verify_pin(UID, '000000')
        record = store._load(UID)
        record['until'] = clock.now + 50 * 365 * 86400
        store._save(UID, record)
        assert store.verify_pin(UID, '123456')[1] <= _lockout_secs(
            helper.PIN_FAIL_THRESHOLD)

    def test_corrupt_record_fails_closed(self, store):
        store.set_pin(UID, '', '123456')
        Path(store.record_path(UID)).write_text('{not json')
        assert store.has_pin(UID) is True
        assert store.verify_pin(UID, '123456')[0] is False

    def test_hash_formats_verify(self, store):
        assert store.check_hash('42', store.hash_pin('42'))
        legacy = helper.PinStore(root=store.root, scrypt_n=2 ** 4,
                                 pbkdf2_iterations=1000)
        digest = legacy.hash_pin('42')
        assert legacy.check_hash('42', digest)
        assert not store.check_hash('43', digest)
        assert not store.check_hash('42', 'garbage$1$2')

    def test_lockout_table_matches_lock_lines(self):
        for n in range(0, 40):
            assert helper.pin_lockout_secs(n) == _lockout_secs(n)


# ------------------------------------------------------ service dispatch

class FakeInvocation:
    def __init__(self):
        self.value = None
        self.error = None

    def return_value(self, value):
        self.value = value

    def return_dbus_error(self, name, message):
        self.error = (name, message)


def _service(store, uid=UID, seen=None):
    def get_uid(conn, sender, callback):
        if seen is not None:
            seen.append(sender)
        callback(uid, None)

    def no_polkit(*_a, **_k):
        raise AssertionError('PIN methods must not go through polkit')

    return helper.HelperService(connection=object(), core=object(),
                                authorize=no_polkit, pin_store=store,
                                get_uid=get_uid)


def _call(service, method, args):
    pytest.importorskip('gi')
    invocation = FakeInvocation()
    params = SimpleNamespace(unpack=lambda: args)
    service._on_method_call(service.connection, ':1.42', '/p', 'i',
                            method, params, invocation)
    return invocation


class TestPinDbus:
    def test_round_trip_scoped_to_bus_uid(self, store):
        seen = []
        service = _service(store, seen=seen)
        assert _call(service, 'HasPin', ()).value.unpack() == (False,)
        inv = _call(service, 'SetPin', ('', '123456'))
        assert inv.error is None and inv.value.unpack() == (True, 0)
        assert _call(service, 'VerifyPin', ('123456',)).value.unpack() == (
            True, 0, True)
        assert seen and all(s == ':1.42' for s in seen)
        # The same calls from another uid see nothing of UID's record.
        other = _service(store, uid=UID + 7)
        assert _call(other, 'HasPin', ()).value.unpack() == (False,)
        assert _call(other, 'VerifyPin', ('123456',)).value.unpack() == (
            False, 0, False)

    def test_bad_args_rejected_before_uid_lookup(self, store):
        def explosive(*_a):
            raise AssertionError('no uid lookup for malformed calls')

        service = helper.HelperService(connection=object(), core=object(),
                                       pin_store=store, get_uid=explosive)
        for method, args in [('VerifyPin', ()), ('SetPin', ('1',)),
                             ('VerifyPin', (5,)), ('HasPin', ('x',))]:
            inv = _call(service, method, args)
            assert inv.error[0] == helper.ERROR_INVALID_ARGS

    def test_uid_lookup_failure_is_not_authorized(self, store):
        service = helper.HelperService(
            connection=object(), core=object(), pin_store=store,
            get_uid=lambda c, s, cb: cb(None, 'gone'))
        inv = _call(service, 'HasPin', ())
        assert inv.error[0] == helper.ERROR_NOT_AUTHORIZED

    def test_pin_text_never_in_errors(self, store):
        service = _service(store)
        inv = _call(service, 'SetPin', ('', 'x' * 100))
        assert inv.error is not None
        assert 'x' * 10 not in inv.error[1]

    def test_introspection_declares_pin_methods(self):
        root = ET.fromstring(helper.INTROSPECT_XML)
        names = {m.get('name') for m in root.iter('method')}
        assert set(helper.PIN_METHODS) <= names


# --------------------------------------------- client: migration/fallback

class FakeConfig:
    """Duck-typed ShellConfig over the real PIN helpers."""

    def __init__(self, tmp_path):
        import config as _config
        cfg = _config.ShellConfig.__new__(_config.ShellConfig)
        cfg.data = dict(_config.DEFAULT_CONFIG)
        cfg.config_dir = tmp_path / 'cfg'
        cfg.config_path = cfg.config_dir / 'config.json'
        self.cfg = cfg


@pytest.fixture()
def cfg(tmp_path):
    return FakeConfig(tmp_path).cfg


class FakeHelper:
    """Scripted helper_client transport backed by a real PinStore."""

    def __init__(self, store, reachable=True):
        self.store = store
        self.reachable = reachable
        self.calls = []

    def install(self, monkeypatch):
        monkeypatch.setattr(helper_client, 'set_pin', self.set_pin)
        monkeypatch.setattr(helper_client, 'verify_pin', self.verify_pin)
        monkeypatch.setattr(helper_client, 'has_pin', self.has_pin)
        monkeypatch.setenv('XX_WM_PIN_HELPER', '1')
        helper_client._pin_logged.clear()
        return self

    def _down(self, on_done):
        on_done(False, helper_client.HELPER_NOT_INSTALLED)

    def set_pin(self, old, new, on_done):
        self.calls.append('SetPin')
        if not self.reachable:
            return self._down(on_done)
        on_done(True, self.store.set_pin(UID, old, new))

    def verify_pin(self, pin, on_done):
        self.calls.append('VerifyPin')
        if not self.reachable:
            return self._down(on_done)
        on_done(True, self.store.verify_pin(UID, pin))

    def has_pin(self, on_done):
        self.calls.append('HasPin')
        if not self.reachable:
            return self._down(on_done)
        on_done(True, self.store.has_pin(UID))


def _verify(cfg, pin):
    out = []
    helper_client.pin_verify(cfg, pin, lambda *a: out.append(a))
    assert len(out) == 1
    return out[0]


class TestMigrationAndFallback:
    def test_successful_unlock_migrates_and_deletes_config_hash(
            self, cfg, store, monkeypatch):
        fake = FakeHelper(store).install(monkeypatch)
        cfg.set_pin('123456')
        assert cfg.pin_hash.startswith('pbkdf2$')
        assert _verify(cfg, '123456') == (True, 0, None)
        assert cfg.pin_in_helper
        assert 'pbkdf2' not in cfg.config_path.read_text()
        assert store.verify_pin(UID, '123456')[0] is True
        # From now on the helper verifies.
        fake.calls.clear()
        assert _verify(cfg, '123456') == (True, 0, None)
        assert _verify(cfg, '000000') == (False, 0, None)
        assert fake.calls == ['VerifyPin', 'VerifyPin']

    def test_wrong_pin_never_migrates(self, cfg, store, monkeypatch):
        fake = FakeHelper(store).install(monkeypatch)
        cfg.set_pin('123456')
        assert _verify(cfg, '000000')[0] is False
        assert not cfg.pin_in_helper
        assert fake.calls == []

    def test_helper_missing_keeps_config_and_logs_once(
            self, cfg, store, monkeypatch, caplog):
        FakeHelper(store, reachable=False).install(monkeypatch)
        cfg.set_pin('123456')
        with caplog.at_level('INFO', logger='helper_client'):
            for _ in range(3):
                assert _verify(cfg, '123456') == (True, 0, None)
        assert cfg.pin_hash.startswith('pbkdf2$')
        notes = [r for r in caplog.records if 'not installed' in r.message]
        assert len(notes) == 1
        assert all('123456' not in r.getMessage() for r in caplog.records)

    def test_stale_helper_record_with_other_pin_keeps_config(
            self, cfg, store, monkeypatch):
        FakeHelper(store).install(monkeypatch)
        store.set_pin(UID, '', '999999')
        cfg.set_pin('123456')
        assert _verify(cfg, '123456')[0] is True
        assert cfg.pin_hash.startswith('pbkdf2$')

    def test_matching_helper_record_is_adopted(self, cfg, store,
                                               monkeypatch):
        FakeHelper(store).install(monkeypatch)
        store.set_pin(UID, '', '123456')
        cfg.set_pin('123456')
        assert _verify(cfg, '123456')[0] is True
        assert cfg.pin_in_helper

    def test_disabled_outside_session(self, cfg, store, monkeypatch):
        fake = FakeHelper(store).install(monkeypatch)
        monkeypatch.delenv('XX_WM_PIN_HELPER')
        monkeypatch.delenv('XX_WM_SESSION', raising=False)
        cfg.set_pin('123456')
        assert _verify(cfg, '123456')[0] is True
        assert fake.calls == [] and not cfg.pin_in_helper

    def test_enabled_flag(self):
        assert helper_client.pin_helper_enabled({'XX_WM_SESSION': '1'})
        assert not helper_client.pin_helper_enabled({})
        assert not helper_client.pin_helper_enabled(
            {'XX_WM_SESSION': '1', 'XX_WM_PIN_HELPER': '0'})
        assert helper_client.pin_helper_enabled({'XX_WM_PIN_HELPER': '1'})

    def test_marker_fails_closed_when_helper_unreachable(
            self, cfg, store, monkeypatch):
        fake = FakeHelper(store).install(monkeypatch)
        cfg.set_pin_in_helper()
        store.set_pin(UID, '', '123456')
        fake.reachable = False
        matched, _retry, error = _verify(cfg, '123456')
        assert matched is False and error
        assert cfg.verify_pin('123456') is False  # sync path fails closed

    def test_marker_lockout_retry_after_reported(self, cfg, store,
                                                 monkeypatch):
        FakeHelper(store).install(monkeypatch)
        cfg.set_pin_in_helper()
        store.set_pin(UID, '', '123456')
        results = [_verify(cfg, '000000') for _ in range(
            helper.PIN_FAIL_THRESHOLD)]
        assert results[-1][1] == _lockout_secs(helper.PIN_FAIL_THRESHOLD)

    def test_marker_without_helper_record_is_stale(self, cfg, store,
                                                   monkeypatch):
        FakeHelper(store).install(monkeypatch)
        cfg.set_pin_in_helper()
        assert _verify(cfg, 'anything') == (True, 0, None)
        assert cfg.pin_hash is None

    def test_pin_change_marker_goes_through_helper(self, cfg, store,
                                                   monkeypatch):
        FakeHelper(store).install(monkeypatch)
        cfg.set_pin_in_helper()
        store.set_pin(UID, '', '111111')
        out = []
        helper_client.pin_change(cfg, '000000', '222222',
                                 lambda *a: out.append(a))
        assert out == [(False, 0, None)]
        helper_client.pin_change(cfg, '111111', '222222',
                                 lambda *a: out.append(a))
        assert out[-1] == (True, 0, None)
        assert store.verify_pin(UID, '222222')[0] is True

    def test_first_pin_lands_in_helper(self, cfg, store, monkeypatch):
        FakeHelper(store).install(monkeypatch)
        out = []
        helper_client.pin_change(cfg, '', '123456', lambda *a: out.append(a))
        assert out == [(True, 0, None)]
        assert cfg.pin_in_helper and store.has_pin(UID)

    def test_pin_sync_restores_missing_marker(self, cfg, store,
                                              monkeypatch):
        FakeHelper(store).install(monkeypatch)
        store.set_pin(UID, '', '123456')
        out = []
        helper_client.pin_sync(cfg, out.append)
        assert out == [True] and cfg.pin_in_helper


class TestSettingsChangePin:
    def test_marker_change_is_async(self, cfg, store, monkeypatch):
        import settings_lock
        FakeHelper(store).install(monkeypatch)
        cfg.set_pin_in_helper()
        store.set_pin(UID, '', '111111')
        later = []
        ok, msg = settings_lock.change_pin(cfg, '111111', '222222', '222222',
                                           on_async=lambda *a: later.append(a))
        assert ok is None and msg
        assert later == [(True, 'PIN changed.')]
        ok, _msg = settings_lock.change_pin(cfg, '111111', '3', '4',
                                            on_async=later.append)
        assert ok is False  # validation stays synchronous

    def test_local_change_still_sync(self, cfg, monkeypatch):
        import settings_lock
        monkeypatch.setenv('XX_WM_PIN_HELPER', '0')
        cfg.set_pin('1234')
        ok, _msg = settings_lock.change_pin(cfg, '1234', '5678', '5678')
        assert ok is True and cfg.verify_pin('5678')


# -------------------------------------------------- lock screen verify path

class _Label:
    def __init__(self):
        self.text = ''

    def set_text(self, text):
        self.text = text


class _Harness:
    def __init__(self, config):
        self._config = config
        self._pin = ''
        self._fail_count = 0
        self._lockout_src = None
        self._open_shade_after_unlock = False
        self.dots_label = _Label()
        self.error_label = _Label()
        self.lockout_label = _Label()
        self.unlocked = 0
        self.lockouts = []

    def _on_unlock(self):
        self.unlocked += 1

    def set_visible(self, _v):
        pass

    def _refresh_dots(self):
        self.dots_label.set_text('●' * len(self._pin))
        self.error_label.set_text('')

    def _shake(self):
        pass

    def _unlock(self):
        self.unlocked += 1

    def _persist_lockout_state(self, until):
        pass

    def _start_lockout(self, secs):
        self.lockouts.append(secs)
        self._lockout_src = 1


def _lock_screen():
    return pytest.importorskip('lock_screen')


class TestLockScreenVerify:
    def test_helper_retry_after_drives_lockout(self, cfg, monkeypatch):
        ls = _lock_screen()
        cfg.set_pin_in_helper()
        monkeypatch.setattr(helper_client, 'verify_pin',
                            lambda pin, cb: cb(True, (False, 120, True)))
        lock = _Harness(cfg)
        lock._pin = '000000'
        ls.LockScreen._check_pin(lock)
        assert lock.unlocked == 0
        assert lock.lockouts == [120]

    def test_helper_unreachable_does_not_count_a_miss(self, cfg,
                                                      monkeypatch):
        ls = _lock_screen()
        cfg.set_pin_in_helper()
        monkeypatch.setattr(helper_client, 'verify_pin', lambda pin, cb: cb(
            False, helper_client.HELPER_NOT_INSTALLED))
        lock = _Harness(cfg)
        lock._pin = '123456'
        ls.LockScreen._check_pin(lock)
        assert lock.unlocked == 0 and lock._fail_count == 0
        assert 'try again' in lock.error_label.text

    def test_input_ignored_while_verifying(self, cfg, monkeypatch):
        ls = _lock_screen()
        cfg.set_pin_in_helper()
        pending = []
        monkeypatch.setattr(helper_client, 'verify_pin',
                            lambda pin, cb: pending.append(cb))
        lock = _Harness(cfg)
        lock._pin = '123456'
        ls.LockScreen._check_pin(lock)
        ls.LockScreen._check_pin(lock)
        assert len(pending) == 1
        pending[0](True, (True, 0, True))
        assert lock.unlocked == 1


# ------------------------------------------------------------ logind

class _Result:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error


class _Reply:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return self._value


class FakeLogindBus:
    def __init__(self, sessions):
        self.sessions = sessions  # (method, arg) -> path
        self.calls = []
        self.subs = []

    def call(self, bus, path, iface, method, params, _rt, _flags, _t,
             _cancel, callback):
        args = params.unpack()
        self.calls.append((path, method, args))
        if method == 'SetLockedHint':
            callback(self, _Result(()))
            return
        path_out = self.sessions.get((method, args[0]))
        callback(self, _Result((path_out,) if path_out else None,
                               None if path_out else 'NoSuchSession'))

    def call_finish(self, result):
        if result.error:
            raise RuntimeError(result.error)
        return _Reply(result.value)

    def signal_subscribe(self, sender, iface, member, path, arg0, flags, cb):
        self.subs.append((iface, member, path, cb))
        return len(self.subs)


class TestLogind:
    def test_resolves_session_and_flushes_pending_hint(self, monkeypatch):
        ls = _lock_screen()
        monkeypatch.delenv('XDG_SESSION_ID', raising=False)
        bus = FakeLogindBus({('GetSessionByPID', os.getpid()):
                             '/org/freedesktop/login1/session/c1'})
        holder = {}
        session = ls.LogindSession(bus_getter=lambda cb: holder.setdefault(
            'cb', cb))
        session.set_locked_hint(True)  # before the path resolves
        holder['cb'](bus)
        assert session.path.endswith('/c1')
        hints = [c for c in bus.calls if c[1] == 'SetLockedHint']
        assert hints == [('/org/freedesktop/login1/session/c1',
                          'SetLockedHint', (True,))]
        session.set_locked_hint(False)
        assert bus.calls[-1][2] == (False,)

    def test_lock_signal_shows_lock_unlock_not_subscribed(self,
                                                          monkeypatch):
        ls = _lock_screen()
        monkeypatch.setenv('XDG_SESSION_ID', '7')
        bus = FakeLogindBus({('GetSession', '7'): '/s/7'})
        session = ls.LogindSession(bus_getter=lambda cb: cb(bus))
        shown = []
        session.set_on_lock(lambda: shown.append(1))
        members = {(s[0], s[1]) for s in bus.subs}
        assert members == {(ls.LOGIND_SESSION, 'Lock')}
        bus.subs[0][3]()
        assert shown == [1]

    def test_no_session_is_silent(self):
        ls = _lock_screen()
        bus = FakeLogindBus({})
        session = ls.LogindSession(bus_getter=lambda cb: cb(bus))
        session.set_locked_hint(True)
        assert session.path is None
        assert not [c for c in bus.calls if c[1] == 'SetLockedHint']


# ---------------------------------------------------- phosh_private / -S

class FakeProxy:
    def __init__(self):
        self.states = []

    def set_shell_state(self, state):
        self.states.append(state)


class FakeRegistry:
    def __init__(self, globals_):
        self.globals = globals_
        self.dispatcher = {}
        self.bound = []

    def bind(self, name, cls, version):
        self.bound.append((name, cls, version))
        return self.proxy


class FakeDisplay:
    def __init__(self, globals_, fail_bind=False):
        self.registry = FakeRegistry(globals_)
        self.registry.proxy = FakeProxy()
        self.fail_bind = fail_bind
        self.flushed = 0
        self.disconnected = False
        self.trips = 0

    def connect(self):
        pass

    def get_registry(self):
        return self.registry

    def roundtrip(self):
        self.trips += 1
        if self.trips == 1:
            for name, (iface, ver) in self.registry.globals.items():
                self.registry.dispatcher['global'](self.registry, name,
                                                   iface, ver)
            return 0
        return -1 if self.fail_bind else 0

    def flush(self):
        self.flushed += 1

    def disconnect(self):
        self.disconnected = True


def _reporter(display, pin=False, watchdog=60.0):
    return wayland_bindings.ShellStateReporter(
        bind_fn=lambda: {'PhoshPrivate': object},
        display_factory=lambda: display, start_thread=False,
        pin_configured=lambda: pin, watchdog_seconds=watchdog)


def _run_bg(reporter):
    thread = threading.Thread(target=reporter._run, daemon=True)
    thread.start()
    return thread


PHOSH = {5: ('wl_output', 4), 9: ('phosh_private', 8)}


class TestShellState:
    def test_gate_rules(self):
        gate = wayland_bindings.ShellStateGate()
        assert not gate.ready_to_report()
        gate.lock_required = True
        gate.shell_ready = True
        assert not gate.ready_to_report()
        gate.lock_mapped = True
        assert gate.ready_to_report()
        gate.reported = True
        assert not gate.ready_to_report()

    def test_no_pin_reports_up_when_shell_ready(self):
        display = FakeDisplay(dict(PHOSH))
        reporter = _reporter(display)
        thread = _run_bg(reporter)
        reporter.note_shell_ready()
        thread.join(5)
        assert display.registry.proxy.states == [
            wayland_bindings.SHELL_STATE_UP]
        assert reporter.sent and display.flushed == 1
        assert display.registry.bound[0][2] == 8

    def test_pin_waits_for_lock_mapped(self):
        display = FakeDisplay(dict(PHOSH))
        reporter = _reporter(display)
        reporter.require_lock(True)
        reporter.note_shell_ready()
        thread = _run_bg(reporter)
        thread.join(0.3)
        assert thread.is_alive()
        assert display.registry.proxy.states == []
        reporter.note_lock_mapped()
        thread.join(5)
        assert display.registry.proxy.states == [
            wayland_bindings.SHELL_STATE_UP]

    def test_lock_mapped_before_ready_still_needs_ready(self):
        reporter = _reporter(FakeDisplay(dict(PHOSH)))
        reporter.require_lock(True)
        reporter.note_lock_mapped()
        assert not reporter._up.is_set()
        reporter.note_shell_ready()
        assert reporter._up.is_set()

    def test_absent_global_does_nothing(self):
        display = FakeDisplay({5: ('wl_output', 4)})
        reporter = _reporter(display)
        reporter.note_shell_ready()
        reporter._run()
        assert reporter.available is False and not reporter.sent
        assert display.disconnected

    def test_old_version_does_nothing(self):
        display = FakeDisplay({9: ('phosh_private', 5)})
        reporter = _reporter(display)
        reporter.note_shell_ready()
        reporter._run()
        assert reporter.available is False
        assert display.registry.bound == []

    def test_refused_bind_degrades(self):
        display = FakeDisplay(dict(PHOSH), fail_bind=True)
        reporter = _reporter(display)
        reporter.note_shell_ready()
        reporter._run()
        assert reporter.available is False and not reporter.sent

    def test_no_bindings_degrades(self):
        reporter = wayland_bindings.ShellStateReporter(
            bind_fn=lambda: None, start_thread=False)
        reporter._run()
        assert reporter.available is False

    def test_watchdog_lowers_shield_only_without_pin(self):
        display = FakeDisplay(dict(PHOSH))
        reporter = _reporter(display, pin=False, watchdog=0.05)
        _run_bg(reporter).join(5)
        assert reporter.sent

        display = FakeDisplay(dict(PHOSH))
        reporter = _reporter(display, pin=True, watchdog=0.05)
        thread = _run_bg(reporter)
        thread.join(0.3)
        assert not reporter.sent and thread.is_alive()
        reporter.note_shell_ready()  # PIN on disk but no lock required yet
        thread.join(0.3)
        assert reporter.sent  # shell explicitly said no lock needed

    def test_pin_configured_on_disk(self, tmp_path):
        path = tmp_path / 'config.json'
        assert wayland_bindings.pin_configured_on_disk(path) is False
        path.write_text('{"theme": "amoled"}')
        assert wayland_bindings.pin_configured_on_disk(path) is False
        path.write_text('{"pin_hash": "helper$"}')
        assert wayland_bindings.pin_configured_on_disk(path) is True
        path.write_text('{corrupt')
        assert wayland_bindings.pin_configured_on_disk(path) is True


class TestPhoshPrivateXml:
    XML = DATA / 'phosh-private.xml'

    def test_vendored_verbatim_with_licence(self):
        text = self.XML.read_text(encoding='utf-8')
        assert 'Copyright © 2019 Purism SPC' in text
        assert 'Permission to use, copy, modify, distribute, and sell' in text
        reference = (ROOT.parent.parent / 'reference' / 'phoc' / 'protocols'
                     / 'phosh-private.xml')
        if reference.is_file():
            assert reference.read_text(encoding='utf-8') == text

    def test_strip_keeps_opcodes_and_drops_foreign_interfaces(self):
        text = self.XML.read_text(encoding='utf-8')
        known = wayland_bindings._declared_interfaces(DATA / 'wayland.xml')
        stripped = wayland_bindings.strip_unknown_interfaces(text, known)
        root = ET.fromstring(stripped)
        declared = {i.get('name') for i in root.iter('interface')}
        for arg in root.iter('arg'):
            iface = arg.get('interface')
            assert iface is None or iface in declared or iface in known
        phosh = next(i for i in root.iter('interface')
                     if i.get('name') == 'phosh_private')
        requests = [r.get('name') for r in phosh.iter('request')]
        original = ET.fromstring(text)
        orig_phosh = next(i for i in original.iter('interface')
                          if i.get('name') == 'phosh_private')
        assert requests == [r.get('name') for r in orig_phosh.iter('request')]
        assert requests.index('set_shell_state') == 5
        assert 'interface="wl_surface"' in stripped  # known ones survive

    def test_meson_installs_xml(self):
        text = (ROOT / 'launcher' / 'meson.build').read_text()
        assert "'data/phosh-private.xml'" in text


# -------------------------------------------------------- session -S guard

def _render_session(tmp_path):
    text = (DATA / 'xx-wm-session.in').read_text()
    text = (text.replace('@datadir@', str(tmp_path / 'share'))
                .replace('@bindir@', str(tmp_path / 'bin'))
                .replace('@libexecdir@', str(tmp_path / 'libexec')))
    script = tmp_path / 'xx-wm-session'
    script.write_text(text)
    return script


def _fake_bin(tmp_path, phoc_help, pywayland_ok):
    fake = tmp_path / 'fakebin'
    fake.mkdir()
    out = tmp_path / 'phoc.args'
    (fake / 'phoc').write_text(
        '#!/bin/sh\n'
        'if [ "$1" = "--help" ]; then printf "%%s\\n" "%s"; exit 0; fi\n'
        'echo "$* MODE=$XX_WM_PHOC_SHELL_MODE" > "%s"\n' % (phoc_help, out))
    (fake / 'python3').write_text(
        '#!/bin/sh\n'
        'case "$*" in *pywayland*) exit %d ;; esac\n'
        'echo 1\n' % (0 if pywayland_ok else 1))
    (fake / 'gsettings').write_text('#!/bin/sh\nexit 1\n')
    for f in fake.iterdir():
        f.chmod(0o755)
    return fake, out


@pytest.mark.parametrize('phoc_help,pyw,env,expect_s', [
    ('  -S, --shell   Whether to expect a shell to attach', True, {}, True),
    ('  -E, --exec   Command', True, {}, False),
    ('  -S, --shell   Whether to expect a shell to attach', False, {}, False),
    ('  -S, --shell   Whether to expect a shell to attach', True,
     {'XX_WM_PHOC_SHELL_MODE': '0'}, False),
])
def test_session_shell_mode_guard(tmp_path, phoc_help, pyw, env, expect_s):
    script = _render_session(tmp_path)
    fake, out = _fake_bin(tmp_path, phoc_help, pyw)
    home = tmp_path / 'home'
    home.mkdir()
    run_env = {'PATH': '%s:/usr/bin:/bin' % fake, 'HOME': str(home)}
    run_env.update(env)
    subprocess.run(['sh', str(script)], env=run_env, check=True,
                   timeout=30, capture_output=True)
    args = out.read_text().split()
    assert ('-S' in args) is expect_s
    assert 'MODE=%d' % (1 if expect_s else 0) in args
    assert '-C' in args and '-E' in args


# ------------------------------------------------ window lock-on-startup

def _window():
    try:
        import window
    except Exception as exc:  # pragma: no cover - env without GTK/Adw
        pytest.skip('window not importable: %s' % exc)
    return window


@pytest.fixture(autouse=False)
def _no_window_leak():
    """Other suites import window against fake gi bindings and build
    ShellWindow with object.__new__: never leave the real module cached."""
    saved = {n: m for n, m in sys.modules.items() if n == 'window'}
    yield
    sys.modules.pop('window', None)
    sys.modules.update(saved)


class _Reporter:
    def __init__(self):
        self.events = []
        self.gate = wayland_bindings.ShellStateGate()

    def require_lock(self, required=True):
        self.events.append(('require_lock', required))

    def note_shell_ready(self):
        self.events.append('ready')


@pytest.mark.usefixtures('_no_window_leak')
class TestStartupLock:
    def _shell(self, pin_hash):
        shown = []
        shell = SimpleNamespace(
            config=SimpleNamespace(pin_hash=pin_hash),
            _show_lock_screen=lambda: shown.append(1))
        window = _window()
        shell._lock_at_startup = lambda reporter: (
            window.ShellWindow._lock_at_startup(shell, reporter))
        return shell, shown

    def _patch(self, monkeypatch, reporter):
        ls = _lock_screen()
        monkeypatch.setattr(ls, '_shell_state', lambda: reporter)
        session = SimpleNamespace(cb=None)
        session.set_on_lock = lambda cb: setattr(session, 'cb', cb)
        monkeypatch.setattr(ls, 'logind_session', lambda: session)
        return session

    def test_pin_starts_locked_before_reporting_ready(self, monkeypatch):
        window = _window()
        reporter = _Reporter()
        session = self._patch(monkeypatch, reporter)
        shell, shown = self._shell('helper$')
        assert window.ShellWindow._startup_lock_gate(shell) is False
        assert shown == [1]
        assert reporter.events == [('require_lock', True), 'ready']
        session.cb()  # loginctl lock-session
        assert shown == [1, 1]

    def test_no_pin_reports_ready_unlocked(self, monkeypatch):
        window = _window()
        reporter = _Reporter()
        self._patch(monkeypatch, reporter)
        monkeypatch.setenv('XX_WM_PIN_HELPER', '0')
        shell, shown = self._shell(None)
        window.ShellWindow._startup_lock_gate(shell)
        assert shown == [] and reporter.events == ['ready']

    def test_helper_held_pin_without_marker_locks(self, monkeypatch):
        window = _window()
        reporter = _Reporter()
        self._patch(monkeypatch, reporter)
        monkeypatch.setattr(helper_client, 'pin_sync',
                            lambda config, cb: cb(True))
        shell, shown = self._shell(None)
        window.ShellWindow._startup_lock_gate(shell)
        assert shown == [1]
        assert reporter.events == [('require_lock', True), 'ready']

    def test_lock_failure_keeps_shield(self, monkeypatch):
        window = _window()
        reporter = _Reporter()
        self._patch(monkeypatch, reporter)
        shell, _shown = self._shell('helper$')

        def boom():
            raise RuntimeError('no layer shell')

        shell._show_lock_screen = boom
        window.ShellWindow._startup_lock_gate(shell)
        # Ready is noted, but the lock is required and never mapped, so
        # the gate stays shut and phoc keeps the outputs covered.
        assert ('require_lock', True) in reporter.events

    def test_no_lock_right_after_first_boot_wizard(self, monkeypatch):
        window = _window()
        reporter = _Reporter()
        reporter.gate.reported = True  # the wizard reported UP
        self._patch(monkeypatch, reporter)
        shell, shown = self._shell('helper$')
        window.ShellWindow._startup_lock_gate(shell)
        assert shown == [] and reporter.events == []
