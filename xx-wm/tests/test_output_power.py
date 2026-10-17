"""Real display power-off over zwlr_output_power_manager_v1.

Covers the vendored output-power protocol XML (upstream order pins the
opcodes), the generalized wayland_bindings entry point, the OutputPower
backend, and the DisplayManager blank/wake fallback chain:

protocol -> backlight-only -> wlopm, with output power restored before
the backlight on wake and the backlight forced up when wake blows up.
"""
import sys
import threading
import time
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
LAUNCHER_DATA = REPO / 'launcher' / 'data'
POWER_XML_NAME = 'wlr-output-power-management-unstable-v1.xml'
POWER_XML = LAUNCHER_DATA / POWER_XML_NAME

sys.path.insert(0, str(REPO / 'launcher' / 'src'))


def _install_fake_glib() -> None:
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.PRIORITY_DEFAULT = 0
    glib.idle_add = lambda fn, *a, **k: (fn(*a), 0)[1]
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None
    glib.IOCondition = types.SimpleNamespace(IN=1, HUP=16)
    glib.unix_fd_add_full = lambda *a, **k: 1

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')
    gi_mod.require_version = lambda *a, **k: None
    gi_rep.GLib = glib
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib


_install_fake_glib()

import display_manager  # noqa: E402  (needs fake GLib installed first)
import output_power  # noqa: E402
import wayland_bindings  # noqa: E402

_REAL_ARM_FD_WATCH = output_power.OutputPower._arm_fd_watch


@pytest.fixture
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path))
    wayland_bindings._logged_once.clear()
    return tmp_path


@pytest.fixture
def isolated_imports():
    saved_modules = dict(sys.modules)
    saved_path = list(sys.path)
    try:
        yield
    finally:
        for name in [n for n in sys.modules if n not in saved_modules]:
            sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


def _interface(root: ET.Element, name: str) -> ET.Element:
    return next(i for i in root.iter('interface') if i.get('name') == name)


class TestPowerXmlVendoring:
    def test_xml_file_exists(self) -> None:
        assert POWER_XML.is_file(), f'protocol XML missing: {POWER_XML}'

    def test_xml_is_well_formed(self) -> None:
        ET.parse(POWER_XML)

    def test_protocol_name(self) -> None:
        root = ET.parse(POWER_XML).getroot()
        assert root.get('name') == 'wlr_output_power_management_unstable_v1'

    def test_resolves_from_checkout(self) -> None:
        assert wayland_bindings.protocol_xml_path(POWER_XML_NAME) == POWER_XML

    def test_declares_manager_and_power_interfaces_at_v1(self) -> None:
        root = ET.parse(POWER_XML).getroot()
        manager = _interface(root, 'zwlr_output_power_manager_v1')
        power = _interface(root, 'zwlr_output_power_v1')
        assert manager.get('version') == '1'
        assert power.get('version') == '1'

    def test_manager_requests_in_upstream_order(self) -> None:
        # Opcodes come from declaration order: get_output_power must stay
        # request 0 or the compositor decodes the wrong call.
        root = ET.parse(POWER_XML).getroot()
        manager = _interface(root, 'zwlr_output_power_manager_v1')
        requests = list(manager.iter('request'))
        assert [r.get('name') for r in requests] == [
            'get_output_power', 'destroy']
        get_power = requests[0]
        args = list(get_power.iter('arg'))
        assert [(a.get('name'), a.get('type'), a.get('interface'))
                for a in args] == [
            ('id', 'new_id', 'zwlr_output_power_v1'),
            ('output', 'object', 'wl_output'),
        ]

    def test_power_requests_events_and_mode_enum_in_upstream_order(self) -> None:
        root = ET.parse(POWER_XML).getroot()
        power = _interface(root, 'zwlr_output_power_v1')
        assert [r.get('name') for r in power.iter('request')] == [
            'set_mode', 'destroy']
        assert [e.get('name') for e in power.iter('event')] == [
            'mode', 'failed']
        mode_enum = next(e for e in power.iter('enum')
                         if e.get('name') == 'mode')
        assert [(e.get('name'), e.get('value'))
                for e in mode_enum.iter('entry')] == [('off', '0'), ('on', '1')]
        set_mode = next(r for r in power.iter('request')
                        if r.get('name') == 'set_mode')
        mode_arg = next(set_mode.iter('arg'))
        assert mode_arg.get('type') == 'uint'
        mode_event = next(e for e in power.iter('event')
                          if e.get('name') == 'mode')
        assert next(mode_event.iter('arg')).get('type') == 'uint'
        failed_event = next(e for e in power.iter('event')
                            if e.get('name') == 'failed')
        assert list(failed_event.iter('arg')) == []


class TestGeneralBindings:
    def test_cache_key_covers_every_named_xml(self, isolated_cache: Path) -> None:
        toplevel_dir = wayland_bindings.cache_dir_for(
            '1.0-test', ('wayland.xml',
                         'wlr-foreign-toplevel-management-unstable-v1.xml'))
        power_dir = wayland_bindings.cache_dir_for(
            '1.0-test', wayland_bindings.OUTPUT_POWER_XML_NAMES)
        assert toplevel_dir != power_dir

    def test_cache_key_follows_single_xml_content(
        self, isolated_cache: Path, tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        overlay = tmp_path / POWER_XML_NAME
        overlay.write_bytes(POWER_XML.read_bytes())
        real_path = wayland_bindings.protocol_xml_path
        monkeypatch.setattr(wayland_bindings, 'protocol_xml_path',
                            lambda name: overlay if name == POWER_XML_NAME
                            else real_path(name))
        before = wayland_bindings.cache_dir_for(
            '1.0-test', wayland_bindings.OUTPUT_POWER_XML_NAMES)
        overlay.write_bytes(overlay.read_bytes() + b'\n')
        after = wayland_bindings.cache_dir_for(
            '1.0-test', wayland_bindings.OUTPUT_POWER_XML_NAMES)
        assert before != after

    def test_default_cache_key_unchanged(self, isolated_cache: Path) -> None:
        assert (wayland_bindings.cache_dir_for('1.0-test')
                == wayland_bindings.cache_dir_for(
                    '1.0-test', ('wayland.xml',
                                 wayland_bindings.WLR_XML_NAME)))

    def test_generate_passes_every_target_to_scanner(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        seen: dict[str, list[str]] = {}

        def _fake_run(argv, **kwargs):
            seen['argv'] = list(argv)

            class _Proc:
                returncode = 0
                stdout = ''
                stderr = ''

            return _Proc()

        monkeypatch.setattr(wayland_bindings.subprocess, 'run', _fake_run)
        base = tmp_path / 'wayland.xml'
        base.write_text('<protocol></protocol>', encoding='utf-8')
        first = tmp_path / 'a.xml'
        first.write_text('<protocol></protocol>', encoding='utf-8')
        second = tmp_path / POWER_XML_NAME
        second.write_text('<protocol></protocol>', encoding='utf-8')
        wayland_bindings.generate_protocol_bindings(
            tmp_path / 'pkg', [base, first, second])
        argv = seen['argv']
        include_at = argv.index('-i')
        assert argv[include_at + 2:] == [str(first), str(second)]

    def test_ensure_protocol_bindings_generates_then_loads(
        self, isolated_cache: Path, isolated_imports,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(wayland_bindings, 'installed_pywayland_version',
                            lambda: '9.9-test')
        requested: list[list[str]] = []

        def _fake_generate(package_dir: Path, xml_paths) -> None:
            requested.append([Path(p).name for p in xml_paths])
            package_dir = Path(package_dir)
            package_dir.mkdir(parents=True, exist_ok=True)
            (package_dir / 'power.py').write_text(
                'class WlOutput:\n    pass\n'
                'class ZwlrOutputPowerManagerV1:\n    pass\n'
                'class ZwlrOutputPowerV1:\n    pass\n',
                encoding='utf-8',
            )

        monkeypatch.setattr(wayland_bindings, 'generate_protocol_bindings',
                            _fake_generate)
        classes = wayland_bindings.ensure_protocol_bindings(
            wayland_bindings.OUTPUT_POWER_XML_NAMES,
            wayland_bindings.OUTPUT_POWER_CLASS_NAMES)
        assert classes is not None
        assert set(classes) == set(wayland_bindings.OUTPUT_POWER_CLASS_NAMES)
        assert sorted(requested[0]) == sorted(
            wayland_bindings.OUTPUT_POWER_XML_NAMES)
        monkeypatch.setattr(
            wayland_bindings, 'generate_protocol_bindings',
            lambda *args: (_ for _ in ()).throw(
                AssertionError('must not regenerate')),
        )
        assert wayland_bindings.ensure_protocol_bindings(
            wayland_bindings.OUTPUT_POWER_XML_NAMES,
            wayland_bindings.OUTPUT_POWER_CLASS_NAMES) is not None

    def test_ensure_protocol_bindings_degrades(
        self, isolated_cache: Path, isolated_imports,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(wayland_bindings, 'installed_pywayland_version',
                            lambda: None)
        assert wayland_bindings.ensure_protocol_bindings(
            wayland_bindings.OUTPUT_POWER_XML_NAMES,
            wayland_bindings.OUTPUT_POWER_CLASS_NAMES) is None
        monkeypatch.setattr(wayland_bindings, 'installed_pywayland_version',
                            lambda: '9.9-test')
        monkeypatch.setattr(wayland_bindings, 'protocol_xml_path',
                            lambda _name: None)
        assert wayland_bindings.ensure_protocol_bindings(
            wayland_bindings.OUTPUT_POWER_XML_NAMES,
            wayland_bindings.OUTPUT_POWER_CLASS_NAMES) is None

    def test_foreign_toplevel_api_still_default(
        self, isolated_cache: Path, isolated_imports,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The switcher's existing entry points keep their default set."""
        monkeypatch.setattr(wayland_bindings, 'installed_pywayland_version',
                            lambda: '9.9-test')
        package_dir = Path(isolated_cache) / 'pkg'
        package_dir.mkdir(parents=True)
        (package_dir / 'toplevel.py').write_text(
            'class WlSeat:\n    pass\n'
            'class ZwlrForeignToplevelManagerV1:\n    pass\n'
            'class ZwlrForeignToplevelHandleV1:\n    pass\n',
            encoding='utf-8',
        )
        assert set(wayland_bindings.load_classes_from(package_dir)) == set(
            wayland_bindings.NEEDED_CLASS_NAMES)
        assert wayland_bindings.load_classes_from(
            package_dir,
            wayland_bindings.OUTPUT_POWER_CLASS_NAMES) is None


class FakePowerObj:
    def __init__(self) -> None:
        self.dispatcher: dict[str, object] = {}
        self.modes: list[int] = []
        self.destroyed = False

    def set_mode(self, mode: int) -> None:
        self.modes.append(mode)

    def destroy(self) -> None:
        self.destroyed = True


class FakeCompositor:
    """Fake pywayland Display + registry driving scripted globals."""

    def __init__(self, available_globals: list[tuple[int, str, int]],
                 fail_connect: bool = False) -> None:
        self._globals = available_globals
        self._fail_connect = fail_connect
        self.dispatcher: dict[str, object] = {}
        self.binds: list[tuple[int, str, int]] = []
        self.outputs: list[object] = []
        self.powers: list[FakePowerObj] = []
        self.manager: object | None = None
        self.connected = False
        self.flushed = 0
        self.output_cls: type | None = None
        self.manager_cls: type | None = None

    def connect(self) -> None:
        if self._fail_connect:
            raise RuntimeError('no compositor')
        self.connected = True

    def get_registry(self) -> 'FakeCompositor':
        return self

    def bind(self, name: int, cls: type, version: int):
        self.binds.append((name, cls.__name__, version))
        if name == 7:
            output = cls()
            self.outputs.append(output)
            return output
        manager = cls(self)
        self.manager = manager
        return manager

    def dispatch(self, block: bool = True) -> None:
        for name, interface, version in self._globals:
            self.dispatcher['global'](self, name, interface, version)

    def flush(self) -> None:
        self.flushed += 1

    def get_fd(self) -> int:
        return 99


class FakeOutputCls:
    pass


class FakeManagerCls:
    def __init__(self, compositor: FakeCompositor) -> None:
        self._compositor = compositor

    def get_output_power(self, _output: object) -> FakePowerObj:
        power = FakePowerObj()
        self._compositor.powers.append(power)
        return power


def _power_bindings() -> dict[str, type]:
    return {'WlOutput': FakeOutputCls,
            'ZwlrOutputPowerManagerV1': FakeManagerCls,
            'ZwlrOutputPowerV1': FakePowerObj}


@pytest.fixture
def quiet_backend(monkeypatch: pytest.MonkeyPatch):
    """Hermetic backend: no scanner, no fd watch, no main loop."""
    monkeypatch.setattr(wayland_bindings, 'ensure_protocol_bindings',
                        lambda _names, _classes: None)
    monkeypatch.setattr(output_power.OutputPower, '_arm_fd_watch',
                        lambda self: None)
    return monkeypatch


def _connect(compositor: FakeCompositor) -> output_power.OutputPower:
    backend = output_power.OutputPower(display=compositor)
    backend._connect_main(_power_bindings())
    return backend


class TestOutputPowerBackend:
    def test_binds_manager_and_every_output(
        self, quiet_backend) -> None:
        compositor = FakeCompositor([(7, 'wl_output', 4),
                                     (8, 'zwlr_output_power_manager_v1', 1)])
        backend = _connect(compositor)
        assert backend.available is True
        assert compositor.manager is not None
        assert len(compositor.outputs) == 1
        assert len(compositor.powers) == 1
        assert (7, 'FakeOutputCls', 1) in compositor.binds
        assert (8, 'FakeManagerCls', 1) in compositor.binds

    def test_manager_before_output_still_powers(
        self, quiet_backend) -> None:
        compositor = FakeCompositor([(8, 'zwlr_output_power_manager_v1', 1),
                                     (7, 'wl_output', 4)])
        backend = _connect(compositor)
        assert backend.available is True
        assert len(compositor.powers) == 1

    def test_registry_global_handler_matches_wl_registry_global_event(
        self) -> None:
        import inspect
        params = list(inspect.signature(
            output_power.OutputPower._on_registry_global).parameters)
        assert params == ['self', 'registry', 'name', 'interface', 'version']

    def test_set_all_off_and_on(self, quiet_backend) -> None:
        compositor = FakeCompositor([(7, 'wl_output', 4),
                                     (8, 'zwlr_output_power_manager_v1', 1)])
        backend = _connect(compositor)
        assert backend.set_all(False) is True
        assert compositor.powers[0].modes == [0]
        assert backend.set_all(True) is True
        assert compositor.powers[0].modes == [0, 1]
        assert compositor.flushed >= 2

    def test_unavailable_without_manager(self, quiet_backend) -> None:
        compositor = FakeCompositor([(7, 'wl_output', 4)])
        backend = _connect(compositor)
        assert backend.available is False
        assert backend.set_all(False) is False

    def test_unavailable_when_compositor_refuses(
        self, quiet_backend) -> None:
        compositor = FakeCompositor([(7, 'wl_output', 4),
                                     (8, 'zwlr_output_power_manager_v1', 1)],
                                    fail_connect=True)
        backend = _connect(compositor)
        assert backend.available is False
        assert backend.set_all(False) is False

    def test_failed_event_falls_back_to_backlight(
        self, quiet_backend) -> None:
        compositor = FakeCompositor([(7, 'wl_output', 4),
                                     (8, 'zwlr_output_power_manager_v1', 1)])
        backend = _connect(compositor)
        assert backend.available is True
        power = compositor.powers[0]
        power.dispatcher['failed'](power)
        assert backend.available is False
        assert backend.set_all(False) is False
        assert power.destroyed is True

    def test_set_all_failure_marks_backend_dead(
        self, quiet_backend) -> None:
        compositor = FakeCompositor([(7, 'wl_output', 4),
                                     (8, 'zwlr_output_power_manager_v1', 1)])
        backend = _connect(compositor)

        def _boom(_mode: int) -> None:
            raise RuntimeError('socket gone')

        compositor.powers[0].set_mode = _boom
        assert backend.set_all(False) is False
        assert backend.available is False

    def test_connection_hup_marks_backend_dead(
        self, quiet_backend, monkeypatch: pytest.MonkeyPatch) -> None:
        compositor = FakeCompositor([(7, 'wl_output', 4),
                                     (8, 'zwlr_output_power_manager_v1', 1)])
        backend = _connect(compositor)
        fake_glib = types.SimpleNamespace(
            IOCondition=types.SimpleNamespace(IN=1, HUP=16))
        fake_repo = types.ModuleType('gi.repository')
        fake_repo.GLib = fake_glib
        monkeypatch.setitem(sys.modules, 'gi.repository', fake_repo)
        monkeypatch.setitem(sys.modules, 'gi.repository.GLib', fake_glib)
        assert backend._on_fd_ready(99, 16) is False
        assert backend.available is False
        assert backend.set_all(True) is False

    def test_fd_dispatch_uses_blocking_read(
        self, quiet_backend, monkeypatch: pytest.MonkeyPatch) -> None:
        compositor = FakeCompositor([(7, 'wl_output', 4),
                                     (8, 'zwlr_output_power_manager_v1', 1)])
        backend = _connect(compositor)
        seen: dict[str, object] = {}
        fake_glib = types.SimpleNamespace(
            IOCondition=types.SimpleNamespace(IN=1, HUP=16))
        fake_repo = types.ModuleType('gi.repository')
        fake_repo.GLib = fake_glib
        monkeypatch.setitem(sys.modules, 'gi.repository', fake_repo)
        monkeypatch.setitem(sys.modules, 'gi.repository.GLib', fake_glib)

        def _dispatch(block: bool = True) -> None:
            seen['block'] = block

        monkeypatch.setattr(compositor, 'dispatch', _dispatch)
        assert backend._on_fd_ready(99, 1) is True
        assert seen == {'block': True}

    def test_fd_watch_prefers_glibunix(
        self, quiet_backend, monkeypatch: pytest.MonkeyPatch) -> None:
        used: list[str] = []
        fake_glib = types.SimpleNamespace(
            PRIORITY_DEFAULT=0,
            IOCondition=types.SimpleNamespace(IN=1, HUP=16),
            unix_fd_add_full=lambda *a: used.append('glib') or 1,
        )
        fake_unix = types.SimpleNamespace(
            fd_add_full=lambda *a: used.append('glibunix') or 2)
        fake_gi = types.ModuleType('gi')
        fake_gi.require_version = lambda *a, **k: None
        fake_repo = types.ModuleType('gi.repository')
        fake_repo.GLib = fake_glib
        fake_repo.GLibUnix = fake_unix
        monkeypatch.setitem(sys.modules, 'gi', fake_gi)
        monkeypatch.setitem(sys.modules, 'gi.repository', fake_repo)
        monkeypatch.setitem(
            sys.modules, 'gi.repository.GLibUnix', fake_unix)
        monkeypatch.setattr(output_power.OutputPower, '_arm_fd_watch',
                            _REAL_ARM_FD_WATCH)
        compositor = FakeCompositor([(7, 'wl_output', 4),
                                     (8, 'zwlr_output_power_manager_v1', 1)])
        backend = output_power.OutputPower(display=compositor)
        backend._output_cls = FakeOutputCls
        backend._manager_cls = FakeManagerCls
        backend._display = compositor
        backend._arm_fd_watch()
        assert used == ['glibunix']

    def test_worker_generates_then_connects_on_main_loop(
        self, quiet_backend, monkeypatch: pytest.MonkeyPatch) -> None:
        posted: list[tuple[object, tuple[object, ...]]] = []
        monkeypatch.setattr(output_power, '_post_to_main',
                            lambda cb, *a: posted.append((cb, a)))
        monkeypatch.setattr(wayland_bindings, 'ensure_protocol_bindings',
                            lambda _names, _classes: _power_bindings())
        compositor = FakeCompositor([(7, 'wl_output', 4),
                                     (8, 'zwlr_output_power_manager_v1', 1)])
        gate = threading.Event()
        real_prepare = output_power.OutputPower._prepare

        def _gated_prepare(self) -> None:
            assert gate.wait(timeout=10)
            real_prepare(self)

        monkeypatch.setattr(
            output_power.OutputPower, '_prepare', _gated_prepare)
        backend = output_power.OutputPower(display=compositor)
        assert backend.available is False
        gate.set()
        deadline = time.time() + 10
        while not posted and time.time() < deadline:
            time.sleep(0.01)
        assert posted, 'worker never posted the main-loop connect'
        callback, args = posted[0]
        assert callback == backend._connect_main
        callback(*args)
        assert backend.available is True


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
        None


class FakeOutputPower:
    """DisplayManager backend double recording set_all calls."""

    def __init__(self, calls: list, available: bool = True,
                 refuse: bool = False, boom: bool = False) -> None:
        self._calls = calls
        self.available = available
        self._refuse = refuse
        self._boom = boom

    def set_all(self, on: bool) -> bool:
        self._calls.append(('power', on))
        if self._boom:
            raise RuntimeError('socket gone')
        return not self._refuse


@pytest.fixture
def blank_env(monkeypatch: pytest.MonkeyPatch):
    """DisplayManager with stubbed evdev/GLib/backlight/wlopm seams."""
    monkeypatch.setattr(display_manager, 'GLib', _SyncGLib)
    monkeypatch.setattr(display_manager, '_all_event_devices',
                        lambda: ['/dev/input/eventX'])
    monkeypatch.setattr(display_manager, '_watch_evdev',
                        lambda *a, **k: None)
    monkeypatch.setattr(display_manager, '_detect_fp_node',
                        lambda: '/dev/input/event9')
    calls: list = []

    def _fake_brightnessctl(*args: str):
        calls.append(('bl', args))
        if args == ('get',):
            return '200'
        return ''

    def _fake_wlopm(mode: str) -> bool:
        calls.append(('wlopm', mode))
        return True

    monkeypatch.setattr(display_manager, '_brightnessctl',
                        _fake_brightnessctl)
    monkeypatch.setattr(display_manager, '_wlopm', _fake_wlopm)

    def _build(output_power=None, **kwargs):
        states: list[bool] = []
        kwargs.setdefault('on_blank_changed', states.append)
        mgr = display_manager.DisplayManager(
            output_power=output_power, **kwargs)
        return mgr, calls, states

    return _build


class TestBlankWakeChain:
    def _drain_startup_adoption(self, calls, shared, states):
        """Construction with a bound backend forces outputs on (the
        restart-while-dark reconciler); drop those records so each test
        below asserts only its own blank/wake path."""
        assert calls == [('power', True)]
        del calls[:]
        assert shared == [('bl', ('get',))]
        del shared[:]
        assert states == [False]
        del states[:]

    def test_blank_prefers_protocol(self, blank_env) -> None:
        calls: list = []
        mgr, shared, states = blank_env(
            output_power=FakeOutputPower(calls))
        self._drain_startup_adoption(calls, shared, states)
        mgr._blank()
        assert mgr._blanked is True
        assert calls == [('power', False)]
        # Backlight off too: hwcomposer phoc only stops rendering, so the
        # FLX1 panel stayed lit (black, not off). wlopm stays unused.
        assert shared == [('bl', ('get',)), ('bl', ('set', '0'))]
        assert states == [True]

    def test_wake_restores_power_before_backlight(self, blank_env) -> None:
        calls: list = []
        mgr, shared, states = blank_env(
            output_power=FakeOutputPower(calls))
        self._drain_startup_adoption(calls, shared, states)
        mgr._blanked = True
        mgr._wake()
        assert mgr._blanked is False
        assert calls == [('power', True)]
        assert shared == [('bl', ('set', '200'))]
        assert ('wlopm', 'on') not in shared
        assert states == [False]

    def test_wake_order_is_power_then_backlight(
        self, blank_env, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        order: list[str] = []
        calls: list = []

        class _OrderedPower(FakeOutputPower):
            def set_all(self, on: bool) -> bool:
                order.append('power')
                return super().set_all(on)

        mgr, _shared, _states = blank_env(output_power=_OrderedPower(calls))
        real_brightnessctl = display_manager._brightnessctl

        def _ordered_brightnessctl(*args: str):
            if args != ('get',):
                order.append('backlight')
            return real_brightnessctl(*args)

        monkeypatch.setattr(display_manager, '_brightnessctl',
                            _ordered_brightnessctl)
        # Drop the startup adoption's power record (see
        # _drain_startup_adoption): this test orders only the wake path.
        assert order == ['power']
        order.clear()
        mgr._blanked = True
        mgr._wake()
        assert order == ['power', 'backlight']

    def test_fallback_chain_without_protocol(self, blank_env) -> None:
        calls: list = []
        mgr, shared, states = blank_env(
            output_power=FakeOutputPower(calls, available=False))
        mgr._blank()
        assert mgr._blanked is True
        assert ('power', False) not in calls
        assert shared == [('bl', ('get',)), ('bl', ('set', '0')),
                          ('wlopm', 'off')]
        assert states == [True]

    def test_wake_without_protocol_uses_wlopm_then_backlight(
        self, blank_env,
    ) -> None:
        calls: list = []
        mgr, shared, states = blank_env(
            output_power=FakeOutputPower(calls, available=False))
        mgr._blanked = True
        mgr._wake()
        assert shared == [('wlopm', 'on'), ('bl', ('set', '200'))]
        assert states == [False]

    def test_refused_set_mode_falls_back_to_backlight(
        self, blank_env,
    ) -> None:
        calls: list = []
        mgr, shared, _states = blank_env(
            output_power=FakeOutputPower(calls, refuse=True))
        # Startup adoption runs first (and is refused the same way).
        assert calls == [('power', True)]
        del calls[:]
        mgr._blank()
        assert mgr._blanked is True
        assert calls == [('power', False)]
        assert ('bl', ('set', '0')) in shared
        assert ('wlopm', 'off') in shared

    def test_exploding_set_mode_never_leaves_screen_dark(
        self, blank_env,
    ) -> None:
        calls: list = []
        mgr, shared, _states = blank_env(
            output_power=FakeOutputPower(calls, boom=True))
        mgr._blank()
        assert mgr._blanked is True
        assert ('bl', ('set', '0')) in shared
        assert ('wlopm', 'off') in shared

    def test_wake_forces_backlight_when_wlopm_raises(
        self, blank_env, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list = []
        mgr, shared, _states = blank_env(
            output_power=FakeOutputPower(calls, available=False))

        def _boom_wlopm(_mode: str) -> bool:
            raise RuntimeError('wlopm gone')

        monkeypatch.setattr(display_manager, '_wlopm', _boom_wlopm)
        mgr._blanked = True
        mgr._wake()
        assert mgr._blanked is False
        assert ('bl', ('set', '200')) in shared

    def test_headless_default_degrades_to_backlight(
        self, blank_env, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(display_manager, '_make_output_power',
                            lambda: None)
        mgr, shared, _states = blank_env()
        mgr._blank()
        assert ('bl', ('set', '0')) in shared
        mgr._wake()
        assert ('bl', ('set', '200')) in shared


class TestInstallManifests:
    def test_meson_lists_output_power_module(self) -> None:
        text = (REPO / 'launcher' / 'meson.build').read_text(
            encoding='utf-8')
        assert "'src/output_power.py'" in text

    def test_meson_lists_output_power_xml(self) -> None:
        text = (REPO / 'launcher' / 'meson.build').read_text(
            encoding='utf-8')
        assert f"'data/{POWER_XML_NAME}'" in text

    def test_deploy_syncs_output_power_xml(self) -> None:
        text = (REPO / 'scripts' / 'deploy.sh').read_text(encoding='utf-8')
        assert POWER_XML_NAME in text
        stanza = text.replace('\\\n', ' ')
        rsync_cmds = [line for line in stanza.splitlines()
                      if 'rsync' in line and '.xml' in line]
        assert rsync_cmds, 'no protocol XML rsync in deploy.sh'
        assert all(POWER_XML_NAME in cmd or '{' in cmd
                   for cmd in rsync_cmds)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
