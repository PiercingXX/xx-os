"""W1 notification pipeline + shade responsiveness (task w1_notifications).

One focused test per numbered item, all headless with a fake bus, a fake
daemon, and recording GLib/Gtk seams — mirroring the test_shade_notif_ownership
/ test_quick_tiles idiom. Threads are either driven through the worker seam
inline (deterministic) or synchronized with events + joins, never slept on.
"""
import sys
import threading
import types
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


class _ImportVariant:
    """Recording Variant for import time, with structural equality so payload
    assertions in sibling files hold no matter who imports first."""

    def __init__(self, *args):
        self.args = args

    def __eq__(self, other):
        return hasattr(other, 'args') and tuple(self.args) == tuple(
            other.args)

    def __hash__(self):
        return hash(repr(self.args))

    def __repr__(self):
        return f'_Variant{self.args!r}'


def _install_fake_gi() -> None:
    """Minimal gi stand-ins so the three modules import without a display."""
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None
    glib.idle_add = lambda *a, **k: None
    glib.Error = Exception
    glib.Variant = _ImportVariant
    glib.VariantType = lambda spec: spec

    gio = types.ModuleType('gi.repository.Gio')
    gio.DBusCallFlags = SimpleNamespace(NONE=0)
    gio.DBusSignalFlags = SimpleNamespace(NONE=0)

    gdk = types.ModuleType('gi.repository.Gdk')
    gtk = types.ModuleType('gi.repository.Gtk')
    gtk.Box = type('Box', (), {})
    gtk.Window = type('Window', (), {})

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
    sys.modules['gi.repository.Gdk'] = gdk
    sys.modules['gi.repository.Gtk'] = gtk


_saved_gi = {n: m for n, m in sys.modules.items()
             if n == 'gi' or n.startswith('gi.')}
_install_fake_gi()

import notif_daemon  # noqa: E402
import notification_shade  # noqa: E402
import quick_actions  # noqa: E402

for _name in [n for n in sys.modules if n == 'gi' or n.startswith('gi.')]:
    del sys.modules[_name]
sys.modules.update(_saved_gi)
del _saved_gi


# --- recording seams ---------------------------------------------------------

class _FakeGLib:
    """Controllable GLib: idle/timeout callbacks only run when drained."""

    Error = Exception

    def __init__(self) -> None:
        self.idle_queue: list = []
        self.timeouts: dict = {}
        self.removed: list = []
        self._next_id = 100

    def idle_add(self, callback, *args):
        self.idle_queue.append((callback, args))
        return len(self.idle_queue)

    def run_idle(self) -> None:
        queued, self.idle_queue = self.idle_queue, []
        for callback, args in queued:
            callback(*args)

    def timeout_add(self, ms, callback, *args):
        tid = self._next_id
        self._next_id += 1
        self.timeouts[tid] = (ms, callback, args)
        return tid

    def timeout_add_seconds(self, secs, callback, *args):
        return self.timeout_add(secs * 1000, callback, *args)

    def source_remove(self, tid):
        self.removed.append(tid)
        self.timeouts.pop(tid, None)
        return True

    class Variant:
        def __init__(self, *args):
            self.args = args

        def __repr__(self):
            return f'_Variant{self.args!r}'

    @staticmethod
    def VariantType(spec):
        return spec

    @staticmethod
    def markup_escape_text(text):
        return (str(text).replace('&', '&amp;').replace('<', '&lt;')
                .replace('>', '&gt;').replace("'", '&apos;')
                .replace('"', '&quot;'))


class _FakeParams:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return self._value


class _FakeBus:
    """Session/system bus stand-in with a scripted reply queue."""

    def __init__(self, replies=None, unique_name=':1.7'):
        self._replies = list(replies or [])
        self._unique = unique_name
        self.calls: list = []
        self.subscriptions: list = []
        self.signals: list = []

    def call_sync(self, *args):
        self.calls.append(args)
        if not self._replies:
            raise Exception('org.freedesktop.DBus.Error.NoReply')
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, _FakeParams):
            return reply
        if isinstance(reply, tuple):
            return _FakeParams(reply)
        return _FakeParams((reply,))

    def signal_subscribe(self, *args):
        self.subscriptions.append(args)
        return len(self.subscriptions)

    def emit_signal(self, *args):
        self.signals.append(args)

    def get_unique_name(self):
        return self._unique


class _FakeDaemon:
    def __init__(self, owns=False, unique=':1.7'):
        self._owns = owns
        self._unique = unique
        self.invoked: list = []
        self.closed: list = []

    def owns_name(self):
        return self._owns

    def unique_name(self):
        return self._unique

    def invoke_action(self, notif_id, key):
        self.invoked.append((notif_id, key))

    def close_from_shell(self, notif_id):
        self.closed.append(notif_id)


class _FakeLabel:
    def __init__(self, label='', xalign=0, wrap=False, max_width_chars=-1):
        self.label_text = label
        self.text = label
        self.markup = None
        self.classes: list = []
        self.visible = None
        self.ellipsize = None

    def set_text(self, text):
        self.text = text

    def set_ellipsize(self, mode):
        self.ellipsize = mode

    def set_markup(self, markup):
        self.markup = markup

    def add_css_class(self, css):
        self.classes.append(css)

    def set_visible(self, value):
        self.visible = value

    def set_hexpand(self, value):
        pass

    def set_halign(self, value):
        pass

    def set_margin_start(self, value):
        pass


class _FakeBox:
    def __init__(self, orientation=None, spacing=0):
        self.children: list = []
        self.controllers: list = []
        self.classes: list = []
        self.visible = None

    def append(self, child):
        self.children.append(child)

    def add_css_class(self, css):
        self.classes.append(css)

    def remove_css_class(self, css):
        if css in self.classes:
            self.classes.remove(css)

    def add_controller(self, controller):
        self.controllers.append(controller)

    def set_hexpand(self, value):
        pass

    def set_halign(self, value):
        pass

    def set_valign(self, value):
        pass

    def set_margin_top(self, value):
        pass

    def set_margin_start(self, value):
        pass

    def set_margin_end(self, value):
        pass

    def set_visible(self, value):
        self.visible = value


class _FakeButton:
    def __init__(self, label=''):
        self.label = label
        self.handlers: dict = {}
        self.classes: list = []

    def connect(self, signal, callback, *args):
        self.handlers[signal] = (callback, args)
        return 1

    def add_css_class(self, css):
        self.classes.append(css)

    def click(self):
        callback, _args = self.handlers['clicked']
        return callback(self)


class _FakeToggleButton(_FakeButton):
    def __init__(self):
        super().__init__('')
        self._active = False
        self.visible = True
        self.controllers: list = []

    def set_child(self, child):
        self.child = child

    def set_hexpand(self, value):
        pass

    def add_controller(self, controller):
        self.controllers.append(controller)

    def get_active(self):
        return self._active

    def set_active(self, value):
        self._active = value

    def set_visible(self, value):
        self.visible = value

    def remove_css_class(self, css):
        if css in self.classes:
            self.classes.remove(css)


class _FakeRow:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.child = None
        self._sibs: list = []

    def set_child(self, child):
        self.child = child

    def get_next_sibling(self):
        idx = self._sibs.index(self)
        return self._sibs[idx + 1] if idx + 1 < len(self._sibs) else None


class _FakeListBox:
    def __init__(self):
        self.rows: list = []

    def append(self, row):
        row._sibs = self.rows
        self.rows.append(row)

    def remove(self, row):
        self.rows.remove(row)

    def get_first_child(self):
        return self.rows[0] if self.rows else None


class _FakeGesture:
    def __init__(self, *args, **kwargs):
        self.handlers: dict = {}

    @classmethod
    def new(cls, *args, **kwargs):
        return cls()

    def connect(self, signal, callback, *args):
        self.handlers[signal] = (callback, args)
        return 1

    def set_touch_only(self, value):
        pass


class _FakeRevealer:
    def __init__(self):
        self.revealed = None

    def set_reveal_child(self, value):
        self.revealed = value


class _FakeVisibility:
    def __init__(self):
        self.visible = None

    def set_visible(self, value):
        self.visible = value


class _FakeSlider:
    def __init__(self):
        self.value = None

    def set_value(self, value):
        self.value = value

    def get_value(self):
        return self.value if self.value is not None else 0.0


class _FakeALS:
    def __init__(self, available=False):
        self._available = available

    def available(self):
        return self._available


class _FakeLocation:
    def __init__(self, available=True, active=False):
        self._available = available
        self._active = active
        self.set_calls: list = []

    def available(self):
        return self._available

    def is_active(self):
        return self._active

    def set_enabled(self, value):
        self.set_calls.append(value)


class _FakeInvocation:
    def __init__(self):
        self.value = None

    def return_value(self, value):
        self.value = value


def _pin_gtk(module, monkeypatch, extra=None):
    """Point a module's Gtk widget names at the recording fakes."""
    names = {
        'Label': _FakeLabel,
        'Box': _FakeBox,
        'Button': _FakeButton,
        'ToggleButton': _FakeToggleButton,
        'ListBoxRow': _FakeRow,
        'GestureClick': _FakeGesture,
        'GestureDrag': _FakeGesture,
        'GestureLongPress': _FakeGesture,
        'Orientation': SimpleNamespace(HORIZONTAL=0, VERTICAL=1),
        'Align': SimpleNamespace(START=0, END=1, CENTER=2, FILL=3),
    }
    if extra:
        names.update(extra)
    for name, obj in names.items():
        monkeypatch.setattr(module.Gtk, name, obj, raising=False)


def _bare_shade(**kwargs):
    shade = object.__new__(notification_shade.NotificationShade)
    shade._notifications = []
    shade._notif_daemon = kwargs.get('daemon')
    shade._is_own_daemon = kwargs.get('predicate')
    shade._external_hint = _FakeLabel()
    shade.list_box = _FakeListBox()
    shade._hide_timer_id = None
    shade._worker_threads = []
    shade._clock_timer_id = None
    return shade


def _bare_panel(**kwargs):
    panel = object.__new__(quick_actions.QuickActionsPanel)
    panel._dnd = None
    panel._focus = None
    panel._hud = None
    panel._als = kwargs.get('als') or _FakeALS()
    panel._location = kwargs.get('location') or _FakeLocation(available=False)
    panel._tile_buttons = {}
    panel._tile_state = {}
    panel._state_labels = {}
    panel._updating = False
    panel._hw_gate_cache = {}
    panel._worker_threads = []
    panel._slider_pending = {}
    panel._slider_timers = {}
    panel._syncing_sliders = False
    panel._slider_appliers = {}
    return panel


def _bare_daemon(monkeypatch, on_notify=None, on_close=None, bus=None):
    daemon = object.__new__(notif_daemon.NotificationDaemon)
    daemon._on_notify = on_notify if on_notify is not None else lambda *a: None
    daemon._on_close = on_close if on_close is not None else (lambda i: None)
    daemon._bus = bus
    daemon._reg_id = 0
    daemon._name_id = 0
    daemon._next_id = 1
    daemon._owns_name = False
    daemon._expire_timers = {}
    daemon._notif_hints = {}
    daemon._takes_actions = None
    glib = _FakeGLib()
    monkeypatch.setattr(notif_daemon, 'GLib', glib)
    return daemon, glib


def _notify_params(app='App', replaces=0, icon='', summary='S', body='B',
                   actions=None, hints=None, expire=-1):
    return _FakeParams((app, replaces, icon, summary, body,
                        actions if actions is not None else [],
                        hints if hints is not None else {}, expire))


def _preset(**overrides):
    base = dict(key='t', name='t', background='#101010', surface='#202020',
                surface_alt='#303030', border='#404040', foreground='#FFFFFF',
                muted='#AAAAAA', accent='#00FF00')
    base.update(overrides)
    return SimpleNamespace(**base)


def _inline_shade_worker(monkeypatch):
    """Drive the shade's worker seam synchronously with a draining GLib."""
    glib = _FakeGLib()
    monkeypatch.setattr(notification_shade, '_spawn_shade_thread',
                        lambda target: target())
    monkeypatch.setattr(notification_shade, 'GLib', glib)
    return glib


# --- item 1: our own daemon never reads as "external" ------------------------

class TestOwnershipComparesUniqueNames:
    def test_owner_equal_to_own_unique_name_is_not_external(
            self, monkeypatch):
        bus = _FakeBus(replies=[':1.7'], unique_name=':1.7')
        monkeypatch.setattr(notification_shade, '_session_bus', lambda: bus)
        monkeypatch.setattr(notification_shade, 'GLib', _FakeGLib())
        assert notification_shade._external_daemon_owns_notifications() is False

    def test_different_owner_is_external(self, monkeypatch):
        bus = _FakeBus(replies=[':1.9'], unique_name=':1.7')
        monkeypatch.setattr(notification_shade, '_session_bus', lambda: bus)
        monkeypatch.setattr(notification_shade, 'GLib', _FakeGLib())
        assert notification_shade._external_daemon_owns_notifications() is True

    def test_explicit_unique_name_overrides_bus(self, monkeypatch):
        bus = _FakeBus(replies=[':1.9'], unique_name=':1.7')
        monkeypatch.setattr(notification_shade, '_session_bus', lambda: bus)
        monkeypatch.setattr(notification_shade, 'GLib', _FakeGLib())
        assert notification_shade._external_daemon_owns_notifications(
            ':1.9') is False

    def test_unowned_name_is_not_external(self, monkeypatch):
        bus = _FakeBus(
            replies=[Exception('org.freedesktop.DBus.Error.NameHasNoOwner')])
        monkeypatch.setattr(notification_shade, '_session_bus', lambda: bus)
        monkeypatch.setattr(notification_shade, 'GLib', _FakeGLib())
        assert notification_shade._external_daemon_owns_notifications() is False

    def test_shade_trusts_own_daemon_without_bus_probe(self, monkeypatch):
        def _bomb():
            raise AssertionError('bus must not be probed')
        monkeypatch.setattr(notification_shade, '_session_bus', _bomb)
        shade = _bare_shade(daemon=_FakeDaemon(owns=True))
        shade._check_external_notif_daemon()
        assert shade._external_hint.visible is False

    def test_shade_predicate_is_authoritative(self, monkeypatch):
        def _bomb():
            raise AssertionError('bus must not be probed')
        monkeypatch.setattr(notification_shade, '_session_bus', _bomb)
        shade = _bare_shade(predicate=lambda: True)
        shade._check_external_notif_daemon()
        assert shade._external_hint.visible is False

    def test_shade_worker_compares_daemon_unique_name(self, monkeypatch):
        glib = _inline_shade_worker(monkeypatch)
        bus = _FakeBus(replies=[':1.7'], unique_name=':1.7')
        monkeypatch.setattr(notification_shade, '_session_bus', lambda: bus)
        shade = _bare_shade(daemon=_FakeDaemon(owns=False, unique=':1.7'))
        shade._check_external_notif_daemon()
        glib.run_idle()
        assert shade._external_hint.visible is False

    def test_shade_worker_flags_foreign_owner(self, monkeypatch):
        glib = _inline_shade_worker(monkeypatch)
        bus = _FakeBus(replies=[':1.9'], unique_name=':1.7')
        monkeypatch.setattr(notification_shade, '_session_bus', lambda: bus)
        shade = _bare_shade(daemon=_FakeDaemon(owns=False, unique=':1.7'))
        shade._check_external_notif_daemon()
        glib.run_idle()
        assert shade._external_hint.visible is True

    def test_check_dispatches_probe_off_main_thread(self, monkeypatch):
        spawned: list = []
        monkeypatch.setattr(notification_shade, '_spawn_shade_thread',
                            lambda target: spawned.append(target))
        monkeypatch.setattr(notification_shade, '_session_bus',
                            lambda: _FakeBus())
        shade = _bare_shade()
        shade._check_external_notif_daemon()
        assert len(spawned) == 1
        assert shade._external_hint.visible is None

    def test_slow_probe_does_not_block_caller(self, monkeypatch):
        release = threading.Event()
        entered = threading.Event()

        def _slow_bus():
            entered.set()
            assert release.wait(10)
            return _FakeBus(replies=[':1.9'])

        monkeypatch.setattr(notification_shade, '_session_bus', _slow_bus)
        glib = _FakeGLib()
        monkeypatch.setattr(notification_shade, 'GLib', glib)
        shade = _bare_shade()
        shade._check_external_notif_daemon()
        assert entered.wait(10)
        assert shade._external_hint.visible is None
        release.set()
        for thread in shade._worker_threads:
            thread.join(10)
        glib.run_idle()
        assert shade._external_hint.visible is True


# --- item 2: async probes, neutral tiles, cached gates -----------------------

class TestAsyncTileRefresh:
    def test_tiles_start_neutral_before_probe(self):
        import inspect
        assert quick_actions._TILE_NEUTRAL_TEXT == '…'
        src = inspect.getsource(quick_actions.QuickActionsPanel._make_tile)
        assert '_TILE_NEUTRAL_TEXT' in src

    def test_refresh_states_runs_off_main_thread(self, monkeypatch):
        panel = _bare_panel(location=_FakeLocation(available=True,
                                                   active=True))
        monkeypatch.setattr(quick_actions, '_nm_get', lambda prop: False)
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: None)
        monkeypatch.setattr(quick_actions, '_night_light_available',
                            lambda: True)
        monkeypatch.setattr(quick_actions, '_get_night_state',
                            lambda: False)
        spawned: list = []
        monkeypatch.setattr(quick_actions, '_spawn_worker_thread',
                            lambda target: spawned.append(target))
        panel.refresh_states()
        assert len(spawned) == 1
        assert panel._tile_state == {}
        glib = _FakeGLib()
        monkeypatch.setattr(quick_actions, 'GLib', glib)
        spawned[0]()
        glib.run_idle()
        assert panel._tile_state == {
            'wifi': False, 'data': False, 'saver': False,
            'airplane': True, 'location': True, 'night': False,
            'caffeine': False}

    def test_refresh_states_survives_worker_probe_failure(self, monkeypatch):
        panel = _bare_panel()
        monkeypatch.setattr(panel, '_query_tile_states',
                            lambda: (_ for _ in ()).throw(RuntimeError('bus')))
        glib = _FakeGLib()
        monkeypatch.setattr(quick_actions, 'GLib', glib)
        panel.refresh_states()
        for thread in panel._worker_threads:
            thread.join(10)
        assert glib.idle_queue == []
        assert panel._tile_state == {}

    def test_sync_core_stays_synchronous(self, monkeypatch):
        panel = _bare_panel(location=_FakeLocation(available=True,
                                                   active=True))
        monkeypatch.setattr(quick_actions, '_nm_get', lambda prop: False)
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: None)
        monkeypatch.setattr(quick_actions, '_night_light_available',
                            lambda: True)
        monkeypatch.setattr(quick_actions, '_get_night_state',
                            lambda: False)
        assert panel._refresh_all_states() is False
        assert panel._tile_state == {
            'wifi': False, 'data': False, 'saver': False,
            'airplane': True, 'location': True, 'night': False,
            'caffeine': False}

    def test_hardware_gates_cached_after_first_probe(self, monkeypatch):
        probes: list = []

        def _probe():
            probes.append(1)
            return True

        monkeypatch.setattr(quick_actions, '_nm_has_wifi_device', _probe)
        panel = _bare_panel(location=_FakeLocation(available=False))
        panel._tiles()
        panel._tiles()
        assert len(probes) == 1
        assert panel._hw_gate_cache['hotspot'] is True

    def test_build_skips_uncached_slow_gates(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_nm_has_wifi_device', lambda: True)
        panel = _bare_panel(location=_FakeLocation(available=True,
                                                   active=True))
        fast = {t.key for t in panel._tiles(include_slow_gates=False)}
        assert 'hotspot' not in fast
        assert 'location' not in fast
        full = {t.key for t in panel._tiles()}
        assert 'hotspot' in full
        assert 'location' in full

    def test_deferred_tiles_land_in_tier_order(self, monkeypatch):
        _pin_gtk(quick_actions, monkeypatch)
        panel = _bare_panel(location=_FakeLocation(available=True,
                                                   active=True))
        monkeypatch.setattr(quick_actions, '_nm_has_wifi_device', lambda: True)
        panel.tier2_grid = _FakeBox()
        refreshed: list = []
        monkeypatch.setattr(panel, 'refresh_states',
                            lambda: refreshed.append(1))
        panel._finish_slow_gates({'hotspot': True, 'location': True})
        assert panel._hw_gate_cache['hotspot'] is True
        assert panel._hw_gate_cache['location'] is True
        assert refreshed == [1]

    def test_expand_reads_sliders_off_main_thread(self, monkeypatch):
        panel = _bare_panel()
        panel.tier2_grid = _FakeVisibility()
        panel.sep = _FakeVisibility()
        panel.sliders_box = _FakeVisibility()
        bright, vol = _FakeSlider(), _FakeSlider()
        panel._bright_slider = bright
        panel._vol_slider = vol
        reads: list = []
        monkeypatch.setattr(
            quick_actions, '_read_slider_values',
            lambda: (reads.append(1), (33, 66))[1])
        spawned: list = []
        monkeypatch.setattr(quick_actions, '_spawn_worker_thread',
                            lambda target: spawned.append(target))
        panel.expand(True)
        assert panel.tier2_grid.visible is True
        assert len(spawned) == 1
        assert reads == []
        assert bright.value is None
        glib = _FakeGLib()
        monkeypatch.setattr(quick_actions, 'GLib', glib)
        spawned[0]()
        glib.run_idle()
        assert reads == [1]
        assert (bright.value, vol.value) == (33, 66)

    def test_expand_off_thread_end_to_end(self, monkeypatch):
        panel = _bare_panel()
        panel.tier2_grid = _FakeVisibility()
        panel.sep = _FakeVisibility()
        panel.sliders_box = _FakeVisibility()
        panel._bright_slider = _FakeSlider()
        panel._vol_slider = _FakeSlider()
        release = threading.Event()
        entered = threading.Event()

        def _slow_read():
            entered.set()
            assert release.wait(10)
            return (11, 22)

        monkeypatch.setattr(quick_actions, '_read_slider_values', _slow_read)
        glib = _FakeGLib()
        monkeypatch.setattr(quick_actions, 'GLib', glib)
        panel.expand(True)
        assert entered.wait(10)
        assert panel._bright_slider.value is None
        release.set()
        for thread in panel._worker_threads:
            thread.join(10)
        glib.run_idle()
        assert panel._bright_slider.value == 11
        assert panel._vol_slider.value == 22


# --- item 3: live tile state (refresh on show, radio signals, airplane) ------

class TestLiveTileState:
    def test_airplane_needs_both_radios_off(self, monkeypatch):
        values = {'WirelessEnabled': False, 'WwanEnabled': False}
        monkeypatch.setattr(quick_actions, '_nm_get', values.get)
        assert quick_actions._get_airplane_state() is True

    def test_airplane_off_when_either_radio_on(self, monkeypatch):
        values = {'WirelessEnabled': True, 'WwanEnabled': False}
        monkeypatch.setattr(quick_actions, '_nm_get', values.get)
        assert quick_actions._get_airplane_state() is False

    def test_airplane_unknown_when_service_down(self, monkeypatch):
        values = {'WirelessEnabled': False, 'WwanEnabled': None}
        monkeypatch.setattr(quick_actions, '_nm_get', values.get)
        assert quick_actions._get_airplane_state() is None

    def test_airplane_tile_reads_back_instead_of_dash(self, monkeypatch):
        tiles = {t.key: t for t in quick_actions._TILES}
        assert tiles['airplane'].get_state is quick_actions._get_airplane_state

    def test_show_shade_refreshes_tiles_and_cancels_hide(self, monkeypatch):
        glib = _FakeGLib()
        monkeypatch.setattr(notification_shade, 'GLib', glib)
        shade = _bare_shade()
        shade._datetime_label = _FakeLabel()
        shade._revealer = _FakeRevealer()
        shade._calendar_revealer = _FakeRevealer()
        presented: list = []
        hidden: list = []
        shade.present = lambda: presented.append(1)
        shade.hide = lambda: hidden.append(1)
        refreshed: list = []

        class _QA:
            def refresh_states(self):
                refreshed.append(1)

        shade.quick_actions = _QA()
        shade._clock_timer_id = None
        shade._hide_timer_id = 41
        shade.show_shade()
        assert glib.removed == [41]
        assert shade._hide_timer_id is None
        assert refreshed == [1]
        assert presented == [1]
        assert hidden == []

    def test_hardware_signal_subscription_watches_both_radios(
            self, monkeypatch):
        panel = _bare_panel()
        bus = _FakeBus()
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        monkeypatch.setattr(quick_actions, 'Gio', SimpleNamespace(
            DBusSignalFlags=SimpleNamespace(NONE=0)))
        panel._subscribe_hardware_signals()
        assert len(bus.subscriptions) == 2
        by_path = {sub[3]: sub for sub in bus.subscriptions}
        assert by_path['/org/freedesktop/NetworkManager'][2] == \
            'PropertiesChanged'
        assert by_path['/org/bluez/hci0'][2] == 'PropertiesChanged'

    def _fire(self, bus, path, iface, changed):
        callbacks = [sub[6] for sub in bus.subscriptions if sub[3] == path]
        assert callbacks, f'no subscription for {path}'
        for callback in callbacks:
            callback(None, None, path, None, None,
                     _FakeParams((iface, changed, [])), None)

    def test_nm_radio_change_refreshes_tiles(self, monkeypatch):
        panel = _bare_panel()
        bus = _FakeBus()
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        monkeypatch.setattr(quick_actions, 'Gio', SimpleNamespace(
            DBusSignalFlags=SimpleNamespace(NONE=0)))
        refreshed: list = []
        monkeypatch.setattr(panel, 'refresh_states',
                            lambda: refreshed.append(1))
        panel._subscribe_hardware_signals()
        self._fire(bus, '/org/freedesktop/NetworkManager',
                   'org.freedesktop.NetworkManager',
                   {'WirelessEnabled': False})
        assert refreshed == [1]

    def test_bluez_power_change_refreshes_tiles(self, monkeypatch):
        panel = _bare_panel()
        bus = _FakeBus()
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        monkeypatch.setattr(quick_actions, 'Gio', SimpleNamespace(
            DBusSignalFlags=SimpleNamespace(NONE=0)))
        refreshed: list = []
        monkeypatch.setattr(panel, 'refresh_states',
                            lambda: refreshed.append(1))
        panel._subscribe_hardware_signals()
        self._fire(bus, '/org/bluez/hci0', 'org.bluez.Adapter1',
                   {'Powered': True})
        assert refreshed == [1]

    def test_unrelated_property_change_ignores(self, monkeypatch):
        panel = _bare_panel()
        bus = _FakeBus()
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: bus)
        monkeypatch.setattr(quick_actions, 'Gio', SimpleNamespace(
            DBusSignalFlags=SimpleNamespace(NONE=0)))
        refreshed: list = []
        monkeypatch.setattr(panel, 'refresh_states',
                            lambda: refreshed.append(1))
        panel._subscribe_hardware_signals()
        self._fire(bus, '/org/freedesktop/NetworkManager',
                   'org.freedesktop.NetworkManager', {'Connectivity': 4})
        self._fire(bus, '/org/bluez/hci0', 'org.bluez.Adapter1',
                   {'Discoverable': True})
        assert refreshed == []

    def test_subscribe_without_bus_never_raises(self, monkeypatch):
        panel = _bare_panel()
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: None)
        panel._subscribe_hardware_signals()


# --- item 4: shade dismissals report back ------------------------------------

class TestDismissReportsBack:
    def test_dismiss_closes_over_daemon(self):
        shade = _bare_shade(daemon=_FakeDaemon())
        shade._notifications.append(
            notification_shade.Notification(7, 'App', 'S', 'B'))
        shade.dismiss(7)
        assert shade._notifications == []
        assert shade._notif_daemon.closed == [7]

    def test_internal_id_never_echo_closes(self):
        shade = _bare_shade(daemon=_FakeDaemon())
        shade._notifications.append(
            notification_shade.Notification(999901, 'System', 'S', 'B'))
        shade.dismiss(999901)
        assert shade._notifications == []
        assert shade._notif_daemon.closed == []

    def test_daemon_close_does_not_echo(self):
        shade = _bare_shade(daemon=_FakeDaemon())
        shade._notifications.append(
            notification_shade.Notification(5, 'App', 'S', 'B'))
        shade._on_dbus_closed(None, None, None, None, None,
                              _FakeParams((5, 2)), None)
        assert shade._notifications == []
        assert shade._notif_daemon.closed == []

    def test_clear_all_reports_each_app_id(self):
        shade = _bare_shade(daemon=_FakeDaemon())
        for nid in (5, 999901, 6):
            shade._notifications.append(
                notification_shade.Notification(nid, 'App', 'S', 'B'))
        shade.clear_all()
        assert shade._notifications == []
        assert shade._notif_daemon.closed == [5, 6]

    def test_dismiss_without_daemon_stays_silent(self):
        shade = _bare_shade()
        shade._notifications.append(
            notification_shade.Notification(5, 'App', 'S', 'B'))
        shade.dismiss(5)
        assert shade._notifications == []


# --- item 5: app actions ------------------------------------------------------

class TestDaemonActionParsing:
    def test_notify_keeps_action_pairs(self, monkeypatch):
        received: list = []
        daemon, glib = _bare_daemon(
            monkeypatch,
            on_notify=lambda *a: received.append(a),
            bus=_FakeBus())
        nid = daemon._handle_notify(_notify_params(
            actions=['default', 'Open', 'view', 'View']))
        glib.run_idle()
        assert nid == 1
        assert received[0][-1] == [('default', 'Open'), ('view', 'View')]

    def test_odd_trailing_key_is_dropped(self, monkeypatch):
        received: list = []
        daemon, glib = _bare_daemon(
            monkeypatch,
            on_notify=lambda *a: received.append(a),
            bus=_FakeBus())
        daemon._handle_notify(_notify_params(actions=['a', 'A', 'orphan']))
        glib.run_idle()
        assert received[0][-1] == [('a', 'A')]

    def test_six_arg_callback_keeps_working(self, monkeypatch):
        received: list = []
        daemon, glib = _bare_daemon(
            monkeypatch,
            on_notify=lambda i, a, s, b, d, h: received.append((i, a, s)),
            bus=_FakeBus())
        daemon._handle_notify(_notify_params(actions=['a', 'A']))
        glib.run_idle()
        assert received == [(1, 'App', 'S')]

    def test_capabilities_advertise_actions(self, monkeypatch):
        daemon, glib = _bare_daemon(monkeypatch, bus=_FakeBus())
        invocation = _FakeInvocation()
        daemon._on_method_call(None, None, None, None, 'GetCapabilities',
                              None, invocation)
        assert invocation.value.args[0] == '(as)'
        assert 'actions' in invocation.value.args[1][0]

    def test_invoke_action_emits_signal(self, monkeypatch):
        bus = _FakeBus()
        daemon, glib = _bare_daemon(monkeypatch, bus=bus)
        daemon.invoke_action(3, 'view')
        assert len(bus.signals) == 1
        sender, path, iface, name, variant = bus.signals[0]
        assert name == 'ActionInvoked'
        assert variant.args == ('(us)', (3, 'view'))

    def test_daemon_tracks_name_ownership(self, monkeypatch):
        daemon, _glib = _bare_daemon(monkeypatch)
        assert daemon.owns_name() is False
        daemon._on_name_acquired(None, 'org.freedesktop.Notifications')
        assert daemon.owns_name() is True
        daemon._on_name_lost(None, 'org.freedesktop.Notifications')
        assert daemon.owns_name() is False


def _row_labels(row):
    found: list = []

    def _walk(widget):
        if isinstance(widget, _FakeLabel):
            found.append(widget)
        for child in getattr(widget, 'children', []):
            _walk(child)

    _walk(row.child)
    return found


def _row_buttons(row):
    found: list = []

    def _walk(widget):
        if isinstance(widget, _FakeButton):
            found.append(widget)
        for child in getattr(widget, 'children', []):
            _walk(child)

    _walk(row.child)
    return found


class TestShadeActionRows:
    def _shade_with_row(self, monkeypatch, **kwargs):
        _pin_gtk(notification_shade, monkeypatch)
        shade = _bare_shade(daemon=kwargs.get('daemon'))
        shade.hide_shade = lambda: None
        shade.add_notification(11, 'App', 'Title', kwargs.get('body', ''),
                               kwargs.get('desktop_entry', ''),
                               kwargs.get('actions'),
                               kwargs.get('hints'))
        return shade

    def test_up_to_three_action_buttons_render(self, monkeypatch):
        shade = self._shade_with_row(
            monkeypatch, daemon=_FakeDaemon(),
            actions=[('k1', 'One'), ('k2', 'Two'), ('k3', 'Three'),
                     ('k4', 'Four')])
        buttons = [b for b in _row_buttons(shade.list_box.rows[0])
                   if b.label != '×']
        assert [b.label for b in buttons] == ['One', 'Two', 'Three']

    def test_default_action_is_not_rendered(self, monkeypatch):
        shade = self._shade_with_row(
            monkeypatch, daemon=_FakeDaemon(),
            actions=[('default', 'Open'), ('view', 'View')])
        buttons = [b for b in _row_buttons(shade.list_box.rows[0])
                   if b.label != '×']
        assert [b.label for b in buttons] == ['View']

    def test_action_button_invokes_key(self, monkeypatch):
        daemon = _FakeDaemon()
        shade = self._shade_with_row(
            monkeypatch, daemon=daemon, actions=[('view', 'View')])
        buttons = [b for b in _row_buttons(shade.list_box.rows[0])
                   if b.label != '×']
        buttons[0].click()
        assert daemon.invoked == [(11, 'view')]
        assert daemon.closed == [11]
        assert shade._notifications == []

    def test_resident_row_survives_action(self, monkeypatch):
        daemon = _FakeDaemon()
        shade = self._shade_with_row(
            monkeypatch, daemon=daemon, actions=[('view', 'View')],
            hints={'resident': True})
        buttons = [b for b in _row_buttons(shade.list_box.rows[0])
                   if b.label != '×']
        buttons[0].click()
        assert daemon.invoked == [(11, 'view')]
        assert daemon.closed == []
        assert len(shade._notifications) == 1

    def test_tap_invokes_default_instead_of_launching(self, monkeypatch):
        _pin_gtk(notification_shade, monkeypatch)
        launched: list = []

        class _AppInfo:
            def __init__(self, *args):
                pass

            @staticmethod
            def new(name):
                _AppInfo.last_name = name
                return _AppInfo()

            def launch(self, args, ctx):
                launched.append(1)
                return True

        monkeypatch.setattr(
            notification_shade, 'Gio',
            SimpleNamespace(DesktopAppInfo=_AppInfo))
        daemon = _FakeDaemon()
        shade = _bare_shade(daemon=daemon)
        shade.hide_shade = lambda: None
        shade.add_notification(12, 'App', 'T', 'B', 'some-app',
                               [('default', 'Open'), ('view', 'View')])
        shade._on_notif_tapped(shade._notifications[0])
        assert launched == []
        assert daemon.invoked == [(12, 'default')]
        assert daemon.closed == [12]
        assert shade._notifications == []

    def test_tap_without_default_launches_entry(self, monkeypatch):
        _pin_gtk(notification_shade, monkeypatch)
        launched: list = []

        class _AppInfo:
            def __init__(self, *args):
                pass

            @staticmethod
            def new(name):
                _AppInfo.last_name = name
                return _AppInfo()

            def launch(self, args, ctx):
                launched.append(1)
                return True

        monkeypatch.setattr(
            notification_shade, 'Gio',
            SimpleNamespace(DesktopAppInfo=_AppInfo))
        daemon = _FakeDaemon()
        shade = _bare_shade(daemon=daemon)
        shade.hide_shade = lambda: None
        shade.add_notification(13, 'App', 'T', 'B', 'some-app', [])
        shade._on_notif_tapped(shade._notifications[0])
        assert launched == [1]
        assert daemon.invoked == []
        assert daemon.closed == [13]

    def test_legacy_callback_actions_keep_working(self, monkeypatch):
        fired: list = []
        shade = self._shade_with_row(
            monkeypatch, daemon=_FakeDaemon(),
            actions=[('Update now', lambda: fired.append(1))])
        buttons = [b for b in _row_buttons(shade.list_box.rows[0])
                   if b.label != '×']
        assert [b.label for b in buttons] == ['Update now']
        buttons[0].click()
        assert fired == [1]
        assert shade._notifications == []

    def test_main_wiring_threads_actions(self):
        src = (Path(__file__).parent.parent / 'launcher' / 'src'
               / 'main.py').read_text(encoding='utf-8')
        assert src.count('actions if actions is not None else []') >= 2
        assert 'shade.dismiss(notif_id, notify_daemon=False)' in src

    def test_window_passes_daemon_to_shade(self):
        src = (Path(__file__).parent.parent / 'launcher' / 'src'
               / 'window.py').read_text(encoding='utf-8')
        assert 'notif_daemon=daemon' in src


# --- item 6: safe body markup -------------------------------------------------

class TestBodyMarkup:
    def test_allowed_tags_survive(self):
        out = notification_shade.notif_body_to_pango_markup(
            '<b>bold</b> <i>it</i> <u>un</u>')
        assert out == '<b>bold</b> <i>it</i> <u>un</u>'

    def test_untrusted_markup_is_escaped(self):
        out = notification_shade.notif_body_to_pango_markup(
            '<script>alert(1)</script>')
        assert '<script>' not in out
        assert '&lt;script&gt;' in out

    def test_link_keeps_text_only(self):
        out = notification_shade.notif_body_to_pango_markup(
            '<a href="http://example.com/x">click</a>')
        assert out == 'click'

    def test_bare_link_keeps_text_only(self):
        out = notification_shade.notif_body_to_pango_markup(
            'see <a>here</a> now')
        assert out == 'see here now'

    def test_unbalanced_tag_stays_literal(self):
        out = notification_shade.notif_body_to_pango_markup('<b>oops')
        assert '<b>' not in out
        assert '&lt;b&gt;' in out

    def test_plain_text_escapes_ampersands(self):
        out = notification_shade.notif_body_to_pango_markup('fish & chips')
        assert out == 'fish &amp; chips'

    def test_quotes_do_not_break_attributes(self):
        out = notification_shade.notif_body_to_pango_markup(
            'say "hi" <b>loud</b>')
        assert '<b>loud</b>' in out
        assert '"hi"' not in out

    def test_row_body_uses_markup_not_raw_text(self, monkeypatch):
        _pin_gtk(notification_shade, monkeypatch)
        shade = _bare_shade(daemon=_FakeDaemon())
        shade.add_notification(21, 'App', 'T', '<b>hi</b> & bye')
        marked = [lbl for lbl in _row_labels(shade.list_box.rows[0])
                  if lbl.markup is not None]
        assert len(marked) == 1
        assert marked[0].markup == '<b>hi</b> &amp; bye'


# --- item 7: expiry keeps shade history --------------------------------------

class TestExpiryKeepsHistory:
    def _notified(self, monkeypatch, **kwargs):
        received: list = []
        closed: list = []
        bus = _FakeBus()
        daemon, glib = _bare_daemon(
            monkeypatch,
            on_notify=lambda *a: received.append(a),
            on_close=closed.append,
            bus=bus)
        nid = daemon._handle_notify(_notify_params(**kwargs))
        glib.run_idle()
        assert received and received[0][0] == nid
        return daemon, glib, bus, nid, closed

    def test_expiry_reports_but_keeps_row(self, monkeypatch):
        daemon, glib, bus, nid, closed = self._notified(
            monkeypatch, expire=5000, hints={})
        assert daemon._expire_notification(nid) is False
        assert closed == []
        assert len(bus.signals) == 1
        _sender, _path, _iface, name, variant = bus.signals[0]
        assert name == 'NotificationClosed'
        assert variant.args == ('(uu)', (nid, 1))

    def test_transient_expiry_removes(self, monkeypatch):
        daemon, glib, bus, nid, closed = self._notified(
            monkeypatch, expire=5000, hints={'transient': True})
        assert daemon._expire_notification(nid) is False
        glib.run_idle()
        assert closed == [nid]
        assert len(bus.signals) == 1
        _sender, _path, _iface, name, variant = bus.signals[0]
        assert name == 'NotificationClosed'
        assert variant.args == ('(uu)', (nid, 1))

    def test_explicit_close_still_removes(self, monkeypatch):
        daemon, glib, bus, nid, closed = self._notified(
            monkeypatch, expire=-1, hints={})
        daemon._close_notification(bus, nid, 3)
        glib.run_idle()
        assert closed == [nid]

    def test_shade_ignores_expiry_broadcast(self):
        shade = _bare_shade(daemon=_FakeDaemon())
        shade._notifications.append(
            notification_shade.Notification(9, 'App', 'S', 'B'))
        shade._on_dbus_closed(None, None, None, None, None,
                              _FakeParams((9, 1)), None)
        assert len(shade._notifications) == 1
        assert shade._notif_daemon.closed == []

    def test_shade_honors_dismiss_broadcast(self):
        shade = _bare_shade(daemon=_FakeDaemon())
        shade._notifications.append(
            notification_shade.Notification(9, 'App', 'S', 'B'))
        shade._on_dbus_closed(None, None, None, None, None,
                              _FakeParams((9, 2)), None)
        assert shade._notifications == []
        assert shade._notif_daemon.closed == []


# --- item 8: show wins over a pending hide ------------------------------------

class TestHideTimer:
    def _shown_shade(self, monkeypatch):
        glib = _FakeGLib()
        monkeypatch.setattr(notification_shade, 'GLib', glib)
        shade = _bare_shade()
        shade._datetime_label = _FakeLabel()
        shade._revealer = _FakeRevealer()
        shade._calendar_revealer = _FakeRevealer()
        calls: list = []
        shade.present = lambda: calls.append('present')
        shade.hide = lambda: calls.append('hide')

        class _QA:
            def refresh_states(self):
                pass

        shade.quick_actions = _QA()
        return shade, glib, calls

    def test_show_cancels_pending_hide(self, monkeypatch):
        shade, glib, calls = self._shown_shade(monkeypatch)
        shade.hide_shade()
        assert shade._hide_timer_id is not None
        pending = dict(glib.timeouts)
        shade.show_shade()
        assert shade._hide_timer_id is None
        assert glib.removed == [next(iter(pending))]
        assert 'hide' not in calls
        assert 'present' in calls

    def test_hide_timer_eventually_hides(self, monkeypatch):
        shade, glib, calls = self._shown_shade(monkeypatch)
        shade.hide_shade()
        tid = shade._hide_timer_id
        _ms, callback, _args = glib.timeouts[tid]
        assert callback() is False
        assert calls == ['hide']
        assert shade._hide_timer_id is None

    def test_double_hide_replaces_timer(self, monkeypatch):
        shade, glib, calls = self._shown_shade(monkeypatch)
        shade.hide_shade()
        first = shade._hide_timer_id
        shade.hide_shade()
        assert shade._hide_timer_id != first
        assert glib.removed == [first]


# --- item 9: one theme provider, forwarded ------------------------------------

class TestThemeForwarding:
    def test_panel_apply_theme_reloads_single_provider(self):
        panel = _bare_panel()
        loaded: list = []

        class _Provider:
            def load_from_data(self, data):
                loaded.append(bytes(data))

        provider = _Provider()
        panel._theme_provider = provider
        # The slimmed panel sheet owns the sliders: distinct surface_alt
        # troughs prove each reload landed on the same provider.
        panel.apply_theme(_preset(surface_alt='#303030'))
        panel.apply_theme(_preset(surface_alt='#ccc4b4'))
        assert panel._theme_provider is provider
        assert b'#303030' in loaded[0]
        assert b'#ccc4b4' in loaded[1]

    def test_shade_apply_theme_forwards_to_panel(self, monkeypatch):
        monkeypatch.setattr(notification_shade, 'GLib', _FakeGLib())
        shade = _bare_shade()
        loaded: list = []

        class _Provider:
            def load_from_data(self, data):
                loaded.append(bytes(data))

        forwarded: list = []

        class _Panel:
            def apply_theme(self, preset):
                forwarded.append(preset)

        shade._theme_provider = _Provider()
        shade.quick_actions = _Panel()
        shade._config = SimpleNamespace(theme=_preset())
        preset = _preset(background='#0F1C2E')
        shade.apply_theme(preset)
        assert b'#0F1C2E' in loaded[0]
        assert forwarded == [preset]

    def test_shade_apply_theme_without_panel_stays_silent(self, monkeypatch):
        monkeypatch.setattr(notification_shade, 'GLib', _FakeGLib())
        shade = _bare_shade()
        loaded: list = []

        class _Provider:
            def load_from_data(self, data):
                loaded.append(bytes(data))

        shade._theme_provider = _Provider()
        shade._config = SimpleNamespace(theme=_preset())
        shade.apply_theme()
        assert b'#101010' in loaded[0]
