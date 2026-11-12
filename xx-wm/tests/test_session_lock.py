"""ext-session-lock lock screen (river): backend choice, monitor placement,
the SessionLock lifecycle against a fake that mirrors gtk4-session-lock
1.3 (lock() emits ::monitor for every monitor before it returns, unlock()
emits ::unlocked before it destroys the windows, an immediate refusal
emits ::failed and returns False), and the LockScreen / ShellWindow
plumbing driven as unbound methods."""
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import session_lock  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_choice():
    session_lock._reset()
    yield
    session_lock._reset()


class FakeDisplay:
    def __init__(self, globals_=(), monitors=()):
        self._globals = set(globals_)
        self._monitors = list(monitors)

    def query_registry(self, interface):
        return interface in self._globals

    def get_monitors(self):
        monitors = self._monitors
        return SimpleNamespace(get_n_items=lambda: len(monitors),
                               get_item=lambda i: monitors[i])


class FakeMonitor:
    def __init__(self, connector, width, height):
        self.connector = connector
        self.geo = SimpleNamespace(width=width, height=height)

    def get_connector(self):
        return self.connector

    def get_geometry(self):
        return self.geo

    def __repr__(self):
        return f'<monitor {self.connector}>'


def _typelib(supported=True):
    return lambda: SimpleNamespace(is_supported=lambda: supported)


LOCK_GLOBALS = ('ext_session_lock_manager_v1',)


class TestChooseBackend:
    def choose(self, display, env=None, typelib=None, river=False):
        return session_lock.choose_backend(
            display, env=env or {}, typelib=typelib or _typelib(),
            xxwm_present=lambda: river)

    def test_river_with_the_protocol_uses_the_session_lock(self):
        assert self.choose(FakeDisplay(LOCK_GLOBALS), river=True) == (
            'session-lock', 'xxwm on river')

    def test_any_other_compositor_with_the_protocol(self):
        assert self.choose(FakeDisplay(LOCK_GLOBALS)) == (
            'session-lock', 'ext-session-lock')

    def test_phoc_session_keeps_the_layer_shell_lock(self):
        display = FakeDisplay(LOCK_GLOBALS)
        env = {'XX_WM_PHOC_SHELL_MODE': '0'}
        assert self.choose(display, env=env)[0] == 'layer-shell'

    def test_phoc_found_by_its_private_protocol(self):
        display = FakeDisplay(LOCK_GLOBALS + ('phosh_private',))
        assert self.choose(display) == ('layer-shell', 'phoc')

    def test_xxwm_wins_over_an_inherited_phoc_environment(self):
        env = {'XX_WM_PHOC_SHELL_MODE': '1'}
        assert self.choose(FakeDisplay(LOCK_GLOBALS), env=env,
                           river=True)[0] == 'session-lock'

    def test_no_protocol(self):
        assert self.choose(FakeDisplay(), river=True)[0] == 'layer-shell'

    def test_typelib_missing(self):
        backend, reason = self.choose(FakeDisplay(LOCK_GLOBALS),
                                      typelib=lambda: None, river=True)
        assert backend == 'layer-shell' and 'typelib' in reason

    def test_library_cannot_bind(self):
        backend, _ = self.choose(FakeDisplay(LOCK_GLOBALS),
                                 typelib=_typelib(False), river=True)
        assert backend == 'layer-shell'

    def test_display_without_registry_query(self):
        assert self.choose(SimpleNamespace())[0] == 'layer-shell'

    def test_decided_once_and_fall_back_sticks(self, monkeypatch):
        calls = []
        monkeypatch.setattr(session_lock, 'choose_backend',
                            lambda: calls.append(1) or ('session-lock', 'x'))
        assert session_lock.backend() == 'session-lock'
        assert session_lock.backend() == 'session-lock'
        assert calls == [1]
        session_lock.fall_back('refused')
        assert session_lock.backend() == 'layer-shell'

    def test_non_wayland_display_has_no_globals(self):
        assert session_lock._wayland_query(object()) is None
        assert session_lock._has_global(object(), 'x') is False


class TestHandsetMonitor:
    def test_portrait_output_is_the_handset(self):
        shapes = [('HDMI-A-1', 1920, 1080), ('DSI-1', 1080, 2400)]
        assert session_lock.handset_index(shapes) == 1

    def test_internal_panel_when_nothing_is_portrait(self):
        shapes = [('DP-1', 2560, 1440), ('eDP-1', 1920, 1080)]
        assert session_lock.handset_index(shapes) == 1

    def test_first_otherwise(self):
        assert session_lock.handset_index([('DP-1', 800, 600),
                                           ('DP-2', 800, 600)]) == 0

    def test_no_monitors(self):
        assert session_lock.handset_index([]) is None

    def test_monitor_object(self):
        phone = FakeMonitor('DSI-1', 360, 720)
        display = FakeDisplay(monitors=[FakeMonitor('HDMI-A-1', 1280, 720),
                                        phone])
        assert session_lock.handset_monitor(display) is phone


class FakeInstance:
    """gtk4-session-lock 1.3 GtkSessionLockInstance, as observed on river."""

    def __init__(self, module):
        self.module = module
        self.handlers = {}
        self.assigned = []
        self.is_locked = False
        self.destroyed = []
        module.instances.append(self)

    def connect(self, name, callback):
        self.handlers.setdefault(name, []).append(callback)

    def emit(self, name, *args):
        for callback in self.handlers.get(name, []):
            callback(self, *args)

    def lock(self):
        if self.module.refuse:
            self.emit('failed')
            return False
        for monitor in self.module.monitors:
            self.emit('monitor', monitor)
        return True

    def assign_window_to_monitor(self, window, monitor):
        self.assigned.append((window, monitor))

    def confirm(self):
        self.is_locked = True
        self.emit('locked')

    def unlock(self):
        if not self.is_locked:
            return
        self.is_locked = False
        self.emit('unlocked')
        self.destroyed = [w for w, _m in self.assigned]


class FakeModule:
    def __init__(self, monitors, refuse=False):
        self.monitors = monitors
        self.refuse = refuse
        self.instances = []
        self.Instance = SimpleNamespace(new=lambda: FakeInstance(self))


class TestSessionLock:
    def setup_method(self):
        self.phone = FakeMonitor('DSI-1', 360, 720)
        self.desk = FakeMonitor('HDMI-A-1', 1280, 720)
        self.ended = []
        self.made = []

    def make(self, kind):
        def factory():
            window = f'{kind}{len(self.made)}'
            self.made.append(window)
            return window
        return factory

    def lock(self, refuse=False):
        module = FakeModule([self.desk, self.phone], refuse=refuse)
        display = FakeDisplay(monitors=[self.desk, self.phone])
        lock = session_lock.SessionLock(
            self.make('ui'), self.make('cover'), on_ended=self.ended.append,
            module=module, display=display)
        return lock, module

    def test_ui_on_the_handset_cover_on_every_other_output(self):
        lock, module = self.lock()
        assert lock.lock() is True
        assigned = module.instances[0].assigned
        assert assigned == [('cover0', self.desk), ('ui1', self.phone)]
        assert lock.active and not lock.locked

    def test_monitor_plugged_in_while_locked_gets_a_cover(self):
        lock, module = self.lock()
        lock.lock()
        instance = module.instances[0]
        dock = FakeMonitor('DP-1', 1920, 1080)
        instance.emit('monitor', dock)
        assert instance.assigned[-1] == ('cover2', dock)

    def test_unlock_ends_once(self):
        lock, module = self.lock()
        lock.lock()
        module.instances[0].confirm()
        assert lock.locked
        lock.unlock()
        lock.unlock()
        assert self.ended == [False]
        assert not lock.active

    def test_unlock_before_the_compositor_confirmed_waits(self):
        lock, module = self.lock()
        lock.lock()
        lock.unlock()
        assert self.ended == [] and lock.active
        module.instances[0].confirm()
        assert self.ended == [False] and not lock.active

    def test_refused_lock_reports_failure(self):
        lock, _module = self.lock(refuse=True)
        assert lock.lock() is False
        assert self.ended == [True]
        assert not lock.active

    def test_later_failure_reports_failure(self):
        lock, module = self.lock()
        lock.lock()
        module.instances[0].emit('failed')
        assert self.ended == [True]

    def test_lock_twice_is_one_lock(self):
        lock, module = self.lock()
        lock.lock()
        lock.lock()
        assert len(module.instances) == 1


# ------------------------------------------------ LockScreen plumbing


def _lock_screen():
    return pytest.importorskip('lock_screen')


class _Widget:
    def __init__(self):
        self.parent = None

    def get_parent(self):
        return self.parent


class _Host:
    def __init__(self):
        self.child = None

    def set_child(self, child):
        if self.child is not None:
            self.child.parent = None
        self.child = child
        if child is not None:
            child.parent = self

    def get_width(self):
        return 360

    def get_height(self):
        return 720


class _Overlay(_Widget):
    def __init__(self):
        super().__init__()
        self.overlays = []

    def add_overlay(self, widget):
        assert widget.parent is None
        widget.parent = self
        self.overlays.append(widget)

    def remove_overlay(self, widget):
        self.overlays.remove(widget)
        widget.parent = None


class _Harness(_Host):
    """The LockScreen attributes the session plumbing touches."""

    def __init__(self, ls):
        super().__init__()
        self._ls = ls
        self._use_session_lock = True
        self._session = None
        self._host = None
        self._surface_overlay = _Overlay()
        self._hosted = []
        self._content = self._surface_overlay
        self.set_child(self._content)
        self.notified = []
        self.presented = 0
        self.overlay = 0
        for name in ('_session_mode', 'get_visible', 'set_visible',
                     '_make_host', '_reclaim_content', '_on_session_ended',
                     'get_width', 'can_host_surface', 'host_surface',
                     'release_surface', '_drop_hosted'):
            setattr(self, name, types.MethodType(
                getattr(ls.LockScreen, name), self))

    def notify(self, prop):
        self.notified.append(prop)

    def _new_lock_window(self):
        return _Host()

    def _init_overlay_surface(self):
        self.overlay += 1


class TestLockScreenSession:
    def test_visible_follows_the_session(self):
        ls = _lock_screen()
        h = _Harness(ls)
        assert h.get_visible() is False
        h._session = SimpleNamespace(active=True)
        assert h.get_visible() is True
        h._session.active = False
        assert h.get_visible() is False

    def test_hiding_unlocks_instead_of_unmapping(self):
        ls = _lock_screen()
        h = _Harness(ls)
        calls = []
        h._session = SimpleNamespace(active=True,
                                     unlock=lambda: calls.append('unlock'))
        h.set_visible(False)
        assert calls == ['unlock']

    def test_content_moves_into_each_lock_and_back(self):
        ls = _lock_screen()
        h = _Harness(ls)
        content = h._content
        for _ in range(3):
            host = h._make_host()
            assert host.child is content and h.child is None
            assert h.get_width() == 360
            h._on_session_ended(False)
            assert h.child is content and host.child is None
            assert h._host is None
        assert h.notified == ['visible'] * 3

    def test_refused_lock_falls_back_to_the_overlay(self, monkeypatch):
        ls = _lock_screen()
        h = _Harness(ls)
        presented = []
        # raising=False: earlier suites may leave lock_screen bound to a
        # fake Gtk whose Window has no present().
        monkeypatch.setattr(ls.Gtk.Window, 'present',
                            lambda self: presented.append(self),
                            raising=False)
        h._make_host()
        h._on_session_ended(True)
        assert h._use_session_lock is False and h.overlay == 1
        assert presented == [h] and h.child is h._content
        assert session_lock.backend() == 'layer-shell'


class TestLockScreenHostsSurfaces:
    """Over a session lock the call UI and the power menu show inside
    the lock surface, and go back to their own windows when it ends."""

    def test_no_hosting_without_an_active_session_lock(self):
        h = _Harness(_lock_screen())
        assert h.can_host_surface() is False
        assert h.host_surface(_Widget()) is False
        h._session = SimpleNamespace(active=True)
        h._use_session_lock = False
        assert h.can_host_surface() is False

    def test_hosted_widget_sits_over_the_lock_ui(self):
        h = _Harness(_lock_screen())
        h._session = SimpleNamespace(active=True)
        call = _Widget()
        assert h.host_surface(call) is True
        assert h._surface_overlay.overlays == [call]
        assert h.host_surface(call) is True
        assert h._surface_overlay.overlays == [call]
        h.release_surface(call)
        assert call.parent is None and h._hosted == []

    def test_parented_widget_is_refused(self):
        h = _Harness(_lock_screen())
        h._session = SimpleNamespace(active=True)
        widget = _Widget()
        widget.parent = object()
        assert h.host_surface(widget) is False

    def test_unlock_hands_hosted_surfaces_back_after_the_lock_is_down(self):
        h = _Harness(_lock_screen())
        session = SimpleNamespace(active=True)
        h._session = session
        h._make_host()
        seen = []
        call, menu = _Widget(), _Widget()

        def released(name, widget):
            seen.append((name, widget.parent, h.get_visible()))
        h.host_surface(call, lambda: released('call', call))
        h.host_surface(menu, lambda: released('menu', menu))
        session.active = False
        h._on_session_ended(False)
        assert seen == [('call', None, False), ('menu', None, False)]
        assert h._surface_overlay.overlays == []
        assert h.child is h._content

    def test_refused_lock_releases_after_the_fallback_lock_maps(
            self, monkeypatch):
        ls = _lock_screen()
        h = _Harness(ls)
        order = []
        monkeypatch.setattr(ls.Gtk.Window, 'present',
                            lambda self: order.append('lock'),
                            raising=False)
        h._session = SimpleNamespace(active=True)
        h._make_host()
        h.host_surface(_Widget(), lambda: order.append('call'))
        h._session.active = False
        h._on_session_ended(True)
        assert order == ['lock', 'call']


class _Window:
    """The Gtk.Window surface LockHosted wraps."""

    def __init__(self, content):
        self.child = None
        self.mapped = False
        self.presents = 0
        self.sensitive = True
        self.set_child(content)

    def get_child(self):
        return self.child

    def set_child(self, child):
        if self.child is not None:
            self.child.parent = None
        self.child = child
        if child is not None:
            assert child.parent is None
            child.parent = self

    def present(self):
        self.presents += 1
        self.mapped = True

    def set_visible(self, visible):
        self.mapped = visible

    def get_visible(self):
        return self.mapped

    def hide(self):
        self.mapped = False

    def get_sensitive(self):
        return self.sensitive

    def set_sensitive(self, sensitive):
        self.sensitive = sensitive


class _Content(_Widget):
    def __init__(self):
        super().__init__()
        self.sensitive = True

    def set_sensitive(self, sensitive):
        self.sensitive = sensitive


class _Surface(session_lock.LockHosted, _Window):
    pass


class _LockHost:
    def __init__(self, active=True, accept=True):
        self.active = active
        self.accept = accept
        self.hosted = {}

    def can_host_surface(self):
        return self.active

    def host_surface(self, widget, on_released=None):
        if not self.accept:
            return False
        assert widget.parent is None
        widget.parent = self
        self.hosted[id(widget)] = (widget, on_released)
        return True

    def release_surface(self, widget):
        self.hosted.pop(id(widget), None)
        if widget is not None and widget.parent is self:
            widget.parent = None

    def end(self):
        self.active = False
        for widget, on_released in list(self.hosted.values()):
            self.release_surface(widget)
            if on_released is not None:
                on_released()


class TestLockHosted:
    def surface(self, host):
        content = _Content()
        surface = _Surface(content)
        surface.set_lock_host(lambda: host)
        return surface, content

    def test_without_a_session_lock_it_maps_itself(self):
        surface, content = self.surface(_LockHost(active=False))
        surface.present()
        assert surface.mapped and surface.presents == 1
        assert content.parent is surface and not surface.lock_hosted()

    def test_without_a_host_it_maps_itself(self):
        surface, content = self.surface(None)
        surface.set_visible(True)
        assert surface.mapped and content.parent is surface

    def test_over_a_session_lock_the_content_moves_into_the_lock(self):
        host = _LockHost()
        surface, content = self.surface(host)
        surface.present()
        assert content.parent is host and surface.child is None
        assert not surface.mapped and surface.presents == 0
        assert surface.get_visible() and surface.lock_hosted()
        surface.present()
        surface.set_visible(True)
        assert len(host.hosted) == 1 and surface.presents == 0

    def test_a_mapped_surface_unmaps_when_the_lock_takes_it(self):
        host = _LockHost(active=False)
        surface, content = self.surface(host)
        surface.present()
        host.active = True
        surface.present()
        assert not surface.mapped and content.parent is host

    def test_hiding_returns_the_content(self):
        host = _LockHost()
        surface, content = self.surface(host)
        surface.present()
        surface.hide()
        assert content.parent is surface and host.hosted == {}
        assert not surface.get_visible()
        surface.present()
        surface.set_visible(False)
        assert content.parent is surface and host.hosted == {}

    def test_lock_end_shows_it_as_its_own_surface(self):
        host = _LockHost()
        surface, content = self.surface(host)
        surface.present()
        host.end()
        assert content.parent is surface and surface.mapped
        assert surface.presents == 1 and not surface.lock_hosted()

    def test_a_refusing_host_leaves_it_on_its_own_window(self):
        surface, content = self.surface(_LockHost(accept=False))
        surface.present()
        assert content.parent is surface and surface.mapped

    def test_a_raising_host_leaves_it_on_its_own_window(self):
        surface, content = self.surface(None)

        def boom():
            raise RuntimeError('gone')
        surface.set_lock_host(boom)
        surface.present()
        assert content.parent is surface and surface.mapped

    def test_stale_host_is_left_on_the_next_present(self):
        host = _LockHost()
        surface, content = self.surface(host)
        surface.present()
        host.active = False
        surface.present()
        assert content.parent is surface and surface.mapped

    def test_sensitivity_follows_into_the_lock(self):
        host = _LockHost()
        surface, content = self.surface(host)
        surface.present()
        surface.set_sensitive(False)
        assert content.sensitive is False
        surface.hide()
        assert content.sensitive is True


class TestEngaged:
    def _lock(self, refuse=False):
        module = FakeModule([FakeMonitor('DSI-1', 360, 720)], refuse=refuse)
        lock = session_lock.SessionLock(
            lambda: 'ui', lambda: 'cover', module=module,
            display=FakeDisplay(monitors=module.monitors))
        return lock, module

    def test_engaged_follows_the_lock(self):
        lock, module = self._lock()
        assert session_lock.engaged() is False
        lock.lock()
        assert session_lock.engaged() is True
        module.instances[0].confirm()
        lock.unlock()
        assert session_lock.engaged() is False

    def test_refused_lock_never_engages(self):
        lock, _module = self._lock(refuse=True)
        lock.lock()
        assert session_lock.engaged() is False

    def test_banners_stay_off_while_engaged(self, monkeypatch):
        banner = pytest.importorskip('banner')
        assert banner._session_locked() is False
        monkeypatch.setattr(session_lock, '_engaged', {1})
        assert banner._session_locked() is True

    def test_call_ui_and_power_menu_are_lock_hosted(self):
        for name, cls in (('call_ui', 'CallUI'), ('power_menu', 'PowerMenu')):
            source = (Path(__file__).parent.parent / 'launcher' / 'src'
                      / f'{name}.py').read_text()
            assert f'class {cls}(LockHosted, Gtk.Window):' in source


# ------------------------------------------- ShellWindow: lock first


@pytest.fixture
def _no_window_leak():
    saved = {n: m for n, m in sys.modules.items() if n == 'window'}
    yield
    sys.modules.pop('window', None)
    sys.modules.update(saved)


@pytest.mark.usefixtures('_no_window_leak')
class TestLockBeforeFirstFrame:
    def _run(self, monkeypatch, pin_hash, backend):
        try:
            import window
        except Exception as exc:  # pragma: no cover - env without GTK/Adw
            pytest.skip('window not importable: %s' % exc)
        monkeypatch.setattr(session_lock, 'backend', lambda: backend)
        shown = []
        shell = SimpleNamespace(config=SimpleNamespace(pin_hash=pin_hash),
                                _show_lock_screen=lambda: shown.append(1))
        window.ShellWindow._lock_before_first_frame(shell)
        return shown

    def test_pin_on_river_locks_during_construction(self, monkeypatch):
        assert self._run(monkeypatch, 'helper$', 'session-lock') == [1]

    def test_no_pin_no_early_lock(self, monkeypatch):
        assert self._run(monkeypatch, None, 'session-lock') == []

    def test_phoc_keeps_the_idle_gate(self, monkeypatch):
        assert self._run(monkeypatch, 'helper$', 'layer-shell') == []


def test_lock_message_lines_wrap_inside_the_screen():
    """The wrong-PIN line ("Incorrect PIN — 4 attempts before lockout")
    is wider than a 360 px screen in the lock font. A session-lock surface
    that grows past its configured size is a protocol error that kills
    the shell, so the message lines wrap."""
    source = (Path(__file__).parent.parent / 'launcher' / 'src'
              / 'lock_screen.py').read_text()
    for label in ('error_label', 'lockout_label'):
        assert f'self.{label}.set_wrap(True)' in source
