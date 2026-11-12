"""Startup reconciliation with the compositor's output-power mode.

``zwlr_output_power`` mode lives in the compositor: when the shell
restarts (deploy, crash) while the screen is off, phoc keeps the output
off, but a fresh DisplayManager used to assume ``_blanked = False`` —
so the next power-key press "blanked" an already-dark screen and the
user had to press twice. DisplayManager now forces every output on once
the backend binds (and restores a dark backlight), and mirrors the
compositor-reported `mode` / `failed` events, including changes made by
someone else.
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


class _SyncGLib:
    """Synchronous GLib seam: idle_add runs inline; timers are inert ids."""

    SOURCE_REMOVE = False
    SOURCE_CONTINUE = True

    @staticmethod
    def idle_add(fn, *a):
        fn(*a)
        return 0

    @staticmethod
    def timeout_add(_ms, _cb, *_a):
        return 1

    timeout_add_seconds = timeout_add

    @staticmethod
    def source_remove(_tid):
        return None


def _install_fake_gi():
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.PRIORITY_DEFAULT = 0
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None
    glib.idle_add = lambda fn, *a, **k: (fn(*a), 0)[1]
    glib.IOCondition = types.SimpleNamespace(IN=1, HUP=16)
    glib.unix_fd_add_full = lambda *a, **k: 1

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')

    def _require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError(f'{namespace} not available')

    gi_mod.require_version = _require_version
    gi_rep.GLib = glib
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib


_install_fake_gi()

import display_manager  # noqa: E402  (needs fake gi modules installed first)
import output_power  # noqa: E402
import wayland_bindings  # noqa: E402


class FakeOutputPower:
    """Scriptable stand-in for OutputPower starting in a given mode.

    ``mode`` mirrors the compositor state (False = off). ``set_all``
    applies immediately and fires the mode listeners, like the real
    compositor's `mode` event would; ``fire_mode`` / ``fire_failed``
    script externally caused events.
    """

    def __init__(self, mode: bool = False) -> None:
        self.mode = mode
        self.set_calls: list[bool] = []
        self.dead = False
        self._ready: list = []
        self._modes: list = []
        self._fails: list = []

    @property
    def available(self) -> bool:
        return not self.dead

    def set_all(self, on: bool) -> bool:
        if self.dead:
            return False
        self.set_calls.append(on)
        self.mode = on
        for callback in list(self._modes):
            callback(on)
        return True

    def add_ready_listener(self, callback) -> None:
        self._ready.append(callback)

    def add_mode_listener(self, callback) -> None:
        self._modes.append(callback)

    def add_fail_listener(self, callback) -> None:
        self._fails.append(callback)

    def fire_ready(self) -> None:
        for callback in list(self._ready):
            callback()

    def fire_mode(self, on: bool) -> None:
        self.mode = on
        for callback in list(self._modes):
            callback(on)

    def fire_failed(self) -> None:
        self.dead = True
        for callback in list(self._fails):
            callback()


@pytest.fixture
def dm_env(monkeypatch):
    """DisplayManager with stubbed devices, backlight, and blanked reports."""
    monkeypatch.setattr(display_manager, 'GLib', _SyncGLib)
    monkeypatch.setattr(display_manager, '_all_event_devices',
                        lambda: ['/dev/input/eventX'])
    monkeypatch.setattr(display_manager, '_watch_evdev',
                        lambda *a, **k: None)
    monkeypatch.setattr(display_manager, '_detect_fp_node',
                        lambda: '/dev/input/event9')
    monkeypatch.setattr(display_manager, '_wlopm', lambda *a: True)
    brightness = {'level': '200', 'sets': []}

    def _fake_brightnessctl(*args: str):
        if args and args[0] == 'get':
            return brightness['level']
        if args[:1] == ('set',) and len(args) == 2:
            brightness['level'] = args[1]
            brightness['sets'].append(args[1])
        return ''

    monkeypatch.setattr(display_manager, '_brightnessctl',
                        _fake_brightnessctl)
    monkeypatch.setattr(display_manager, '_run_io', lambda job: job())
    return brightness


def _build(monkeypatch, backend, **kwargs):
    changes: list[bool] = []
    mgr = display_manager.DisplayManager(
        output_power=backend, on_blank_changed=changes.append, **kwargs)
    return mgr, changes


class TestStartupAdoption:
    def test_restart_while_dark_forces_output_on(self, dm_env, monkeypatch):
        """The bug: a fake starting "off" must be switched on at startup,
        and the manager must mirror lit — not assume it."""
        backend = FakeOutputPower(mode=False)
        mgr, changes = _build(monkeypatch, backend)
        assert backend.set_calls == [True]
        assert backend.mode is True
        assert mgr._blanked is False
        assert changes == [False]

    def test_restart_while_dark_restores_dark_backlight(self, dm_env,
                                                       monkeypatch):
        dm_env['level'] = '0'
        backend = FakeOutputPower(mode=False)
        _build(monkeypatch, backend)
        assert dm_env['sets'] == ['200']

    def test_restart_leaves_user_backlight_level_alone(self, dm_env,
                                                      monkeypatch):
        dm_env['level'] = '42'
        backend = FakeOutputPower(mode=True)
        _build(monkeypatch, backend)
        assert dm_env['sets'] == []

    def test_late_bind_also_forces_on(self, dm_env, monkeypatch):
        """The real backend connects after __init__: the ready event must
        reconcile the same way the constructor path does."""
        backend = FakeOutputPower(mode=False)
        backend.dead = True  # not yet bound at construction
        mgr, changes = _build(monkeypatch, backend)
        assert backend.set_calls == []
        backend.dead = False
        backend.fire_ready()
        assert backend.set_calls == [True]
        assert mgr._blanked is False
        assert changes == [False]

    def test_backend_without_listeners_still_adopted(self, dm_env,
                                                   monkeypatch):
        """Duck typing: an old fake with only available/set_all still gets
        the startup force-on."""

        class _OldFake:
            def __init__(self) -> None:
                self.calls: list[bool] = []

            @property
            def available(self) -> bool:
                return True

            def set_all(self, on: bool) -> bool:
                self.calls.append(on)
                return True

        fake = _OldFake()
        mgr, _changes = _build(monkeypatch, fake)
        assert fake.calls == [True]
        assert mgr._blanked is False

    def test_unavailable_backend_leaves_state_alone(self, dm_env,
                                                   monkeypatch):
        backend = FakeOutputPower(mode=False)
        backend.dead = True
        mgr, changes = _build(monkeypatch, backend)
        assert backend.set_calls == []
        assert mgr._blanked is False
        assert changes == []


class TestModeMirroring:
    def test_external_mode_off_blanks(self, dm_env, monkeypatch):
        backend = FakeOutputPower(mode=True)
        mgr, changes = _build(monkeypatch, backend)
        assert mgr._blanked is False
        backend.fire_mode(False)  # someone else blanked via the protocol
        assert mgr._blanked is True
        assert changes[-1] is True

    def test_external_mode_on_wakes(self, dm_env, monkeypatch):
        backend = FakeOutputPower(mode=False)
        mgr, changes = _build(monkeypatch, backend)
        assert mgr._blanked is False
        backend.fire_mode(False)
        assert mgr._blanked is True
        backend.fire_mode(True)
        assert mgr._blanked is False
        assert changes[-1] is False

    def test_failed_event_keeps_mirrored_state(self, dm_env, monkeypatch):
        """`failed` means the refused change never happened: the mirror
        stays as it was, and the backend drops out of use."""
        backend = FakeOutputPower(mode=True)
        mgr, changes = _build(monkeypatch, backend)
        backend.fire_mode(False)
        assert mgr._blanked is True
        before = list(changes)
        backend.fire_failed()
        assert mgr._blanked is True
        assert changes == before
        assert backend.available is False
        assert backend.set_all(False) is False


class TestBackendListenerFanout:
    @pytest.fixture
    def backend(self, monkeypatch):
        monkeypatch.setattr(wayland_bindings, 'ensure_protocol_bindings',
                            lambda _names, _classes: None)
        monkeypatch.setattr(output_power.OutputPower, '_arm_fd_watch',
                            lambda self: None)
        return output_power.OutputPower(display=object())

    def test_mode_event_forwards_on_and_off(self, backend):
        seen: list[bool] = []
        backend.add_mode_listener(seen.append)
        backend._on_mode(object(), output_power.MODE_OFF)
        backend._on_mode(object(), output_power.MODE_ON)
        assert seen == [False, True]

    def test_failed_event_marks_dead_and_notifies(self, backend):
        backend._connected = True
        backend._manager = object()
        assert backend.available is True
        seen: list[bool] = []
        backend.add_fail_listener(lambda: seen.append(True))
        power = types.SimpleNamespace(destroy=lambda: None)
        backend._on_failed(power)
        assert backend.available is False
        assert seen == [True]

    def test_ready_fires_once_on_connect(self, backend):
        seen: list[bool] = []
        backend.add_ready_listener(lambda: seen.append(True))
        backend._fire_ready()
        backend._fire_ready()
        assert seen == [True]

    def test_broken_listener_does_not_break_mirror(self, backend):
        def _boom(_on: bool) -> None:
            raise RuntimeError('listener bug')

        seen: list[bool] = []
        backend.add_mode_listener(_boom)
        backend.add_mode_listener(seen.append)
        backend._on_mode(object(), output_power.MODE_ON)
        assert seen == [True]


if __name__ == '__main__':
    import pytest as _pytest

    _pytest.main([__file__, '-v'])
