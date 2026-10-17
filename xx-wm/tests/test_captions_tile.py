"""Shell side of live captions: Captions tile, volume-row toggle, privacy.

Contract under test: ``ref/xx-captions/docs/shell-integration.md``
(service ``io.piercingxx.Captions1``, overlay ``xx-captions --overlay``).
The tile is visible only while xx-captions is installed; tap toggles
playback through the service, long-press opens the overlay's model
screen. ``captions.py`` keeps its gi imports function-local, so it
imports cleanly under whatever gi flavor collection installed — gi
itself is only imported inside test bodies, where conftest has
restored the real modules.
"""
import itertools
import shutil
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest


def _install_fake_gi() -> None:
    """Make `from gi.repository import Gdk, GLib, Gio, Gtk` resolve to stubs.

    Same trick as test_quick_tiles: widget construction is never run —
    panel logic is exercised on bare panels built with object.__new__.
    captions.py itself needs no fake (its gi imports are function-local,
    so they resolve to the real modules conftest restores at test time).
    """
    import types

    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None
    glib.idle_add = lambda *a, **k: None
    glib.Error = Exception
    glib.Variant = lambda *a, **k: None
    glib.VariantType = lambda *a, **k: None

    gio = types.ModuleType('gi.repository.Gio')
    gio.DBusCallFlags = types.SimpleNamespace(NONE=0)
    gio.DBusSignalFlags = types.SimpleNamespace(NONE=0)

    for name in ('Gdk', 'Gtk'):
        sys.modules[f'gi.repository.{name}'] = types.ModuleType(
            f'gi.repository.{name}')
    sys.modules['gi.repository.Gtk'].Box = type('Box', (), {})

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')

    def _require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError(f'{namespace} not available')
    gi_mod.require_version = _require_version
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib
    sys.modules['gi.repository.Gio'] = gio


sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import captions  # noqa: E402


def _load_quick_actions_private():
    """Private quick_actions bound to test-local fake gi.

    Importing the shared module here would rebind its gi flavor for
    every suite collected after this one (test_capture needs a real
    Gtk.Window at import); exec a private copy under rich-enough fakes
    instead and restore the ambient gi entries afterwards, the same
    trick test_f_privacy uses for its real-gi copy.
    """
    import importlib.util

    saved = {name: sys.modules[name] for name in list(sys.modules)
             if name == 'gi' or name.startswith('gi.')}
    _install_fake_gi()
    try:
        src = (Path(__file__).parent.parent / 'launcher' / 'src'
               / 'quick_actions.py')
        spec = importlib.util.spec_from_file_location(
            'quick_actions_captions_test', str(src))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        for name in [name for name in sys.modules
                     if name == 'gi' or name.startswith('gi.')]:
            del sys.modules[name]
        sys.modules.update(saved)


quick_actions = _load_quick_actions_private()

REPO = Path(__file__).resolve().parent.parent


def _ref_src():
    """xx-captions tree: lane ref/ first, sibling checkout second."""
    for cand in (REPO / 'ref' / 'xx-captions',
                 REPO.parent / 'xx-captions'):
        if (cand / 'src' / 'xx_captions' / 'dbus_api.py').is_file():
            return cand / 'src'
    return None


@pytest.fixture()
def ref_xx_captions():
    src = _ref_src()
    if src is None:
        pytest.skip('no xx-captions tree beside this lane '
                    '(ref/ nor ../xx-captions)')
    sys.path.insert(0, str(src))
    try:
        yield src
    finally:
        if str(src) in sys.path:
            sys.path.remove(str(src))
        for mod in [m for m in sys.modules
                    if m == 'xx_captions' or m.startswith('xx_captions.')]:
            del sys.modules[mod]


@pytest.fixture()
def model_home(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    return home


def _install_model(home, complete=True):
    mid = next(iter(captions._MODEL_FILES))
    names = captions._MODEL_FILES[mid]
    if not complete:
        names = names[:1]
    for name in names:
        target = (home / '.local' / 'share' / 'xx-captions'
                  / 'models' / mid / name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'x')
    return mid


class _FakeParams:
    def __init__(self, values):
        self._values = values

    def unpack(self):
        return self._values


class _FakeResult:
    def __init__(self, reply=None, error=None):
        self._reply = reply
        self._error = error


class _FakeBus:
    """Duck-typed Gio.DBusConnection with the REAL call/subscribe shape."""

    def __init__(self, active=False, source=''):
        self.active = active
        self.source = source
        self.calls = []
        self.subs = []
        self.unsubs = []
        self._handlers = {}
        self._next = 1
        self._pending = []

    def _reply_for(self, method):
        from gi.repository import GLib

        if method == 'GetAll':
            return GLib.Variant('(a{sv})', ({
                'Active': GLib.Variant('b', self.active),
                'Source': GLib.Variant('s', self.source),
            },))
        return GLib.Variant('()', ())

    def call(self, bus_name, path, iface, method, params, reply_type,
             flags, timeout, cancellable, callback, *user_data):
        assert not user_data, 'no trailing user_data: callback takes 2 args'
        self.calls.append({'bus': bus_name, 'path': path, 'iface': iface,
                           'method': method, 'params': params,
                           'reply_type': reply_type})
        self._pending.append((callback, _FakeResult(self._reply_for(method))))
        return 1

    def run_pending(self, error=None):
        pending, self._pending = self._pending, []
        for callback, result in pending:
            if error is not None:
                result = _FakeResult(error=error)
            callback(self, result)

    def call_finish(self, result):
        if result._error is not None:
            raise result._error
        return result._reply

    def signal_subscribe(self, sender, iface, signal, path, arg0, flags,
                         callback, *user_data):
        assert not user_data, 'no trailing user_data: handler takes 6 args'
        sub = self._next
        self._next += 1
        self.subs.append({'sender': sender, 'iface': iface,
                          'signal': signal, 'path': path})
        self._handlers[sub] = (signal, callback)
        return sub

    def signal_unsubscribe(self, sub):
        self.unsubs.append(sub)

    def emit(self, signal, params):
        for _sub, (name, callback) in self._handlers.items():
            if name == signal:
                for sub in [s for s in self.subs if s['signal'] == signal]:
                    callback(self, ':1.99', sub['path'], sub['iface'],
                             signal, params)


def _wired(home, bus=None, **kwargs):
    bus = bus if bus is not None else _FakeBus()
    changes = []
    launches = []
    ctrl = captions.CaptionsController(
        on_change=lambda: changes.append(ctrl.tile_state),
        bus=bus, home=home,
        launcher=lambda: launches.append(1) or True, **kwargs)
    ctrl.connect()
    bus.run_pending()
    return ctrl, bus, changes, launches


class TestTileState:
    @pytest.mark.parametrize(
        'active,starting,installed,error,expected',
        [(False, False, True, False, 'off'),
         (False, True, True, False, 'starting'),
         (True, False, True, False, 'on'),
         (True, True, True, False, 'on'),
         (False, False, False, False, 'no-model'),
         (True, False, False, False, 'no-model'),
         (False, False, True, True, 'error'),
         (False, True, False, True, 'error')])
    def test_contract_states(self, active, starting, installed, error,
                             expected):
        assert captions.tile_state(active, starting, installed,
                                   error) == expected

    def test_state_names(self):
        assert set(captions.TILE_STATES) == {
            'off', 'starting', 'on', 'no-model', 'error'}

    def test_matches_reference_every_combo(self, ref_xx_captions):
        from xx_captions import overlay_state

        for combo in itertools.product((False, True), repeat=4):
            active, starting, installed, error = combo
            assert captions.tile_state(
                active, starting, installed, error) == \
                overlay_state.tile_state(active, starting, installed,
                                         error), combo

    def test_labels_fit_short_screens(self):
        assert captions.tile_label('off') == 'Off'
        assert captions.tile_label('on') == 'On'
        assert captions.tile_label('starting') == 'Starting…'
        assert captions.tile_label('no-model') == 'No model'
        assert captions.tile_label('error', 'boom') == 'boom'

    def test_dbus_errors_read_as_text_not_glib_wrappers(self):
        unknown = Exception('g-dbus-error-quark: GDBus.Error:org.freedesktop.'
                            'DBus.Error.ServiceUnknown: The name is not activatable (2)')
        assert captions.error_text(unknown) == 'Unavailable'
        failed = Exception('g-dbus-error-quark: GDBus.Error:io.piercingxx.'
                           'Captions1.Error.NoAudio: No audio source')
        assert captions.error_text(failed) == 'No audio source'
        assert captions.error_text(RuntimeError('boom')) == 'boom'
        assert captions.tile_label('error', '') == 'Error'
        for state in captions.TILE_STATES:
            if state != 'error':
                assert len(captions.tile_label(state)) <= 10


class TestContractNames:
    def test_bus_name_path_and_interface_pinned(self):
        assert captions.BUS_NAME == 'io.piercingxx.Captions1'
        assert captions.OBJECT_PATH == '/io/piercingxx/Captions1'
        assert captions.INTERFACE == 'io.piercingxx.Captions1'

    def test_overlay_app_id_differs_from_service_name(self):
        assert captions.OVERLAY_APP_ID == 'io.piercingxx.Captions'
        assert captions.OVERLAY_APP_ID != captions.BUS_NAME

    def test_overlay_argv(self):
        assert tuple(captions.OVERLAY_ARGV) == ('xx-captions', '--overlay')


class TestInstallGate:
    def _bare_panel(self, ctrl=None):
        panel = object.__new__(quick_actions.QuickActionsPanel)
        panel._dnd = None
        panel._focus = None
        panel._hud = None
        panel._als = _FakeALS()
        panel._location = _FakeLocation()
        panel._captions = ctrl
        return panel

    def test_tile_hidden_without_package(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_captions_installed',
                            lambda: False)
        keys = [t.key for t in self._bare_panel()._tiles()]
        assert 'captions' not in keys

    def test_tile_shown_in_expanded_tier(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_captions_installed',
                            lambda: True)
        tiles = {t.key: t for t in self._bare_panel()._tiles()}
        assert tiles['captions'].tier == 2
        assert tiles['captions'].label == 'Captions'

    def test_tile_state_routes_to_controller(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_captions_installed',
                            lambda: True)
        ctrl = _FakeController()
        tiles = {t.key: t for t in self._bare_panel(ctrl)._tiles()}
        assert tiles['captions'].get_state() is False
        tiles['captions'].set_state(True)
        assert ctrl.enabled is True
        ctrl.active = True
        assert tiles['captions'].get_state() is True

    def test_gate_accepts_desktop_entry(self, tmp_path, monkeypatch):
        apps = tmp_path / 'apps'
        (apps / 'applications').mkdir(parents=True)
        (apps / 'applications' / captions.DESKTOP_ENTRY).write_text('[x]')
        monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'empty'))
        monkeypatch.setenv('XDG_DATA_DIRS', str(apps))
        monkeypatch.setenv('PATH', str(tmp_path))
        assert captions.captions_installed() is True

    def test_gate_accepts_activation_file(self, tmp_path, monkeypatch):
        services = tmp_path / 'data' / 'dbus-1' / 'services'
        services.mkdir(parents=True)
        (services / captions.ACTIVATION_FILE).write_text('[x]')
        monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
        monkeypatch.setenv('XDG_DATA_DIRS', str(tmp_path / 'nodirs'))
        monkeypatch.setenv('PATH', str(tmp_path))
        assert captions.captions_installed() is True

    def test_gate_rejects_empty_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'home'))
        monkeypatch.setenv('XDG_DATA_DIRS', str(tmp_path / 'dirs'))
        monkeypatch.setenv('PATH', str(tmp_path))
        assert captions.captions_installed() is False

    def test_no_settings_section(self):
        assert quick_actions.tile_settings_section('captions') is None


class _FakeALS:
    def available(self):
        return False


class _FakeLocation:
    def available(self):
        return False

    def is_active(self):
        return False

    def set_enabled(self, value):
        pass


class _FakeController:
    def __init__(self):
        self.active = False
        self.enabled = None
        self.model_calls = []
        self.force_state = None

    @property
    def tile_state(self):
        if self.force_state is not None:
            return self.force_state
        return captions.tile_state(self.active, False, True, False)

    @property
    def label(self):
        return captions.tile_label(self.tile_state)

    @property
    def error(self):
        return None

    def set_enabled(self, value):
        self.enabled = bool(value)

    def open_model_screen(self):
        self.model_calls.append(1)


class TestTapToggles:
    def test_enable_starts_playback_and_launches_overlay(self, model_home):
        _install_model(model_home)
        ctrl, bus, changes, launches = _wired(model_home)
        assert bus.calls and bus.calls[0]['method'] == 'GetAll'

        ctrl.set_enabled(True)

        assert launches == [1]
        assert ctrl.starting is True
        assert ctrl.tile_state == 'starting'
        start = bus.calls[-1]
        assert (start['bus'], start['path'], start['iface'],
                start['method']) == (
            captions.BUS_NAME, captions.OBJECT_PATH,
            captions.INTERFACE, 'Start')
        assert start['params'].unpack() == ('playback',)
        assert start['reply_type'] is None

    def test_enable_completes_when_service_confirms(self, model_home):
        _install_model(model_home)
        bus = _FakeBus(active=True, source='playback')
        ctrl, bus, changes, launches = _wired(model_home, bus=bus)
        ctrl.set_enabled(True)
        bus.run_pending()
        bus.run_pending()
        assert ctrl.active is True
        assert ctrl.source == 'playback'
        assert ctrl.starting is False
        assert ctrl.tile_state == 'on'
        assert 'on' in changes

    def test_overlay_launches_once_per_session(self, model_home):
        _install_model(model_home)
        ctrl, bus, changes, launches = _wired(model_home)
        ctrl.set_enabled(True)
        ctrl.set_enabled(True)
        assert launches == [1]

    def test_disable_calls_stop(self, model_home):
        _install_model(model_home)
        bus = _FakeBus(active=True, source='playback')
        ctrl, bus, changes, launches = _wired(model_home, bus=bus)
        assert ctrl.tile_state == 'on'
        ctrl.set_enabled(False)
        stop = bus.calls[-1]
        assert stop['method'] == 'Stop'
        assert stop['reply_type'] is None
        bus.active = False
        bus.run_pending()
        bus.run_pending()
        assert ctrl.tile_state == 'off'

    def test_late_start_reply_after_disable_is_ignored(self, model_home):
        _install_model(model_home)
        ctrl, bus, changes, launches = _wired(model_home)
        ctrl.set_enabled(True)
        ctrl.set_enabled(False)
        bus.run_pending()
        bus.run_pending()
        assert ctrl.starting is False
        assert ctrl.error is None
        assert ctrl.tile_state == 'off'

    def test_start_failure_shows_error_text(self, model_home):
        _install_model(model_home)
        ctrl, bus, changes, launches = _wired(model_home)
        ctrl.set_enabled(True)
        bus.run_pending(error=Exception('NotPermitted'))
        assert ctrl.starting is False
        assert ctrl.error == 'NotPermitted'
        assert ctrl.tile_state == 'error'
        assert ctrl.label == 'NotPermitted'
        ctrl.set_enabled(True)
        assert ctrl.error is None
        assert ctrl.tile_state == 'starting'

    def test_enable_without_bus_reports_not_connected(self, model_home):
        _install_model(model_home)
        ctrl = captions.CaptionsController(home=model_home)
        ctrl.set_enabled(True)
        assert ctrl.starting is False
        assert ctrl.error == 'not connected'
        assert ctrl.tile_state == 'error'

    def test_no_model_tap_only_opens_overlay(self, model_home):
        ctrl, bus, changes, launches = _wired(model_home)
        assert ctrl.installed is False
        ctrl.set_enabled(True)
        assert launches == [1]
        assert [c['method'] for c in bus.calls] == ['GetAll']
        assert ctrl.starting is False
        assert ctrl.error is None
        assert ctrl.tile_state == 'no-model'

    def test_reply_types_are_never_plain_strings(self, model_home):
        _install_model(model_home)
        from gi.repository import GLib

        ctrl, bus, changes, launches = _wired(model_home)
        ctrl.set_enabled(True)
        bus.run_pending()
        bus.run_pending()
        ctrl.set_enabled(False)
        bus.run_pending()
        bus.run_pending()
        assert bus.calls, 'expected Start/Stop/GetAll traffic'
        for call in bus.calls:
            reply_type = call['reply_type']
            assert reply_type is None or isinstance(
                reply_type, GLib.VariantType), call


class TestPropertiesWatch:
    def test_subscriptions_filter_on_service_sender(self, model_home):
        ctrl, bus, changes, launches = _wired(model_home)
        by_signal = {s['signal']: s for s in bus.subs}
        assert set(by_signal) == {'Text', 'PropertiesChanged'}
        for sub in by_signal.values():
            assert sub['sender'] == captions.BUS_NAME
            assert sub['path'] == captions.OBJECT_PATH

    def test_getall_uses_variant_reply_type(self, model_home):
        from gi.repository import GLib

        _ctrl, bus, _c, _l = _wired(model_home)
        getall = bus.calls[0]
        assert getall['method'] == 'GetAll'
        assert isinstance(getall['reply_type'], GLib.VariantType)
        assert getall['reply_type'].dup_string() == '(a{sv})'
        assert getall['params'].unpack() == (captions.INTERFACE,)

    def test_getall_unpacks_first_out_arg(self, model_home):
        bus = _FakeBus(active=True, source='mic')
        ctrl, bus, changes, launches = _wired(model_home, bus=bus)
        assert (ctrl.active, ctrl.source) == (True, 'mic')

    def test_properties_changed_updates_live(self, model_home):
        from gi.repository import GLib

        _install_model(model_home)
        ctrl, bus, changes, launches = _wired(model_home)
        assert ctrl.tile_state == 'off'
        bus.emit('PropertiesChanged', _FakeParams(
            (captions.INTERFACE,
             {'Active': GLib.Variant('b', True),
              'Source': GLib.Variant('s', 'playback')}, [])))
        assert ctrl.active is True
        assert ctrl.starting is False
        assert ctrl.tile_state == 'on'
        assert 'on' in changes

    def test_text_signal_forwards_valid_caption(self, model_home):
        seen = []
        ctrl, bus, changes, launches = _wired(
            model_home, on_text=lambda text, final: seen.append((text, final)))
        bus.emit('Text', _FakeParams(('hello', False)))
        bus.emit('Text', _FakeParams(('hello world', True)))
        assert seen == [('hello', False), ('hello world', True)]

    def test_text_signal_drops_garbage(self, model_home):
        from gi.repository import GLib

        seen = []
        ctrl, bus, changes, launches = _wired(
            model_home, on_text=lambda text, final: seen.append((text, final)))
        bus.emit('Text', _FakeParams(('', True)))
        bus.emit('Text', GLib.Variant('(sb)', ('', True)))

        class _Exploding:
            def unpack(self):
                raise ValueError('nope')

        bus.emit('Text', _Exploding())
        assert seen == []

    def test_close_unsubscribes(self, model_home):
        ctrl, bus, changes, launches = _wired(model_home)
        subs = list(ctrl._subs)
        ctrl.close()
        assert bus.unsubs == subs
        assert ctrl.connected is False


class _FakeGesture:
    def __init__(self):
        self.states = []

    def set_state(self, state):
        self.states.append(state)


class _FakeTileButton:
    def __init__(self, active=False):
        self.active = active
        self.css = []
        self.tooltip = None

    def get_active(self):
        return self.active

    def set_active(self, value):
        self.active = bool(value)

    def add_css_class(self, css):
        if css not in self.css:
            self.css.append(css)

    def remove_css_class(self, css):
        if css in self.css:
            self.css.remove(css)

    def set_tooltip_text(self, text):
        self.tooltip = text


class _FakeStateLabel:
    def __init__(self):
        self.text = ''

    def set_text(self, text):
        self.text = text


class TestLongPress:
    def _panel(self, ctrl, seen, monkeypatch):
        monkeypatch.setattr(quick_actions, 'Gtk', _FakeGtk)
        panel = object.__new__(quick_actions.QuickActionsPanel)
        panel._captions = ctrl
        panel._on_open_settings = seen.append
        return panel

    def test_long_press_opens_model_screen_not_settings(self, monkeypatch):
        seen = []
        ctrl = _FakeController()
        panel = self._panel(ctrl, seen, monkeypatch)
        panel._on_tile_long_press(_FakeGesture(), 0.0, 0.0, 'captions')
        assert ctrl.model_calls == [1]
        assert seen == []

    def test_long_press_without_controller_launches_overlay(
            self, monkeypatch):
        seen = []
        opened = []
        monkeypatch.setattr(captions, 'open_overlay',
                            lambda: opened.append(1) or True)
        panel = self._panel(None, seen, monkeypatch)
        panel._on_tile_long_press(_FakeGesture(), 0.0, 0.0, 'captions')
        assert opened == [1]
        assert seen == []


class TestTileToggleWiring:
    def _panel(self, ctrl):
        panel = object.__new__(quick_actions.QuickActionsPanel)
        panel._captions = ctrl
        panel._tile_buttons = {'captions': _FakeTileButton()}
        panel._state_labels = {'captions': _FakeStateLabel()}
        panel._tile_state = {}
        panel._updating = False
        return panel

    def test_toggle_routes_through_controller(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_captions_installed',
                            lambda: True)
        ctrl = _FakeController()
        panel = TestInstallGate()._bare_panel(ctrl)
        tiles = {t.key: t for t in panel._tiles()}
        btn = _FakeTileButton(active=True)
        panel._tile_buttons = {'captions': btn}
        panel._state_labels = {'captions': _FakeStateLabel()}
        panel._tile_state = {}
        panel._updating = False
        panel._on_tile_toggled(btn, tiles['captions'])
        assert ctrl.enabled is True
        assert panel._state_labels['captions'].text == 'Off'

    def test_refresh_paints_starting_label(self):
        ctrl = _FakeController()
        panel = self._panel(ctrl)
        ctrl.active = False
        panel._refresh_captions_ui()
        assert panel._state_labels['captions'].text == 'Off'
        assert 'active' not in panel._tile_buttons['captions'].css

    def test_refresh_clears_stale_highlight(self):
        ctrl = _FakeController()
        panel = self._panel(ctrl)
        ctrl.active = False
        panel._tile_buttons['captions'].css.append('active')
        panel._paint_captions_tile()
        assert panel._state_labels['captions'].text == 'Off'
        assert 'active' not in panel._tile_buttons['captions'].css


class _FakeGtkWidget:
    def __init__(self, *args, **kwargs):
        self.children = []
        self.css = []
        self.text = kwargs.get('label', '')
        self.active = False
        self.handlers = {}
        self.visible = True

    def append(self, widget):
        self.children.append(widget)

    def add_css_class(self, css):
        self.css.append(css)

    def set_hexpand(self, value):
        pass

    def set_ellipsize(self, mode):
        pass

    def connect(self, signal, callback):
        self.handlers[signal] = callback
        return 1

    def set_active(self, value):
        self.active = bool(value)

    def get_active(self):
        return self.active

    def set_text(self, text):
        self.text = text

    def set_label(self, text):
        self.text = text


class _FakeGtk:
    Box = _FakeGtkWidget
    Label = _FakeGtkWidget
    ToggleButton = _FakeGtkWidget
    Orientation = types.SimpleNamespace(HORIZONTAL=0, VERTICAL=1)
    EventSequenceState = types.SimpleNamespace(CLAIMED=1)


class _FakeSlidersBox:
    def __init__(self):
        self.children = []

    def append(self, widget):
        self.children.append(widget)


class TestNoCaptionsRow:
    def test_shade_has_no_captions_row(self):
        # Owner: drop the "CAPTIONS  No model  Get model" strip; the
        # Captions tile is the one entry point.
        assert not hasattr(quick_actions.QuickActionsPanel, '_build_captions_row')


class TestModelPresence:
    def test_empty_home_has_no_model(self, model_home):
        assert captions.any_model_installed(model_home) is False

    def test_partial_model_counts_as_missing(self, model_home):
        _install_model(model_home, complete=False)
        assert captions.any_model_installed(model_home) is False

    def test_complete_model_counts_as_installed(self, model_home):
        _install_model(model_home)
        assert captions.any_model_installed(model_home) is True

    def test_unknown_model_never_installed(self, model_home):
        assert captions.is_model_installed('nope-not-a-model',
                                           model_home) is False

    def test_presence_matches_reference(self, model_home,
                                        ref_xx_captions):
        from xx_captions import models as ref_models

        assert captions.any_model_installed(model_home) == \
            bool(ref_models.list_installed(model_home)) is False
        _install_model(model_home, complete=False)
        assert captions.any_model_installed(model_home) == \
            bool(ref_models.list_installed(model_home)) is False
        _install_model(model_home)
        assert captions.any_model_installed(model_home) == \
            bool(ref_models.list_installed(model_home)) is True


class TestPrivacyCaptureStream:
    def _captions_node(self, capture, state='running',
                       media_class='Stream/Input/Audio'):
        return {
            'id': 53,
            'type': 'PipeWire:Interface:Node',
            'info': {
                'state': state,
                'props': {
                    'node.name':
                        'alsa_output.platform-sound.HiFi__hw__0__sink',
                    'media.class': media_class,
                    **capture.STREAM_PROPS,
                },
            },
        }

    def test_captions_capture_classifies_as_mic(self, ref_xx_captions):
        from xx_captions import capture
        from xx_captions.dbus_api import BUS_NAME
        from privacy_monitor import classify_node

        assert capture.APP_ID == BUS_NAME
        assert capture.STREAM_PROPS['application.id'] == BUS_NAME
        assert classify_node(
            self._captions_node(capture)) == ('mic', 'xx-captions')

    def test_suspended_or_sink_nodes_stay_dark(self, ref_xx_captions):
        from xx_captions import capture
        from privacy_monitor import classify_node

        assert classify_node(
            self._captions_node(capture, state='suspended')) is None
        assert classify_node(
            self._captions_node(capture,
                               media_class='Audio/Sink')) is None


_DBUS_DRIVER = textwrap.dedent('''\
    """Real service + real shell controller on a private session bus."""
    import sys

    src_dir, ref_src, home = sys.argv[1], sys.argv[2], sys.argv[3]
    sys.path.insert(0, src_dir)
    sys.path.insert(0, ref_src)

    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib

    from pathlib import Path

    import captions
    from xx_captions import dbus_api

    node = Gio.DBusNodeInfo.new_for_xml(dbus_api.INTROSPECTION_XML)
    service = {'active': False, 'source': ''}

    def emit_changed(conn):
        conn.emit_signal(
            None, dbus_api.OBJECT_PATH,
            'org.freedesktop.DBus.Properties', 'PropertiesChanged',
            GLib.Variant('(sa{sv}as)', (
                dbus_api.INTERFACE,
                {'Active': GLib.Variant('b', service['active']),
                 'Source': GLib.Variant('s', service['source'])},
                [],
            )))

    def on_method(conn, sender, path, iface, method, params, invocation):
        if method == 'Start':
            (source,) = params.unpack()
            assert source == 'playback', source
            service.update(active=True, source=source)
            emit_changed(conn)
            conn.emit_signal(None, dbus_api.OBJECT_PATH,
                             dbus_api.INTERFACE, 'Text',
                             GLib.Variant('(sb)', ('hello', False)))
            invocation.return_value(GLib.Variant('()', ()))
        elif method == 'Stop':
            service.update(active=False)
            emit_changed(conn)
            invocation.return_value(GLib.Variant('()', ()))
        else:
            invocation.return_error_literal(
                'org.freedesktop.DBus.Error.UnknownMethod', method)

    def on_get(conn, sender, path, iface, prop):
        if prop == 'Active':
            return GLib.Variant('b', service['active'])
        if prop == 'Source':
            return GLib.Variant('s', service['source'])
        return None

    def on_set(conn, sender, path, iface, prop, value, invocation):
        invocation.return_error_literal(
            'org.freedesktop.DBus.Error.PropertyReadOnly', prop)

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    bus.register_object(dbus_api.OBJECT_PATH, node.interfaces[0],
                        on_method, on_get, on_set)
    owned = bus.call_sync(
        'org.freedesktop.DBus', '/org/freedesktop/DBus',
        'org.freedesktop.DBus', 'RequestName',
        GLib.Variant('(su)', (dbus_api.BUS_NAME, 0)),
        GLib.VariantType.new('(u)'),
        Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
    assert owned == 1, owned

    launches = []
    texts = []
    changes = []
    ctrl = captions.CaptionsController(
        on_change=lambda: changes.append(ctrl.tile_state),
        bus=bus, home=Path(home),
        launcher=lambda: launches.append(1) or True,
        on_text=lambda text, final: texts.append((text, final)))
    assert ctrl.connect()
    ctrl.set_enabled(True)

    loop = GLib.MainLoop()
    done = []

    def poll_on():
        if ctrl.tile_state == 'on' and ('hello', False) in texts:
            done.append('on')
            ctrl.set_enabled(False)
            GLib.timeout_add(50, poll_off)
            return False
        return True

    def poll_off():
        if ctrl.tile_state == 'off':
            done.append('off')
            loop.quit()
            return False
        return True

    def watchdog():
        if 'off' not in done:
            print('DRIVER FAIL: timed out in state',
                  ctrl.tile_state, texts, changes)
            sys.exit(1)
        return False

    GLib.timeout_add(50, poll_on)
    GLib.timeout_add(8000, watchdog)
    loop.run()
    assert done == ['on', 'off'], done
    assert launches == [1], launches
    assert texts == [('hello', False)], texts
    assert 'on' in changes and 'off' in changes, changes
    assert ctrl.source == 'playback', ctrl.source
    print('INTEGRATION OK')
    ''')


class TestLiveServiceRoundTrip:
    def test_start_stop_roundtrip(self, tmp_path, ref_xx_captions):
        if shutil.which('dbus-run-session') is None:
            pytest.skip('needs dbus-run-session')
        pytest.importorskip('gi')
        home = tmp_path / 'home'
        home.mkdir()
        _install_model(home)
        driver = tmp_path / 'captions_dbus_driver.py'
        driver.write_text(_DBUS_DRIVER)
        proc = subprocess.run(
            ['dbus-run-session', '--', sys.executable, str(driver),
             str(REPO / 'launcher' / 'src'), str(ref_xx_captions),
             str(home)],
            capture_output=True, text=True, timeout=90)
        assert 'INTEGRATION OK' in proc.stdout, (
            proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])


_BROADWAY_SMOKE = textwrap.dedent('''\
    """Real QuickActionsPanel on a Broadway display with a fake bus."""
    import glob
    import os
    import subprocess
    import sys
    import time

    src_dir, run_dir = sys.argv[1], sys.argv[2]
    display = ':61'
    os.environ['GDK_BACKEND'] = 'broadway'
    os.environ['BROADWAY_DISPLAY'] = display
    os.environ['XDG_RUNTIME_DIR'] = run_dir
    os.makedirs(run_dir, exist_ok=True)
    daemon = subprocess.Popen(
        ['gtk4-broadwayd', display],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _i in range(200):
            if glob.glob(os.path.join(run_dir, 'broadway*.socket')):
                break
            time.sleep(0.05)
        else:
            print('SMOKE FAIL: no broadway socket')
            sys.exit(1)
        sys.path.insert(0, src_dir)
        import gi
        gi.require_version('Gtk', '4.0')
        from gi.repository import GLib, Gtk

        import captions
        import quick_actions

        failures = []
        sys.excepthook = lambda *exc: failures.append(repr(exc[1]))

        from pathlib import Path

        home = Path(run_dir) / 'home'
        mid = next(iter(captions._MODEL_FILES))
        for name in captions._MODEL_FILES[mid]:
            target = (home / '.local' / 'share' / 'xx-captions'
                      / 'models' / mid / name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'x')

        class FakeParams:
            def __init__(self, values):
                self._values = values

            def unpack(self):
                return self._values

        class FakeResult:
            def __init__(self, reply=None, error=None):
                self._reply = reply
                self._error = error

        class FakeBus:
            def __init__(self):
                self.calls = []
                self.subs = []
                self._handlers = {}
                self._next = 1
                self._pending = []
                self.active = False

            def _reply_for(self, method):
                if method == 'GetAll':
                    return GLib.Variant('(a{sv})', ({
                        'Active': GLib.Variant('b', self.active),
                        'Source': GLib.Variant(
                            's', 'playback' if self.active else ''),
                    },))
                return GLib.Variant('()', ())

            def call(self, *args):
                callback = args[-1]
                method = args[3]
                if method == 'Start':
                    self.active = True
                elif method == 'Stop':
                    self.active = False
                self.calls.append(args)
                self._pending.append(
                    (callback, FakeResult(self._reply_for(method))))
                return 1

            def run_pending(self):
                pending, self._pending = self._pending, []
                for callback, result in pending:
                    callback(self, result)

            def call_finish(self, result):
                if result._error is not None:
                    raise result._error
                return result._reply

            def signal_subscribe(self, *args):
                sub = self._next
                self._next += 1
                self._handlers[sub] = args[-1]
                return sub

            def signal_unsubscribe(self, sub):
                pass

        fake = FakeBus()
        launches = []
        quick_actions._captions_installed = lambda: True
        quick_actions._make_captions_controller = (
            lambda on_change: captions.CaptionsController(
                on_change=on_change, bus=fake, home=home,
                launcher=lambda: launches.append(1) or True))

        win = Gtk.Window()
        panel = quick_actions.QuickActionsPanel()
        win.set_child(panel)
        win.present()
        assert 'captions' in panel._tile_buttons, (
            sorted(panel._tile_buttons))
        panel.expand(True)
        assert not hasattr(panel, '_captions_row_btn')  # no shade row

        def step_on():
            panel._tile_buttons['captions'].set_active(True)
            return False

        def step_run_start():
            fake.run_pending()
            fake.run_pending()
            return False

        def step_assert_on():
            label = panel._state_labels['captions'].get_text()
            assert label == 'On', label
            css = panel._tile_buttons['captions'].get_css_classes()
            assert 'active' in css, css
            panel._tile_buttons['captions'].set_active(False)
            return False

        def step_run_stop():
            fake.run_pending()
            fake.run_pending()
            return False

        def step_assert_off_and_long_press():
            label = panel._state_labels['captions'].get_text()
            assert label == 'Off', label

            class Gesture:
                def set_state(self, state):
                    pass

            panel._on_tile_long_press(Gesture(), 0.0, 0.0, 'captions')
            assert launches == [1, 1], launches
            loop.quit()
            return False

        def guard(step):
            def run():
                try:
                    return step()
                except Exception as exc:
                    failures.append(repr(exc))
                    loop.quit()
                    return False

            return run

        loop = GLib.MainLoop()
        GLib.timeout_add(200, guard(step_on))
        GLib.timeout_add(400, guard(step_run_start))
        GLib.timeout_add(600, guard(step_assert_on))
        GLib.timeout_add(800, guard(step_run_stop))
        GLib.timeout_add(1000, guard(step_assert_off_and_long_press))
        GLib.timeout_add(5000, guard(lambda: (
            failures.append('timeout'), loop.quit(), False)[2]))
        loop.run()
        assert not failures, failures
        print('SMOKE OK')
    finally:
        daemon.terminate()
    ''')


class TestBroadwayPanelSmoke:
    def test_real_panel_toggle_and_long_press(self, tmp_path):
        if shutil.which('gtk4-broadwayd') is None:
            pytest.skip('needs gtk4-broadwayd')
        pytest.importorskip('gi')
        run_dir = tmp_path / 'broadway'
        run_dir.mkdir()
        script = tmp_path / 'captions_broadway_smoke.py'
        script.write_text(_BROADWAY_SMOKE)
        proc = subprocess.run(
            [sys.executable, str(script), str(REPO / 'launcher' / 'src'),
             str(run_dir)],
            capture_output=True, text=True, timeout=120)
        assert 'SMOKE OK' in proc.stdout, (
            proc.returncode, proc.stdout[-2000:], proc.stderr[-2000:])
