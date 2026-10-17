"""W2 notification shade + quick settings redesign (task w2_shade).

Text-first monochrome shade: full-screen scrim surface, no-truncate header,
quiet tiles with SSID/BT state lines, themed sliders, newest-first grouped
notifications. Headless via the recording-Gtk idiom from test_w1_notifications.
"""
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


def _install_fake_gi() -> None:
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None
    glib.idle_add = lambda *a, **k: None
    glib.Error = Exception
    glib.Variant = lambda *a, **k: None
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

import notification_shade  # noqa: E402
import quick_actions  # noqa: E402

for _name in [n for n in sys.modules if n == 'gi' or n.startswith('gi.')]:
    del sys.modules[_name]
sys.modules.update(_saved_gi)
del _saved_gi


# --- recording Gtk fakes ------------------------------------------------------

class _FakeBox:
    def __init__(self, orientation=None, spacing=0, **kwargs):
        self.orientation = orientation
        self.children = []
        self.controllers = []
        self.classes = []
        self.visible = None

    def append(self, child):
        self.children.append(child)

    def add_css_class(self, css):
        self.classes.append(css)

    def add_controller(self, controller):
        self.controllers.append(controller)

    def set_hexpand(self, value):
        self.hexpand = value

    def set_vexpand(self, value):
        self.vexpand = value

    def set_halign(self, value):
        self.halign = value

    def set_valign(self, value):
        self.valign = value

    def set_margin_top(self, value):
        pass

    def set_margin_start(self, value):
        pass

    def set_margin_end(self, value):
        pass

    def set_margin_bottom(self, value):
        pass

    def set_visible(self, value):
        self.visible = value


class _FakeLabel:
    def __init__(self, label='', xalign=0, wrap=False, max_width_chars=-1,
                 **kwargs):
        self.label_text = label
        self.text = label
        self.markup = None
        self.classes = []
        self.ellipsize = None
        self.visible = None

    def set_text(self, text):
        self.text = text

    def set_markup(self, markup):
        self.markup = markup

    def add_css_class(self, css):
        self.classes.append(css)

    def set_hexpand(self, value):
        pass

    def set_margin_start(self, value):
        pass

    def set_ellipsize(self, mode):
        self.ellipsize = mode

    def set_visible(self, value):
        self.visible = value

    def set_valign(self, value):
        pass

    def set_margin_bottom(self, value):
        pass


class _FakeButton:
    def __init__(self, label='', icon_name=None):
        self.label = label
        self.icon_name = icon_name
        self.tooltip = None
        self.visible = True
        self.handlers = {}
        self.classes = []
        self.child = None

    def set_icon_name(self, name):
        self.icon_name = name

    def set_valign(self, value):
        pass

    def connect(self, signal, callback, *args):
        self.handlers[signal] = (callback, args)
        return 1

    def set_label(self, label):
        self.label = label

    def get_label(self):
        return self.label

    def set_visible(self, value):
        self.visible = value

    def add_css_class(self, css):
        self.classes.append(css)

    def set_child(self, child):
        self.child = child

    def set_tooltip_text(self, text):
        self.tooltip = text

    def set_halign(self, value):
        pass

    def set_hexpand(self, value):
        pass

    def set_margin_top(self, value):
        pass

    def click(self):
        callback, _args = self.handlers['clicked']
        return callback(self)


class _FakeToggleButton(_FakeButton):
    def __init__(self):
        super().__init__('')
        self._active = False
        self.visible = True
        self.controllers = []

    def get_active(self):
        return self._active

    def set_active(self, value):
        self._active = value

    def set_visible(self, value):
        self.visible = value

    def add_controller(self, controller):
        self.controllers.append(controller)

    def remove_css_class(self, css):
        if css in self.classes:
            self.classes.remove(css)


class _FakeGesture:
    def __init__(self, *args, **kwargs):
        self.handlers = {}
        self.claimed = False
        self.touch_only = None

    @classmethod
    def new(cls, *args, **kwargs):
        return cls()

    def connect(self, signal, callback, *args):
        self.handlers[signal] = (callback, args)
        return 1

    def set_state(self, state):
        self.claimed = state

    def set_touch_only(self, value):
        self.touch_only = value

    def set_propagation_phase(self, phase):
        self.propagation_phase = phase


class _FakeRevealer:
    def __init__(self, **kwargs):
        self.child = None
        self.revealed = None

    def set_child(self, child):
        self.child = child

    def set_reveal_child(self, value):
        self.revealed = value

    def get_reveal_child(self):
        return bool(self.revealed)


class _FakeGrid:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.cells = []
        self.visible = None

    def attach(self, child, col, row, w, h):
        self.cells.append(child)

    def set_visible(self, value):
        self.visible = value

    def get_visible(self):
        return bool(self.visible)


class _FakeSeparator:
    def __init__(self, **kwargs):
        self.classes = []

    def add_css_class(self, css):
        self.classes.append(css)

    def set_margin_top(self, value):
        pass

    def set_margin_bottom(self, value):
        pass

    def set_visible(self, value):
        self.visible = value


class _FakeScroller:
    def __init__(self, **kwargs):
        self.child = None

    def set_policy(self, *args):
        pass

    def set_max_content_height(self, value):
        pass

    def set_propagate_natural_height(self, value):
        pass

    def set_child(self, child):
        self.child = child

    def add_css_class(self, css):
        pass


class _FakeRow:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.child = None
        self._sibs = []

    def set_child(self, child):
        self.child = child

    def get_next_sibling(self):
        idx = self._sibs.index(self)
        return self._sibs[idx + 1] if idx + 1 < len(self._sibs) else None


class _FakeListBox:
    def __init__(self):
        self.rows = []
        self.placeholder = None

    def append(self, row):
        row._sibs = self.rows
        self.rows.append(row)

    def remove(self, row):
        self.rows.remove(row)

    def get_first_child(self):
        return self.rows[0] if self.rows else None

    def set_placeholder(self, widget):
        self.placeholder = widget

    def add_css_class(self, css):
        self.classes = getattr(self, 'classes', []) + [css]


class _StubWidgets(types.ModuleType):
    """xx_ui.widgets stand-in: real Gtk segfaults without a display.

    Mirrors the real section_label contract (uppercased caption text plus
    the xx-caption/xx-section-label classes) so header assertions see
    what the phone renders.
    """

    @staticmethod
    def section_label(text):
        label = _FakeLabel(label=text.upper())
        label.add_css_class('xx-caption')
        label.add_css_class('xx-section-label')
        return label


class _FakeScale:
    def __init__(self):
        self.value = 0.0
        self.classes = []
        self.visible = None
        self.sensitive = True

    @classmethod
    def new_with_range(cls, *args):
        return cls()

    def add_css_class(self, css):
        self.classes.append(css)

    def set_hexpand(self, value):
        pass

    def set_draw_value(self, value):
        pass

    def set_tooltip_text(self, text):
        self.tooltip = text

    def set_value(self, value):
        self.value = value

    def get_value(self):
        return self.value

    def set_sensitive(self, value):
        self.sensitive = value

    def connect(self, signal, callback):
        return 1


class _FakeImage:
    def __init__(self, icon_name=None):
        self.icon_name = icon_name
        self.tooltip = None
        self.classes = []

    @classmethod
    def new_from_icon_name(cls, name):
        return cls(name)

    def set_pixel_size(self, px):
        self.pixel_size = px

    def add_css_class(self, css):
        self.classes.append(css)

    def set_tooltip_text(self, text):
        self.tooltip = text


class _FakeGLib:
    Error = Exception

    def __init__(self):
        self.idle_queue = []

    def idle_add(self, callback, *args):
        self.idle_queue.append((callback, args))
        return len(self.idle_queue)

    def run_idle(self):
        queued, self.idle_queue = self.idle_queue, []
        for callback, args in queued:
            callback(*args)


class _Provider:
    def __init__(self):
        self.loaded = []

    def load_from_data(self, data):
        self.loaded.append(bytes(data))


def _pin_shade_gtk(monkeypatch):
    names = {
        'Label': _FakeLabel,
        'Image': _FakeImage,
        'Box': _FakeBox,
        'Button': _FakeButton,
        'ToggleButton': _FakeToggleButton,
        'ListBoxRow': _FakeRow,
        'ListBox': _FakeListBox,
        'Revealer': _FakeRevealer,
        'Grid': _FakeGrid,
        'Separator': _FakeSeparator,
        'ScrolledWindow': _FakeScroller,
        'Scale': _FakeScale,
        'GestureClick': _FakeGesture,
        'GestureDrag': _FakeGesture,
        'GestureSwipe': _FakeGesture,
        'GestureLongPress': _FakeGesture,
        'EventControllerKey': _FakeGesture,
        'Orientation': SimpleNamespace(HORIZONTAL=0, VERTICAL=1),
        'PropagationPhase': SimpleNamespace(NONE=0, CAPTURE=1, BUBBLE=2, TARGET=3),
        'RevealerTransitionType': SimpleNamespace(SLIDE_DOWN=0),
        'Align': SimpleNamespace(START=0, END=1, CENTER=2, FILL=3),
        'PolicyType': SimpleNamespace(NEVER=0, AUTOMATIC=1),
        'EventSequenceState': SimpleNamespace(CLAIMED=2),
    }
    for name, obj in names.items():
        monkeypatch.setattr(notification_shade.Gtk, name, obj, raising=False)
        monkeypatch.setattr(quick_actions.Gtk, name, obj, raising=False)
    # The headers build through xx_ui.widgets, which binds Gtk at import
    # time: point both the module cache and the package attribute at the
    # stand-in (the test_w2_settings idiom) so no real widget is built.
    import xx_ui
    stub = _StubWidgets('xx_ui.widgets')
    monkeypatch.setitem(sys.modules, 'xx_ui.widgets', stub)
    monkeypatch.setattr(xx_ui, 'widgets', stub, raising=False)


def _bare_shade(**kwargs):
    shade = object.__new__(notification_shade.NotificationShade)
    shade._notifications = []
    shade._notif_daemon = kwargs.get('daemon')
    shade._is_own_daemon = kwargs.get('predicate')
    shade._on_open_settings = kwargs.get('on_open_settings')
    shade._external_hint = _FakeLabel()
    shade.list_box = _FakeListBox()
    shade._hide_timer_id = None
    shade._clock_timer_id = None
    shade._worker_threads = []
    shade.quick_actions = _FakeBox()
    return shade


def _bare_panel(**kwargs):
    panel = object.__new__(quick_actions.QuickActionsPanel)
    panel._dnd = None
    panel._focus = None
    panel._hud = None
    panel._als = kwargs.get('als') or SimpleNamespace(available=lambda: False)
    panel._location = kwargs.get('location') or SimpleNamespace(
        available=lambda: False, is_active=lambda: False,
        set_enabled=lambda v: None)
    panel._tile_buttons = {}
    panel._tile_state = {}
    panel._tile_details = {}
    panel._state_labels = {}
    panel._updating = False
    panel._hw_gate_cache = {}
    panel._worker_threads = []
    panel._slider_pending = {}
    panel._slider_timers = {}
    panel._syncing_sliders = False
    panel._slider_appliers = {}
    panel._on_open_settings = kwargs.get('on_open_settings')
    return panel


def _preset(**overrides):
    base = dict(key='t', name='t', background='#101010', surface='#202020',
                surface_alt='#303030', border='#404040', foreground='#FFFFFF',
                muted='#AAAAAA', accent='#00FF00')
    base.update(overrides)
    return SimpleNamespace(**base)


def _walk(widget, kind):
    found = []

    def _visit(node):
        if isinstance(node, kind):
            found.append(node)
        for child in getattr(node, 'children', []):
            _visit(child)
        child = getattr(node, 'child', None)
        if isinstance(child, (_FakeBox, _FakeLabel, _FakeButton)):
            _visit(child)

    _visit(widget)
    return found


# --- item 3: tile state text formatting (pure) ---------------------------------

class TestFormatTileState:
    def test_wifi_shows_ssid_while_on(self):
        assert quick_actions.format_tile_state('wifi', True, 'HomeNet') == 'HomeNet'

    def test_wifi_without_ssid_shows_on(self):
        assert quick_actions.format_tile_state('wifi', True, None) == 'On'
        assert quick_actions.format_tile_state('wifi', True, '') == 'On'

    def test_wifi_off_ignores_detail(self):
        assert quick_actions.format_tile_state('wifi', False, 'HomeNet') == 'Off'

    def test_bt_shows_device_name_while_on(self):
        assert quick_actions.format_tile_state('bt', True, 'Pixel 8') == 'Pixel 8'

    def test_bt_off(self):
        assert quick_actions.format_tile_state('bt', False, 'Pixel 8') == 'Off'
        assert quick_actions.format_tile_state('bt', False, None) == 'Off'

    def test_plain_on_off_tiles(self):
        for key in ('data', 'airplane', 'torch', 'dnd', 'focus', 'auto_br',
                    'location', 'hotspot', 'night'):
            assert quick_actions.format_tile_state(key, True) == 'On'
            assert quick_actions.format_tile_state(key, False) == 'Off'


# --- item 3: relative-time formatting (pure) -----------------------------------

class TestFormatRelTime:
    def test_now(self):
        now = datetime(2026, 10, 15, 12, 0, 0)
        assert notification_shade.format_rel_time(now, now) == 'now'
        assert notification_shade.format_rel_time(
            now - timedelta(seconds=59), now) == 'now'

    def test_minutes(self):
        now = datetime(2026, 10, 15, 12, 0, 0)
        assert notification_shade.format_rel_time(
            now - timedelta(minutes=5), now) == '5m'

    def test_hours(self):
        now = datetime(2026, 10, 15, 12, 0, 0)
        assert notification_shade.format_rel_time(
            now - timedelta(hours=2), now) == '2h'

    def test_days(self):
        now = datetime(2026, 10, 15, 12, 0, 0)
        assert notification_shade.format_rel_time(
            now - timedelta(days=3), now) == '3d'

    def test_future_is_now(self):
        now = datetime(2026, 10, 15, 12, 0, 0)
        assert notification_shade.format_rel_time(
            now + timedelta(minutes=1), now) == 'now'


# --- item 6: grouping (pure) ----------------------------------------------------

class TestGroupNotificationRuns:
    def _notif(self, app):
        return notification_shade.Notification(1, app, 'S', 'B')

    def test_consecutive_same_app_groups(self):
        groups = notification_shade.group_notification_runs(
            [self._notif('Signal'), self._notif('Signal')])
        assert len(groups) == 1
        assert len(groups[0]) == 2

    def test_repeat_after_other_app_starts_new_group(self):
        groups = notification_shade.group_notification_runs(
            [self._notif('Bank'), self._notif('Signal'),
             self._notif('Signal'), self._notif('Bank')])
        assert [len(g) for g in groups] == [1, 2, 1]
        assert [g[0].app_name for g in groups] == ['Bank', 'Signal', 'Bank']

    def test_empty(self):
        assert notification_shade.group_notification_runs([]) == []


# --- item 4: long-press section mapping ------------------------------------------

class TestTileSettingsSection:
    def test_mapping(self):
        assert quick_actions.tile_settings_section('wifi') == 'wifi'
        assert quick_actions.tile_settings_section('bt') == 'bluetooth'
        assert quick_actions.tile_settings_section('data') == 'mobile'
        assert quick_actions.tile_settings_section('airplane') == 'mobile'

    def test_everything_else_is_none(self):
        for key in ('torch', 'dnd', 'focus', 'auto_br', 'location',
                    'hotspot', 'nope'):
            assert quick_actions.tile_settings_section(key) is None


class TestTileLongPressWiring:
    def _tile_with_press(self, monkeypatch, key, callback):
        _pin_shade_gtk(monkeypatch)
        panel = _bare_panel(on_open_settings=callback)
        tile = next(t for t in quick_actions._TILES if t.key == key)
        # The tile body comes from the shared design-system builder (real
        # Gtk segfaults without a display); the press wiring is driven
        # through its own seam with a fake button.
        btn = _FakeToggleButton()
        panel._wire_tile_press(btn, tile)
        panel._tile_buttons[key] = btn
        presses = [c for c in btn.controllers
                   if 'pressed' in c.handlers]
        assert len(presses) == 1
        return panel, presses[0]

    def test_long_press_opens_mapped_section(self, monkeypatch):
        seen = []
        _, press = self._tile_with_press(
            monkeypatch, 'wifi', lambda section: seen.append(section))
        callback, args = press.handlers['pressed']
        callback(press, 0.0, 0.0, *args)
        assert seen == ['wifi']

    def test_long_press_without_section_passes_none(self, monkeypatch):
        seen = []
        _, press = self._tile_with_press(
            monkeypatch, 'torch', lambda section: seen.append(section))
        callback, args = press.handlers['pressed']
        callback(press, 0.0, 0.0, *args)
        assert seen == [None]

    def test_legacy_zero_arg_handler_stays_compatible(self, monkeypatch):
        fired = []

        def _legacy():
            fired.append(1)

        _, press = self._tile_with_press(monkeypatch, 'data', _legacy)
        callback, args = press.handlers['pressed']
        callback(press, 0.0, 0.0, *args)
        assert fired == [1]

    def test_focus_keeps_break_menu_not_settings(self, monkeypatch):
        _pin_shade_gtk(monkeypatch)
        panel = _bare_panel(on_open_settings=lambda section: None)
        tile = next(t for t in quick_actions._TILES if t.key == 'focus')
        btn = _FakeToggleButton()
        panel._wire_tile_press(btn, tile)
        panel._tile_buttons['focus'] = btn
        assert btn.controllers, 'focus tile lost its long-press'
        for controller in btn.controllers:
            for callback, _args in controller.handlers.values():
                assert callback != panel._on_tile_long_press


class TestOpenSettingsSectionCompat:
    def test_section_is_forwarded(self, monkeypatch):
        _pin_shade_gtk(monkeypatch)
        seen = []
        shade = _bare_shade(on_open_settings=lambda s: seen.append(s))
        shade.hide_shade = lambda: seen.append('hidden')
        shade._open_settings_section('wifi')
        assert seen == ['hidden', 'wifi']

    def test_zero_arg_handler_still_works(self):
        fired = []
        shade = _bare_shade(on_open_settings=lambda: fired.append(1))
        shade.hide_shade = lambda: None
        shade._open_settings_section('mobile')
        assert fired == [1]

    def test_missing_handler_is_silent(self):
        shade = _bare_shade(on_open_settings=None)
        shade.hide_shade = lambda: None
        shade._open_settings_section('wifi')


# --- items 1+2: surface, scrim, header --------------------------------------------

class TestShadeSurface:
    def _built(self, monkeypatch):
        _pin_shade_gtk(monkeypatch)
        shade = _bare_shade()
        shade._revealer = _FakeRevealer()
        root = shade._build_content()
        outer = shade._build_outer()
        return shade, root, outer

    def test_scrim_closes_on_tap(self, monkeypatch):
        shade, _root, _outer = self._built(monkeypatch)
        assert 'shade-scrim' in shade._scrim.classes
        clicks = [c for c in shade._scrim.controllers
                  if 'pressed' in c.handlers]
        assert len(clicks) == 1
        hidden = []
        shade.hide_shade = lambda: hidden.append(1)
        callback, args = clicks[0].handlers['pressed']
        callback(clicks[0], 1, 0.0, 0.0, *args)
        assert hidden == [1]

    def test_swipe_up_anywhere_closes(self, monkeypatch):
        shade, _root, outer = self._built(monkeypatch)
        swipes = [c for c in outer.controllers if 'swipe' in c.handlers]
        assert len(swipes) == 1
        hidden = []
        shade.hide_shade = lambda: hidden.append(1)
        callback, args = swipes[0].handlers['swipe']
        callback(swipes[0], 0.0, -300.0, *args)
        assert hidden == [1]
        callback(swipes[0], 0.0, 300.0, *args)
        assert hidden == [1]

    def test_header_labels_and_buttons_fit(self, monkeypatch):
        _shade, root, _outer = self._built(monkeypatch)
        labels = {lbl.label_text for lbl in _walk(root, _FakeLabel)}
        buttons = {btn.label for btn in _walk(root, _FakeButton)}
        tips = {btn.tooltip for btn in _walk(root, _FakeButton)}
        icons = {btn.icon_name for btn in _walk(root, _FakeButton)}
        assert 'QUICK SETTINGS' in labels
        assert 'NOTIFICATIONS' in labels
        assert 'Quick settings' not in labels
        assert 'Notifications' not in labels
        # Settings and Power are glyph buttons (named by tooltip); Clear
        # all stays caps text.
        assert 'CLEAR ALL' in buttons
        assert {'Settings', 'Power', 'Clear all'} <= tips
        assert {'xx-shade-settings-symbolic', 'xx-shade-power-symbolic',
                'xx-shade-expand-symbolic'} <= icons
        # One dismiss set only (scrim tap, swipe, Escape): no Close button.
        assert '▲ Close' not in buttons

    def test_headers_are_design_section_labels(self, monkeypatch):
        _shade, root, _outer = self._built(monkeypatch)
        headers = [lbl for lbl in _walk(root, _FakeLabel)
                   if lbl.label_text in ('QUICK SETTINGS', 'NOTIFICATIONS')]
        assert len(headers) == 2
        for header in headers:
            assert 'xx-section-label' in header.classes
            assert 'xx-caption' in header.classes

    def test_no_letter_spaced_truncation_classes(self, monkeypatch):
        _shade, root, _outer = self._built(monkeypatch)
        for btn in _walk(root, _FakeButton):
            assert 'shade-header' not in btn.classes, btn.label
        css = notification_shade.theme_css(_preset())
        assert 'shade-header' not in css
        # The design tracks the section captions at +18% on the shared
        # xx-section-label rule, scoped to the shade root.
        assert '.shade-root .xx-section-label' in css
        assert '0.18em' in css

    def test_window_and_scrim_rules_in_sheet(self):
        css = notification_shade.theme_css(_preset(background='#101010'))
        assert 'window.xx-wm-shade' in css
        assert 'background: transparent' in css
        assert '.shade-scrim' in css
        assert 'alpha(#101010, 0.85)' in css

    def test_no_close_button_anywhere(self, monkeypatch):
        # The merge-leftover Close button is gone: the shade dismisses via
        # scrim tap, swipe, or Escape, and the scrim carries no buttons.
        shade, root, _outer = self._built(monkeypatch)
        assert '▲ Close' not in {btn.label for btn in _walk(root, _FakeButton)}
        assert _walk(shade._scrim, _FakeButton) == []

    def test_layer_surface_source_contract(self):
        src = Path(__file__).parent.parent.joinpath(
            'launcher', 'src', 'notification_shade.py').read_text(
                encoding='utf-8')
        # The shade sits on OVERLAY so it stays above the launcher when
        # Settings hops it to TOP (and above full-screen recents).
        assert 'LayerShell.Layer.OVERLAY' in src
        assert 'LayerShell.Edge.BOTTOM, True' in src
        assert 'set_exclusive_zone(self, 0)' in src
        assert "add_css_class('xx-wm-shade')" in src


# --- item 6: newest-first, grouped rows, empty state -------------------------------

class TestShadeNotificationList:
    def _shade(self, monkeypatch):
        _pin_shade_gtk(monkeypatch)
        return _bare_shade()

    def _row_header_labels(self, row):
        content = row.child.children[0]
        headers = [c for c in content.children
                   if isinstance(c, _FakeBox) and len(c.children) == 2]
        texts = []
        for header in headers:
            texts.extend(lbl.label_text for lbl in header.children)
        return texts

    def test_newest_first(self, monkeypatch):
        shade = self._shade(monkeypatch)
        shade.add_notification(1, 'Signal', 'First', '')
        shade.add_notification(2, 'Bank', 'Second', '')
        assert [n.summary for n in shade._notifications] == ['Second', 'First']
        titles = [row.child.children[0].children[-1].label_text
                  for row in shade.list_box.rows]
        assert titles == ['Second', 'First']

    def test_grouped_rows_share_one_app_label(self, monkeypatch):
        shade = self._shade(monkeypatch)
        shade.add_notification(1, 'Signal', 'One', '')
        shade.add_notification(2, 'Signal', 'Two', '')
        assert len(shade.list_box.rows) == 2
        headers = [self._row_header_labels(row)
                   for row in shade.list_box.rows]
        assert headers[0][0] == 'SIGNAL'
        assert 'now' in headers[0]
        assert headers[1] == []

    def test_repeat_after_other_app_labels_again(self, monkeypatch):
        shade = self._shade(monkeypatch)
        shade.add_notification(1, 'Signal', 'One', '')
        shade.add_notification(2, 'Bank', 'Two', '')
        shade.add_notification(3, 'Signal', 'Three', '')
        headers = [self._row_header_labels(row)
                   for row in shade.list_box.rows]
        assert [h[0] if h else None for h in headers] == [
            'SIGNAL', 'BANK', 'SIGNAL']

    def test_empty_shade_hides_the_section(self, monkeypatch):
        # No "No notifications" line: an empty shade hides the whole
        # notifications section (header, CLEAR ALL, card).
        _pin_shade_gtk(monkeypatch)
        shade = _bare_shade()
        shade._revealer = _FakeRevealer()
        shade._build_content()
        assert shade.list_box.placeholder is None
        assert len(shade._notif_section) == 2

    def test_dismiss_keeps_grouping_honest(self, monkeypatch):
        shade = self._shade(monkeypatch)
        shade.add_notification(1, 'Signal', 'One', '')
        shade.add_notification(2, 'Signal', 'Two', '')
        shade.dismiss(2)
        assert len(shade.list_box.rows) == 1
        assert self._row_header_labels(shade.list_box.rows[0])[0] == 'SIGNAL'


# --- item 7: theme fan-out, single provider -----------------------------------------

class TestApplyThemeProviders:
    def test_shade_reloads_one_provider_and_forwards(self, monkeypatch):
        shade = _bare_shade()
        provider = _Provider()
        shade._theme_provider = provider
        forwarded = []
        shade.quick_actions = SimpleNamespace(
            apply_theme=lambda preset: forwarded.append(preset))
        shade._config = SimpleNamespace(theme=_preset())
        preset = _preset(background='#0F1C2E')
        shade.apply_theme(preset)
        shade.apply_theme(preset)
        assert shade._theme_provider is provider
        assert len(provider.loaded) == 2
        assert b'#0F1C2E' in provider.loaded[0]
        assert b'window.xx-wm-shade' in provider.loaded[0]
        assert b'.shade-scrim' in provider.loaded[0]
        assert forwarded == [preset, preset]

    def test_panel_reloads_one_provider(self):
        panel = _bare_panel()
        provider = _Provider()
        panel._theme_provider = provider
        panel._config = SimpleNamespace(theme=_preset())
        panel.apply_theme(_preset())
        panel.apply_theme(_preset())
        assert panel._theme_provider is provider
        assert len(provider.loaded) == 2
        # The slimmed panel sheet owns the sliders: the preset's
        # surface_alt trough proves the reload landed.
        assert b'#303030' in provider.loaded[0]

    def test_tile_css_is_quiet_not_accent_fill(self):
        # Tiles render through the shared xx-tile builder: quiet surface,
        # inverted (foreground fill) while on, never an accent fill.
        from xx_ui import css as _xx_css
        from xx_ui import theme as _xx_theme
        active = _xx_theme.Theme(
            key='t', background='#101010', surface='#202020',
            surface_alt='#303030', border='#404040', foreground='#FFFFFF',
            muted='#AAAAAA', accent='#00FF00', font_family='Sans',
            scale=1.0, dark=True)
        css = _xx_css.render(active)
        assert '.xx-tile' in css
        assert 'background-color: #00FF00' not in css
        assert '.xx-tile.active' in css

    def test_tiles_come_from_shared_builder(self):
        src = Path(__file__).parent.parent.joinpath(
            'launcher', 'src', 'quick_actions.py').read_text(
                encoding='utf-8')
        assert 'glyph_tile' in src
        assert 'qa-tile' not in src

    def test_slider_css_uses_theme_not_libadwaita(self):
        css = quick_actions.theme_css(_preset())
        for rule in ('.shade-slider trough', '.shade-slider highlight',
                     '.shade-slider slider'):
            assert rule in css, rule
        assert 'background: #303030' in css
        assert css.count('background: #FFFFFF') >= 2

    def test_grids_shrink_homogeneous(self):
        src = Path(__file__).parent.parent.joinpath(
            'launcher', 'src', 'quick_actions.py').read_text(
                encoding='utf-8')
        assert src.count('column_homogeneous=True') >= 2
        assert 'btn.set_hexpand(True)' in src


# --- item 5: sliders -----------------------------------------------------------------

class TestBrightnessRow:
    def test_hidden_without_backlight(self, monkeypatch):
        _pin_shade_gtk(monkeypatch)
        monkeypatch.setattr(quick_actions, '_has_backlight', lambda: False)
        panel = _bare_panel()
        panel.sliders_box = _FakeBox()
        panel._build_sliders()
        assert panel._bright_row.visible is False

    def test_shown_with_backlight(self, monkeypatch):
        _pin_shade_gtk(monkeypatch)
        monkeypatch.setattr(quick_actions, '_has_backlight', lambda: True)
        panel = _bare_panel()
        panel.sliders_box = _FakeBox()
        panel._build_sliders()
        assert panel._bright_row.visible is True
        glyphs = [img for row in panel.sliders_box.children
                  for img in row.children if isinstance(img, _FakeImage)]
        assert [g.tooltip for g in glyphs] == ['Brightness', 'Volume']
        assert [g.icon_name for g in glyphs] == [
            'xx-shade-brightness-symbolic', 'xx-shade-volume-symbolic']
        readouts = [lbl for lbl in _walk(panel.sliders_box, _FakeLabel)
                    if 'qa-readout' in lbl.classes]
        assert len(readouts) == 2
        assert 'shade-slider' in panel._bright_slider.classes
        assert 'shade-slider' in panel._vol_slider.classes

    def test_has_backlight_probes_sysfs(self, monkeypatch):
        import backlight
        monkeypatch.setattr(backlight, 'find', lambda root=None: None)
        assert quick_actions._has_backlight() is False
        monkeypatch.setattr(backlight, 'find', lambda root=None: object())
        assert quick_actions._has_backlight() is True


# --- item 3: worker-thread detail lookups ----------------------------------------------

class TestTileDetailLookups:
    def test_ssid_parses_active_network(self, monkeypatch):
        import shutil
        import system_status

        monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/nmcli')
        monkeypatch.setattr(system_status.subprocess, 'check_output',
                            lambda *a, **k: 'no:OtherNet\nyes:HomeNet\nno:Third\n')
        assert quick_actions._get_wifi_ssid() == 'HomeNet'

    def test_ssid_none_without_nmcli(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, 'which', lambda name: None)
        assert quick_actions._get_wifi_ssid() is None

    def test_ssid_none_when_offline(self, monkeypatch):
        import shutil
        import system_status

        monkeypatch.setattr(shutil, 'which', lambda name: '/usr/bin/nmcli')
        monkeypatch.setattr(system_status.subprocess, 'check_output',
                            lambda *a, **k: 'no:OtherNet\n')
        assert quick_actions._get_wifi_ssid() is None

    def _bt_bus(self, objects):
        class _Params:
            def unpack(self):
                return (objects,)

        class _Bus:
            def call_sync(self, *args):
                return _Params()

        return _Bus()

    def test_bt_name_from_connected_device(self, monkeypatch):
        objects = {'/org/bluez/hci0/dev_AA': {
            'org.bluez.Device1': {'Connected': True, 'Name': 'Pixel 8'}}}
        monkeypatch.setattr(quick_actions, '_dbus_system',
                            lambda: self._bt_bus(objects))
        monkeypatch.setattr(quick_actions, 'GLib', SimpleNamespace(
            Error=Exception, VariantType=lambda spec: spec))
        monkeypatch.setattr(quick_actions, 'Gio', SimpleNamespace(
            DBusCallFlags=SimpleNamespace(NONE=0)))
        assert quick_actions._get_bt_device_name() == 'Pixel 8'

    def test_bt_name_none_when_disconnected(self, monkeypatch):
        objects = {'/org/bluez/hci0/dev_AA': {
            'org.bluez.Device1': {'Connected': False, 'Name': 'Pixel 8'}}}
        monkeypatch.setattr(quick_actions, '_dbus_system',
                            lambda: self._bt_bus(objects))
        monkeypatch.setattr(quick_actions, 'GLib', SimpleNamespace(
            Error=Exception, VariantType=lambda spec: spec))
        monkeypatch.setattr(quick_actions, 'Gio', SimpleNamespace(
            DBusCallFlags=SimpleNamespace(NONE=0)))
        assert quick_actions._get_bt_device_name() is None

    def test_bt_name_none_without_bus(self, monkeypatch):
        monkeypatch.setattr(quick_actions, '_dbus_system', lambda: None)
        assert quick_actions._get_bt_device_name() is None

    def test_refresh_posts_details_off_thread(self, monkeypatch):
        _pin_shade_gtk(monkeypatch)
        panel = _bare_panel()
        panel._state_labels['wifi'] = _FakeLabel()
        monkeypatch.setattr(panel, '_query_tile_states',
                            lambda: {'wifi': True})
        monkeypatch.setattr(quick_actions, '_query_tile_details',
                            lambda: {'wifi': 'HomeNet'})
        spawned = []
        monkeypatch.setattr(quick_actions, '_spawn_worker_thread',
                            lambda target: spawned.append(target))
        glib = _FakeGLib()
        monkeypatch.setattr(quick_actions, 'GLib', glib)
        panel.refresh_states()
        assert len(spawned) == 1
        spawned[0]()
        glib.run_idle()
        assert panel._tile_details == {'wifi': 'HomeNet'}
        assert panel._state_labels['wifi'].text == 'HomeNet'

    def test_detail_query_never_runs_on_main_thread(self):
        import inspect
        src = inspect.getsource(quick_actions.QuickActionsPanel.refresh_states)
        assert '_query_tile_details()' in src
        assert '_spawn_panel_worker' in src
        src = inspect.getsource(
            quick_actions.QuickActionsPanel._sync_sliders_to_live_state_async)
        assert '_spawn_panel_worker' in src
