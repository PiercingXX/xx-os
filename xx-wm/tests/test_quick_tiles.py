"""Tests for the Hotspot + Location shade tiles (WS25.5 + review fixes).

Hotspot turns ON via nmcli (`device wifi hotspot`, NM auto-creates whatever
profile it needs) but answers OFF/state/visibility entirely from NM's D-Bus
view: the active connection of Type 'ap' is resolved by object path and torn
down with Manager.DeactivateConnection. No profile-name assumption, no mixed
transports — gate, state query, and toggle all share one view.

Location registers an org.freedesktop.GeoClue2.Agent as a global master
switch (not per-app policy): while ON every client is granted up to EXACT
accuracy, while OFF MaxAccuracyLevel=0 denies everyone. Geoclue serves ONE
agent system-wide, so registration is lazy (first enable), rejection (e.g.
phosh's prompting agent owns the slot) hides the tile, disable unregisters to
free the slot, and the daemon's name owner is watched so the tile never
claims ON without a live registration across geoclue restarts.

Both tiles must hide when their backing service is absent and must survive the
service disappearing mid-session without crashing.

quick_actions imports gi.repository.Gdk/GLib/Gio/Gtk at module top level, but
PyGObject may be absent here; minimal fake modules are injected before import,
exactly like test_hud_silent does. Widget construction is never run — the tile
logic is exercised through module helpers and a bare QuickActionsPanel built
with object.__new__.
"""
import subprocess
import sys
import types


class _RecordingVariant:
    """Duck-typed GLib.Variant that remembers its constructor arguments so
    tests can assert exact D-Bus payloads."""

    def __init__(self, *args):
        self.args = args

    def __eq__(self, other):
        return (isinstance(other, _RecordingVariant)
                and self.args == other.args)

    def __repr__(self):
        return f'_Variant{self.args!r}'


def _install_fake_gi() -> None:
    """Make `from gi.repository import Gdk, GLib, Gio, Gtk` resolve to stubs."""
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.idle_add = lambda *a, **k: None
    glib.Error = Exception
    glib.Variant = _RecordingVariant
    glib.VariantType = lambda *a, **k: None

    gio = types.ModuleType('gi.repository.Gio')
    gio.DBusCallFlags = types.SimpleNamespace(NONE=0)
    gio.DBusSignalFlags = types.SimpleNamespace(NONE=0)

    for name in ('Gdk', 'Gtk'):
        sys.modules[f'gi.repository.{name}'] = types.ModuleType(
            f'gi.repository.{name}')
    # The class statement `class QuickActionsPanel(Gtk.Box)` evaluates its
    # base class at import time even though widgets are never constructed.
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


_install_fake_gi()

import quick_actions  # noqa: E402  (needs fake gi modules installed first)


# --- fakes -----------------------------------------------------------------

class _Reply:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return (self._value,)


class _Bus:
    """System-bus stand-in: call_sync answers from an ordered reply script;
    entries may be values (wrapped) or exceptions (raised)."""

    def __init__(self, replies=None):
        self.replies = list(replies or [])
        self.calls = []
        self.registrations = []
        self.unregistered = []
        self.signals = []
        self.defer = False
        self.pending = []

    def call_sync(self, *args):
        self.calls.append(args)
        if not self.replies:
            return _Reply(False)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, _Reply) else _Reply(reply)

    def call(self, *args):
        """Gio's async call: same 9 leading args, then a callback that
        gets (conn, result) and finishes with call_finish. The reply comes
        from the same script; deferred replies wait in self.pending."""
        *call_args, callback = args
        self.calls.append(tuple(call_args))
        if not self.replies:
            reply = _Reply(False)
        else:
            reply = self.replies.pop(0)
        if self.defer:
            self.pending.append((callback, reply))
        else:
            callback(self, reply)

    def call_finish(self, result):
        if isinstance(result, Exception):
            raise result
        return result if isinstance(result, _Reply) else _Reply(result)

    def register_object(self, *args):
        self.registrations.append(args)
        return len(self.registrations)

    def unregister_object(self, obj_id):
        self.unregistered.append(obj_id)

    def emit_signal(self, *args):
        self.signals.append(args)


class _FakeLocation:
    def __init__(self, available=True):
        self.available_flag = available
        self.enabled = False
        self.set_calls = []

    def available(self):
        return self.available_flag

    def is_active(self):
        return bool(self.enabled)

    def set_enabled(self, value):
        self.enabled = bool(value)
        self.set_calls.append(value)


class _FakeALS:
    def __init__(self, available=False):
        self._available = available

    def available(self):
        return self._available


def _bare_panel(location=None) -> quick_actions.QuickActionsPanel:
    panel = object.__new__(quick_actions.QuickActionsPanel)
    panel._dnd = None
    panel._focus = None
    panel._hud = None
    panel._als = _FakeALS()
    panel._location = location if location is not None else _FakeLocation()
    return panel


def _pin_dbus_seams(monkeypatch):
    """Pin GLib/Gio bindings on the imported quick_actions module itself.

    Which fake gi ends up bound depends on which test module first imports
    quick_actions during collection (see test_hud_silent), so tests that
    exercise D-Bus paths pin the exact seam objects they assert on.
    """
    class _FakeInterface:
        pass

    class _FakeNodeInfo:
        interfaces = [_FakeInterface()]

        @staticmethod
        def new_for_xml(xml):
            assert 'AuthorizeApp' in xml
            assert 'MaxAccuracyLevel' in xml
            return _FakeNodeInfo()

    watches = []

    def _watch_name(bus_type, name, flags, appeared, vanished):
        watches.append((bus_type, name, flags, appeared, vanished))
        return len(watches)

    glib = types.SimpleNamespace(
        Error=Exception,
        Variant=_RecordingVariant,
        VariantType=lambda *a, **k: None,
    )
    gio = types.SimpleNamespace(
        DBusCallFlags=types.SimpleNamespace(NONE=0),
        DBusSignalFlags=types.SimpleNamespace(NONE=0),
        DBusNodeInfo=_FakeNodeInfo,
        BusType=types.SimpleNamespace(SYSTEM='system'),
        BusNameWatcherFlags=types.SimpleNamespace(NONE=0),
        bus_watch_name=_watch_name,
        _watches=watches,
    )
    monkeypatch.setattr(quick_actions, 'GLib', glib)
    monkeypatch.setattr(quick_actions, 'Gio', gio)


def _panel_keys(panel):
    return [t.key for t in panel._tiles()]


# --- Hotspot gating ---------------------------------------------------------

class TestHotspotGating:
    def test_tile_appears_with_wifi_device(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_nm_has_wifi_device', lambda: True)
        assert 'hotspot' in _panel_keys(_bare_panel())

    def test_tile_hidden_without_wifi_device_or_nm(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_nm_has_wifi_device', lambda: False)
        assert 'hotspot' not in _panel_keys(_bare_panel())

    def test_gate_uses_nm_dbus_probe_family(self):
        """The gate must reuse the WiFi tile's D-Bus probing, not a new stack."""
        import inspect
        src = inspect.getsource(quick_actions._nm_has_wifi_device)
        assert '_dbus_system()' in src
        assert 'NetworkManager' in src
        assert 'DeviceType' in src


class TestHotspotToggleEnable:
    def test_enable_issues_nmcli_hotspot(self, monkeypatch):
        calls = []
        monkeypatch.setattr(subprocess, 'Popen',
                            lambda cmd, **k: calls.append(cmd))
        quick_actions._toggle_hotspot(True)
        assert calls == [['nmcli', 'device', 'wifi', 'hotspot']]

    def test_missing_nmcli_does_not_raise(self, monkeypatch):
        def _boom(cmd, **k):
            raise FileNotFoundError('nmcli')
        monkeypatch.setattr(subprocess, 'Popen', _boom)
        quick_actions._toggle_hotspot(True)


class TestHotspotDisableDBus:
    _AC = '/org/freedesktop/NetworkManager/ActiveConnection/7'

    def _bus_with_active(self, monkeypatch, actype='ap'):
        _pin_dbus_seams(monkeypatch)
        bus = _Bus(replies=[_Reply([self._AC]), _Reply(actype)])
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        return bus

    def test_disable_deactivates_resolved_ap_connection(self, monkeypatch):
        bus = self._bus_with_active(monkeypatch)
        quick_actions._toggle_hotspot(False)
        assert len(bus.calls) == 3
        deactivate = bus.calls[-1]
        assert deactivate[:4] == (
            'org.freedesktop.NetworkManager',
            '/org/freedesktop/NetworkManager',
            'org.freedesktop.NetworkManager', 'DeactivateConnection')
        assert deactivate[4].args == ('(o)', (self._AC,))

    def test_disable_works_without_any_profile_named_hotspot(
            self, monkeypatch):
        bus = self._bus_with_active(monkeypatch)
        assert getattr(quick_actions, '_HOTSPOT_CONN', None) is None
        quick_actions._toggle_hotspot(False)
        assert bus.calls[-1][3] == 'DeactivateConnection'
        assert bus.calls[-1][4].args == ('(o)', (self._AC,))

    def test_disable_ignores_client_wifi_connections(self, monkeypatch):
        bus = self._bus_with_active(monkeypatch, actype='802-11-wireless')
        quick_actions._toggle_hotspot(False)
        assert all(call[3] != 'DeactivateConnection' for call in bus.calls)

    def test_disable_without_active_connection_is_noop(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        bus = _Bus(replies=[_Reply([])])
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        quick_actions._toggle_hotspot(False)
        assert len(bus.calls) == 1

    def test_disable_without_bus_never_raises(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: None)
        quick_actions._toggle_hotspot(False)


class TestHotspotStateQuery:
    def test_active_ap_connection_reports_on(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        ac = '/org/freedesktop/NetworkManager/ActiveConnection/2'
        bus = _Bus(replies=[_Reply([ac]), _Reply('ap')])
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        assert quick_actions._get_hotspot_state() is True

    def test_client_wifi_only_reports_off(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        ac = '/org/freedesktop/NetworkManager/ActiveConnection/2'
        bus = _Bus(replies=[_Reply([ac]), _Reply('802-11-wireless')])
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        assert quick_actions._get_hotspot_state() is False

    def test_nm_down_reports_unknown(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: None)
        assert quick_actions._get_hotspot_state() is None

    def test_dbus_error_reports_unknown(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        bus = _Bus(replies=[Exception('NM went away')])
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        assert quick_actions._get_hotspot_state() is None

    def test_gate_state_and_toggle_share_one_dbus_view(self):
        """The visibility gate, the state query and the OFF toggle must all
        answer from the same NM D-Bus view — no nmcli side channel."""
        import inspect
        assert '_dbus_system()' in inspect.getsource(
            quick_actions._nm_has_wifi_device)
        state_src = inspect.getsource(quick_actions._get_hotspot_state)
        assert '_dbus_system' in state_src
        assert '_active_hotspot_path' in state_src
        assert 'nmcli' not in inspect.getsource(quick_actions._get_hotspot_state)
        assert '_active_hotspot_path' in inspect.getsource(
            quick_actions._toggle_hotspot)


# --- Location gating --------------------------------------------------------

class TestHardwareGatedTiles:
    def test_torch_hidden_without_led(self, monkeypatch):
        class _Empty:
            def glob(self, pattern):
                return iter([])

        monkeypatch.setattr(quick_actions, 'Path', lambda p: _Empty())
        assert 'torch' not in _panel_keys(_bare_panel())

    def test_auto_brightness_hidden_without_als(self):
        panel = _bare_panel()
        panel._als = _FakeALS(available=False)
        assert 'auto_br' not in _panel_keys(panel)

    def test_auto_brightness_shown_with_als(self):
        panel = _bare_panel()
        panel._als = _FakeALS(available=True)
        assert 'auto_br' in _panel_keys(panel)

    def test_tier1_always_includes_wifi_bt_data_airplane(self, monkeypatch):
        class _Empty:
            def glob(self, pattern):
                return iter([])

        monkeypatch.setattr(quick_actions, 'Path', lambda p: _Empty())
        monkeypatch.setattr(quick_actions, '_nm_has_wifi_device', lambda: False)
        keys = _panel_keys(_bare_panel())
        for key in ('wifi', 'bt', 'data', 'airplane'):
            assert key in keys
        assert 'torch' not in keys
        assert 'auto_br' not in keys
        assert 'hotspot' not in keys


class TestLocationGating:
    def test_tile_appears_when_geoclue_available(self):
        assert 'location' in _panel_keys(_bare_panel())

    def test_tile_hidden_when_geoclue_unavailable(self):
        assert 'location' not in _panel_keys(
            _bare_panel(location=_FakeLocation(available=False)))

    def test_expanded_tier_orders_both_after_auto_brightness(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_nm_has_wifi_device', lambda: True)
        monkeypatch.setattr(quick_actions, '_night_light_available',
                            lambda: True)

        class _ALS:
            def available(self):
                return True

        class _LedDir:
            def glob(self, pattern):
                return iter([object()])
        monkeypatch.setattr(quick_actions, 'Path', lambda p: _LedDir())
        panel = _bare_panel()
        panel._als = _ALS()
        keys = [t.key for t in panel._tiles()]
        assert keys[-4:] == ['auto_br', 'location', 'hotspot', 'night']

    def test_panel_toggle_routes_to_location_state(self):
        loc = _FakeLocation()
        panel = _bare_panel(location=loc)
        tiles = {t.key: t for t in panel._tiles()}
        assert tiles['location'].get_state() is False
        tiles['location'].set_state(True)
        assert loc.enabled is True


# --- Location mechanism (GeoClue2 agent) ------------------------------------

class TestLocationAgentMechanism:
    def test_enable_registers_agent_and_announces_accuracy(self, monkeypatch):
        bus = _Bus()
        _pin_dbus_seams(monkeypatch)
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        state = quick_actions.LocationState()

        state.set_enabled(True)

        assert state.enabled is True
        assert len(bus.registrations) == 1
        reg_path = bus.registrations[0][0]
        assert reg_path == quick_actions._AGENT_PATH
        register_call = bus.calls[-1]
        assert register_call[:4] == (
            'org.freedesktop.GeoClue2', '/org/freedesktop/GeoClue2/Manager',
            'org.freedesktop.GeoClue2.Manager', 'RegisterAgent')
        assert register_call[4].args == ('(o)', (reg_path,))
        signals = [(sig[2], sig[3], sig[4]) for sig in bus.signals]
        assert signals == [('org.freedesktop.DBus.Properties',
                            'PropertiesChanged',
                            _RecordingVariant(
                                '(sa{sv}as)',
                                ('org.freedesktop.GeoClue2.Agent',
                                 {'MaxAccuracyLevel': _RecordingVariant('u', 8)},
                                 [])))]

    def test_disabled_agent_revokes_everything(self):
        state = quick_actions.LocationState()
        assert state._max_accuracy() == 0
        assert state._allow(8) == 0
        assert state._allow(4) == 0

    def test_enabled_agent_grants_requested_accuracy_capped_at_exact(self):
        state = quick_actions.LocationState()
        state.enabled = True
        assert state._max_accuracy() == 8
        assert state._allow(4) == 4
        assert state._allow(99) == 8

    def test_authorize_app_returns_denial_while_disabled(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        class _Params:
            def unpack(self):
                return ('org.example.MapApp', 4)

        class _Invocation:
            def __init__(self):
                self.value = None

            def return_value(self, value):
                self.value = value
        state = quick_actions.LocationState()
        invocation = _Invocation()
        state._on_method_call(None, None, None, None, 'AuthorizeApp',
                              _Params(), invocation)
        assert invocation.value.args == ('(u)', (0,))

    def test_authorize_app_returns_requested_while_enabled(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        class _Params:
            def unpack(self):
                return ('org.example.MapApp', 4)

        class _Invocation:
            def __init__(self):
                self.value = None

            def return_value(self, value):
                self.value = value
        state = quick_actions.LocationState()
        state.enabled = True
        invocation = _Invocation()
        state._on_method_call(None, None, None, None, 'AuthorizeApp',
                              _Params(), invocation)
        assert invocation.value.args == ('(u)', (4,))

    def test_property_getter_tracks_max_accuracy_level(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        state = quick_actions.LocationState()
        prop = state._on_get_property(None, None, None, None,
                                      'MaxAccuracyLevel')
        assert prop.args == ('u', 0)
        state.enabled = True
        prop = state._on_get_property(None, None, None, None,
                                      'MaxAccuracyLevel')
        assert prop.args == ('u', 8)

    def test_geoclue_absent_means_hidden_and_never_registered(
            self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        bus = _Bus(replies=[Exception('ServiceUnknown')])
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        state = quick_actions.LocationState()
        assert state.available() is False
        assert bus.registrations == []

    def test_probe_is_read_only_never_registers(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        bus = _Bus(replies=[_Reply(False)])
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        state = quick_actions.LocationState()
        assert state.available() is True
        assert bus.registrations == []
        assert all(call[3] != 'RegisterAgent' for call in bus.calls)

    def test_no_bus_toggle_keeps_intent_without_raising(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: None)
        state = quick_actions.LocationState()
        state.set_enabled(True)
        assert state.enabled is True
        assert state.is_active() is False


# --- lazy registration (one agent slot, claimed only on enable) --------------

class TestLazyRegistration:
    def _state_with_bus(self, monkeypatch, replies=None):
        _pin_dbus_seams(monkeypatch)
        bus = _Bus(replies=list(replies or []))
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        return quick_actions.LocationState(), bus

    def test_first_enable_registers_lazily(self, monkeypatch):
        state, bus = self._state_with_bus(monkeypatch)
        assert bus.registrations == []
        state.set_enabled(True)
        registers = [call for call in bus.calls if call[3] == 'RegisterAgent']
        assert len(registers) == 1
        assert state.is_active() is True

    def test_rejection_cleans_up_and_hides_tile(self, monkeypatch):
        state, bus = self._state_with_bus(
            monkeypatch, replies=[Exception('Unauthorized')])
        state.set_enabled(True)
        assert state.enabled is False
        assert state.is_active() is False
        assert len(bus.registrations) == 1
        assert bus.unregistered == [1]
        assert state.available() is False

    def test_second_enable_after_slot_freed_retries(self, monkeypatch):
        state, bus = self._state_with_bus(
            monkeypatch, replies=[Exception('Unauthorized')])
        state.set_enabled(True)
        assert state.available() is False
        state.set_enabled(True)
        registers = [call for call in bus.calls if call[3] == 'RegisterAgent']
        assert len(registers) == 2
        assert state.is_active() is True
        assert state.available() is True


class TestDisableUnregistersAgent:
    def test_off_revokes_then_unregisters_and_frees_slot(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        bus = _Bus()
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        state = quick_actions.LocationState()
        state.set_enabled(True)
        state.set_enabled(False)

        assert state.is_active() is False
        unregister = bus.calls[-1]
        assert unregister[:4] == (
            'org.freedesktop.GeoClue2', '/org/freedesktop/GeoClue2/Manager',
            'org.freedesktop.GeoClue2.Manager', 'UnregisterAgent')
        assert unregister[4].args == ('(o)', (quick_actions._AGENT_PATH,))
        revoke = [(sig[2], sig[3], sig[4]) for sig in bus.signals][-1]
        assert revoke[2].args == ('(sa{sv}as)', (
            'org.freedesktop.GeoClue2.Agent',
            {'MaxAccuracyLevel': _RecordingVariant('u', 0)},
            [],
        ))
        assert bus.unregistered == [1]

    def test_off_leaves_daemon_available_for_a_future_enable(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        bus = _Bus()
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        state = quick_actions.LocationState()
        state.set_enabled(True)
        state.set_enabled(False)
        assert state.available() is True


# --- geoclue owner watch (restart mid-session) -------------------------------

class TestGeoclueOwnerWatch:
    def _wired(self, monkeypatch, replies=None, owner=':1.50'):
        _pin_dbus_seams(monkeypatch)
        bus = _Bus(replies=list(replies or []))
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        state = quick_actions.LocationState()
        _, name, _, appeared, vanished = quick_actions.Gio._watches[-1]
        assert name == 'org.freedesktop.GeoClue2'
        appeared(None, 'org.freedesktop.GeoClue2', owner)
        return state, bus, appeared, vanished

    def test_construction_watches_geoclue_name(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        quick_actions.LocationState()
        _, name, _, appeared, vanished = quick_actions.Gio._watches[-1]
        assert name == 'org.freedesktop.GeoClue2'
        assert callable(appeared)
        assert callable(vanished)

    def test_restart_resets_then_reregisters_while_on(self, monkeypatch):
        state, bus, appeared, vanished = self._wired(monkeypatch)
        state.set_enabled(True)
        assert state.is_active() is True

        vanished(None, 'org.freedesktop.GeoClue2')
        assert state.is_active() is False
        assert state.enabled is True

        appeared(None, 'org.freedesktop.GeoClue2', ':1.99')
        assert state.is_active() is True
        registers = [call for call in bus.calls if call[3] == 'RegisterAgent']
        assert len(registers) == 2
        assert len(bus.registrations) == 2
        assert all(reg[0] == quick_actions._AGENT_PATH
                   for reg in bus.registrations)
        assert bus.unregistered == [1]

    def test_restart_with_slot_taken_leaves_tile_hidden(self, monkeypatch):
        state, bus, appeared, vanished = self._wired(
            monkeypatch, replies=[_Reply(False), Exception('Unauthorized')])
        state.set_enabled(True)
        assert state.is_active() is True

        vanished(None, 'org.freedesktop.GeoClue2')
        appeared(None, 'org.freedesktop.GeoClue2', ':1.99')
        assert state.enabled is False
        assert state.is_active() is False
        assert state.available() is False
        assert len(bus.registrations) == 2

    def test_stays_unregistered_across_restart_while_off(self, monkeypatch):
        state, bus, appeared, vanished = self._wired(monkeypatch)
        state.set_enabled(True)
        state.set_enabled(False)
        assert any(call[3] == 'UnregisterAgent' for call in bus.calls)

        vanished(None, 'org.freedesktop.GeoClue2')
        appeared(None, 'org.freedesktop.GeoClue2', ':1.100')
        registers = [call for call in bus.calls if call[3] == 'RegisterAgent']
        assert len(registers) == 1
        assert state.is_active() is False

    def test_same_owner_repeat_appearance_does_not_reregister(
            self, monkeypatch):
        _, bus, appeared, _ = self._wired(monkeypatch, owner=':1.5')
        appeared(None, 'org.freedesktop.GeoClue2', ':1.5')
        assert bus.calls == []
        assert bus.registrations == []


# --- panel wiring for location honesty ---------------------------------------

class _FakeBtn:
    def __init__(self):
        self.visible = True
        self.active = False
        self.css = []

    def set_visible(self, value):
        self.visible = value

    def set_active(self, value):
        self.active = value

    def get_active(self):
        return self.active

    def add_css_class(self, css):
        self.css.append(css)

    def remove_css_class(self, css):
        if css in self.css:
            self.css.remove(css)


class _FakeLabel:
    def __init__(self):
        self.text = ''

    def set_text(self, text):
        self.text = text


class TestLocationPanelHonesty:
    def _panel_with_btn(self, loc):
        panel = _bare_panel(location=loc)
        panel._tile_buttons = {'location': _FakeBtn()}
        panel._state_labels = {'location': _FakeLabel()}
        panel._tile_state = {}
        panel._updating = False
        panel._spawn_panel_worker = lambda target: target()
        return panel

    def test_owner_change_hides_button_until_daemon_returns(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_post_to_main',
                            lambda cb, *args: cb(*args))
        class _Loc:
            def __init__(self):
                self.ok = True
                self.enabled = False

            def available(self):
                return self.ok

            def is_active(self):
                return self.enabled and self.ok

            def set_enabled(self, value):
                self.enabled = bool(value)

        loc = _Loc()
        panel = self._panel_with_btn(loc)
        btn = panel._tile_buttons['location']

        loc.ok = False
        panel._on_location_changed()
        assert btn.visible is False
        assert panel._tile_state['location'] is False
        assert panel._state_labels['location'].text == 'Off'

        loc.ok = True
        loc.enabled = True
        panel._on_location_changed()
        assert btn.visible is True
        assert panel._tile_state['location'] is True
        assert panel._state_labels['location'].text == 'On'

    def test_failed_enable_snaps_tile_back_off_immediately(
            self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        monkeypatch.setattr(quick_actions, '_nm_has_wifi_device', lambda: True)
        bus = _Bus(replies=[_Reply(False), Exception('Unauthorized')])
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        state = quick_actions.LocationState()
        panel = self._panel_with_btn(state)
        btn = panel._tile_buttons['location']
        tiles = {t.key: t for t in panel._tiles()}

        btn.set_active(True)
        panel._on_tile_toggled(btn, tiles['location'])

        assert state.available() is False
        assert btn.active is False
        assert 'active' not in btn.css
        assert panel._state_labels['location'].text == 'Off'

    def test_successful_enable_marks_tile_on(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        bus = _Bus()
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        state = quick_actions.LocationState()
        panel = self._panel_with_btn(state)
        btn = panel._tile_buttons['location']
        tiles = {t.key: t for t in panel._tiles()}

        btn.set_active(True)
        panel._on_tile_toggled(btn, tiles['location'])

        assert btn.active is True
        assert 'active' in btn.css
        assert panel._state_labels['location'].text == 'On'


# --- mid-session disappearance ----------------------------------------------

class TestServiceDisappearsMidSession:
    def test_refresh_survives_hotspot_query_dying_mid_session(
            self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_nm_has_wifi_device', lambda: True)
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: None)

        def _died():
            raise RuntimeError('NM went away')
        monkeypatch.setattr(quick_actions, '_get_hotspot_state', _died)

        panel = _bare_panel()
        panel._tile_buttons = {}
        panel._state_labels = {}
        panel._tile_state = {}
        panel._updating = False

        assert panel._refresh_all_states() is False
        assert panel._tile_state.get('location') is False

    def test_set_enabled_with_dead_service_never_raises(self, monkeypatch):
        bus = _Bus()
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        state = quick_actions.LocationState()
        state.set_enabled(True)

        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: None)
        state.set_enabled(False)
        assert state.enabled is False


# --- toggles paint what the system reads back ---------------------------------

class _FakeScale:
    def __init__(self, value=0):
        self.value = value

    def set_value(self, value):
        self.value = value


def _toggle_panel(monkeypatch, key, get_state, set_state=lambda v: None):
    import threading
    panel = _bare_panel()
    panel._tile_buttons = {key: _FakeBtn()}
    panel._state_labels = {key: _FakeLabel()}
    panel._tile_state = {key: False}
    panel._tile_details = {}
    panel._updating = False
    panel._tile_gen = {}
    panel._tile_inflight = set()
    jobs = []
    panel._spawn_panel_worker = jobs.append
    monkeypatch.setattr(quick_actions, '_post_to_main',
                        lambda cb, *args: cb(*args))
    setter_threads = []

    def _set(value):
        setter_threads.append(threading.current_thread())
        set_state(value)

    tile = quick_actions._TileDef(key, key, get_state, _set, 1)
    return panel, tile, jobs, setter_threads


class TestToggleTruth:
    def test_refused_wifi_toggle_turns_back_off(self, monkeypatch):
        panel, tile, jobs, setter_threads = _toggle_panel(
            monkeypatch, 'wifi', get_state=lambda: False)
        btn = panel._tile_buttons['wifi']
        btn.set_active(True)
        panel._on_tile_toggled(btn, tile)
        assert setter_threads == []
        assert len(jobs) == 1
        jobs.pop()()
        assert btn.active is False
        assert 'active' not in btn.css
        assert panel._tile_state['wifi'] is False

    def test_accepted_toggle_shows_read_back(self, monkeypatch):
        panel, tile, jobs, _threads = _toggle_panel(
            monkeypatch, 'bt', get_state=lambda: True)
        btn = panel._tile_buttons['bt']
        btn.set_active(True)
        panel._on_tile_toggled(btn, tile)
        jobs.pop()()
        assert btn.active is True and 'active' in btn.css

    def test_older_probe_never_flips_a_newer_tap(self, monkeypatch):
        panel, tile, jobs, _threads = _toggle_panel(
            monkeypatch, 'wifi', get_state=lambda: True)
        old_gens = dict(panel._tile_gen)
        btn = panel._tile_buttons['wifi']
        btn.set_active(True)
        panel._on_tile_toggled(btn, tile)
        jobs.pop()()
        panel._apply_queried_states({'wifi': False}, {}, old_gens)
        assert panel._tile_state['wifi'] is True

    def test_probe_waits_for_an_inflight_toggle(self, monkeypatch):
        panel, tile, jobs, _threads = _toggle_panel(
            monkeypatch, 'wifi', get_state=lambda: True)
        btn = panel._tile_buttons['wifi']
        btn.set_active(True)
        panel._on_tile_toggled(btn, tile)
        gens = dict(panel._tile_gen)
        panel._apply_queried_states({'wifi': False}, {}, gens)
        assert btn.active is True

    def test_stale_toggle_result_is_dropped(self, monkeypatch):
        panel, tile, jobs, _threads = _toggle_panel(
            monkeypatch, 'wifi', get_state=lambda: False)
        btn = panel._tile_buttons['wifi']
        btn.set_active(True)
        panel._on_tile_toggled(btn, tile)
        first = jobs.pop()
        btn.set_active(True)
        panel._on_tile_toggled(btn, tile._replace(get_state=lambda: True))
        jobs.pop()()
        first()
        assert panel._tile_state['wifi'] is True


class TestSliderUnderFinger:
    def test_live_read_skips_the_slider_being_dragged(self, monkeypatch):
        panel = _bare_panel()
        panel._bright_slider = _FakeScale(40)
        panel._vol_slider = _FakeScale(40)
        panel._readouts = {}
        panel._syncing_sliders = False
        panel._slider_pending = {}
        panel._slider_timers = {}
        panel._slider_touched = {}
        monkeypatch.setattr(quick_actions.GLib, 'timeout_add',
                            lambda *a: 1, raising=False)
        monkeypatch.setattr(quick_actions.GLib, 'source_remove',
                            lambda *a: None, raising=False)
        panel._queue_slider_apply('vol', 80)
        panel._vol_slider.value = 80
        panel._apply_slider_values(10, 20)
        assert panel._vol_slider.value == 80
        assert panel._bright_slider.value == 10

    def test_build_reads_sliders_off_the_loop(self):
        import inspect
        src = inspect.getsource(quick_actions.QuickActionsPanel._build)
        assert '_sync_sliders_to_live_state_async()' in src
        assert '_sync_sliders_to_live_state()' not in src


class TestLocationAgentAsync:
    def test_register_never_waits_for_geoclue(self, monkeypatch):
        _pin_dbus_seams(monkeypatch)
        bus = _Bus(replies=[Exception('Unauthorized')])
        bus.defer = True
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        changes = []
        state = quick_actions.LocationState(on_change=lambda: changes.append(1))
        state.set_enabled(True)
        assert state.is_active() is True
        assert len(bus.pending) == 1
        callback, reply = bus.pending.pop()
        callback(bus, reply)
        assert state.enabled is False
        assert state.is_active() is False
        assert bus.unregistered == [1]
        assert changes

    def test_agent_calls_are_never_call_sync(self):
        import inspect
        for fn in (quick_actions.LocationState._register,
                   quick_actions.LocationState._unregister):
            assert 'call_sync' not in inspect.getsource(fn)
