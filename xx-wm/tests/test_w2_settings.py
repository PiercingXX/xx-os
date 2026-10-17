"""W2 settings tests: deep links, 320 px rows, Shell section, WiFi password.

window.py is imported against recorder fakes (no display): fake widgets
record the sizing calls that keep the settings page within 320 px, and bare
ShellWindow instances drive open_settings / _on_wifi_selected / page build.
"""
import inspect
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


class _FakeWidget:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.children = []
        self.classes = []
        self.props = {}
        self.handlers = {}

    def set_visible(self, value):
        self.props['visible'] = bool(value)

    def get_visible(self):
        return self.props.get('visible', True)

    def __getattr__(self, name):
        # Any other setter the settings row builders call is recorded.
        if name.startswith('set_'):
            return lambda *args: self.props.__setitem__(
                name[4:], args[0] if len(args) == 1 else args)
        raise AttributeError(name)

    @classmethod
    def new(cls, *args, **kwargs):
        return cls(*args, **kwargs)

    @classmethod
    def new_with_label(cls, text):
        return cls(label=text)

    def append(self, child):
        self.children.append(child)
        try:
            child._parent = self
        except AttributeError:
            pass

    def remove(self, child):
        if child in self.children:
            self.children.remove(child)

    def get_first_child(self):
        return self.children[0] if self.children else None

    def get_next_sibling(self):
        parent = getattr(self, '_parent', None)
        if parent is None:
            return None
        siblings = parent.children
        try:
            index = siblings.index(self)
        except ValueError:
            return None
        return siblings[index + 1] if index + 1 < len(siblings) else None

    def set_child(self, child):
        self.child = child

    def get_child(self):
        return getattr(self, 'child', None) or _FakeWidget()

    def add_css_class(self, name):
        self.classes.append(name)

    def remove_css_class(self, name):
        if name in self.classes:
            self.classes.remove(name)

    def add_controller(self, controller):
        self.props['controller'] = controller

    def connect(self, signal, callback, *args):
        self.handlers[signal] = (callback, args)
        return 1

    def set_hexpand(self, value):
        self.props['hexpand'] = value

    def set_vexpand(self, value):
        self.props['vexpand'] = value

    def set_xalign(self, value):
        self.props['xalign'] = value

    def set_halign(self, value):
        self.props['halign'] = value

    def set_valign(self, value):
        self.props['valign'] = value

    def set_can_focus(self, value):
        self.props['can_focus'] = value

    def set_size_request(self, width, height):
        self.props['size_request'] = (width, height)

    def set_name(self, name):
        self.props['name'] = name

    def get_name(self):
        return self.props.get('name', '')

    def set_margin_top(self, value):
        self.props['margin_top'] = value

    def set_margin_bottom(self, value):
        self.props['margin_bottom'] = value

    def set_margin_start(self, value):
        self.props['margin_start'] = value

    def set_margin_end(self, value):
        self.props['margin_end'] = value

    def set_policy(self, horizontal, vertical):
        self.props['policy'] = (horizontal, vertical)

    def set_wrap(self, value):
        self.props['wrap'] = value

    def set_wrap_mode(self, value):
        self.props['wrap_mode'] = value

    def set_max_width_chars(self, value):
        self.props['max_width_chars'] = value

    def set_ellipsize(self, value):
        self.props['ellipsize'] = value

    def set_placeholder_text(self, value):
        self.props['placeholder'] = value

    def set_text(self, value):
        self.props['text'] = value

    def get_text(self):
        if 'text' in self.props:
            return self.props['text']
        return self.kwargs.get('label', '')

    def set_visibility(self, value):
        self.props['visibility'] = value

    def set_width_chars(self, value):
        self.props['width_chars'] = value

    def set_active(self, value):
        self.props['active'] = bool(value)

    def get_active(self):
        return self.props.get('active', False)

    def get_allocation(self):
        return self.props.get('allocation', SimpleNamespace(x=0, y=0))

    def get_vadjustment(self):
        return self.props['vadjustment']


class _FakeEntry(_FakeWidget):
    pass


class _FakeAdjustment:
    def __init__(self):
        self.values = []

    def set_value(self, value):
        self.values.append(value)


class _FakeStack:
    def __init__(self):
        self.visible = None
        self.handlers = {}

    def set_visible_child_name(self, name):
        self.visible = name

    def connect(self, signal, callback, *args):
        self.handlers[signal] = (callback, args)
        return 1


@pytest.fixture
def window():
    """Import window.py against recorder fakes (the test_layer_policy idiom:
    save sys.modules, pop window, install fakes, restore afterwards)."""

    def require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError('no layer shell')
        return None

    gi = types.ModuleType('gi')
    gi.require_version = require_version
    repo = types.ModuleType('gi.repository')
    gi.repository = repo

    def make(name):
        return types.ModuleType(f'gi.repository.{name}')

    adw, gdk, glib, gtk, pango, gio = (
        make('Adw'), make('Gdk'), make('GLib'), make('Gtk'),
        make('Pango'), make('Gio'),
    )
    adw.ApplicationWindow = type('ApplicationWindow', (), {})

    class _FakeImage(_FakeWidget):
        @classmethod
        def new_from_icon_name(cls, name):
            image = cls()
            image.props['icon_name'] = name
            return image

        def set_pixel_size(self, px):
            self.props['pixel_size'] = px

    class _FakeGesture(_FakeWidget):
        def set_propagation_phase(self, phase):
            self.props['propagation_phase'] = phase

        def set_touch_only(self, touch_only):
            self.props['touch_only'] = touch_only

    class _FakeGrid(_FakeWidget):
        def set_column_homogeneous(self, homogeneous):
            self.props['column_homogeneous'] = homogeneous

        def attach(self, child, *args):
            self.append(child)

    class _FakeCssProvider:
        def __init__(self):
            self.data = []

        def load_from_data(self, data):
            self.data.append(bytes(data))

    class _FakeGtkAdjustment(_FakeWidget):
        def __init__(self, value=0.0, lower=0.0, upper=0.0,
                     step_increment=0.0, page_increment=0.0,
                     page_size=0.0):
            super().__init__(value=value, lower=lower, upper=upper,
                             step_increment=step_increment,
                             page_increment=page_increment,
                             page_size=page_size)
            self.props['adjustment_value'] = value

        def get_value(self):
            return self.props['adjustment_value']

        def set_value(self, value):
            self.props['adjustment_value'] = value

    class _FakeScale(_FakeWidget):
        @classmethod
        def new_with_range(cls, orientation, min_value, max_value, step):
            scale = cls()
            scale.props['range'] = (min_value, max_value, step)
            scale.props['adjustment_value'] = min_value
            return scale

        def set_digits(self, digits):
            self.props['digits'] = digits

        def set_draw_value(self, draw):
            self.props['draw_value'] = draw

        def get_value(self):
            adjustment = self.kwargs.get('adjustment')
            if adjustment is not None:
                return adjustment.get_value()
            return 0.0

        def set_value(self, value):
            adjustment = self.kwargs.get('adjustment')
            if adjustment is not None:
                adjustment.set_value(value)

    class _FakeScrolled(_FakeWidget):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.props['vadjustment'] = _FakeGtkAdjustment()

    class _FakeGtkStack(_FakeWidget):
        def add_named(self, child, name):
            self.append(child)
            self.props.setdefault('pages', {})[name] = child

        def set_visible_child_name(self, name):
            self.props['visible'] = name

        def get_visible_child_name(self):
            return self.props.get('visible', 'menu')

    gtk.Box = _FakeWidget
    gtk.Label = _FakeWidget
    gtk.Button = type('_FakeButtonW', (_FakeWidget,), {})
    gtk.Entry = _FakeEntry
    gtk.Image = _FakeImage
    gtk.ScrolledWindow = _FakeScrolled
    gtk.Switch = type('_FakeSwitchW', (_FakeWidget,), {})
    gtk.Stack = _FakeGtkStack
    gtk.StackTransitionType = SimpleNamespace(SLIDE_LEFT_RIGHT=0)
    gtk.InputPurpose = SimpleNamespace(PASSWORD=0, PIN=1, NUMBER=2)
    gtk.Grid = _FakeGrid
    gtk.CssProvider = _FakeCssProvider
    gtk.Adjustment = _FakeGtkAdjustment
    gtk.Scale = _FakeScale
    gtk.GestureClick = _FakeGesture
    gtk.Align = SimpleNamespace(START=0, END=1, CENTER=2, FILL=3)
    gtk.PropagationPhase = SimpleNamespace(
        NONE=0, CAPTURE=1, BUBBLE=2, TARGET=3)
    gtk.StyleContext = SimpleNamespace(
        add_provider_for_display=lambda *a, **k: None)
    gdk.Display = SimpleNamespace(get_default=lambda: None)
    gtk.Orientation = SimpleNamespace(HORIZONTAL=0, VERTICAL=1)
    gtk.PolicyType = SimpleNamespace(NEVER=0, AUTOMATIC=1)
    pango.WrapMode = SimpleNamespace(WORD=0, WORD_CHAR=1, CHAR=2)
    pango.EllipsizeMode = SimpleNamespace(NONE=0, START=1, MIDDLE=2, END=3)
    repo.Adw, repo.Gdk, repo.GLib, repo.Gtk, repo.Pango, repo.Gio = (
        adw, gdk, glib, gtk, pango, gio)
    glib.idle_add = lambda callback, *args: (callback(*args), 0)[1]
    glib.timeout_add = lambda _ms, callback, *args: (callback(*args), 0)[1]

    installed = {
        'gi': gi, 'gi.repository': repo,
        'gi.repository.Adw': adw, 'gi.repository.Gdk': gdk,
        'gi.repository.GLib': glib, 'gi.repository.Gtk': gtk,
        'gi.repository.Pango': pango, 'gi.repository.Gio': gio,
    }
    saved = {name: sys.modules.get(name) for name in installed}
    saved_window = sys.modules.pop('window', None)
    # xx_ui.widgets binds Gtk at import time and both sys.modules and the
    # package attribute cache it: drop both so section code re-binds to
    # this fixture's fakes instead of an earlier test file's.
    import xx_ui
    saved_widgets = sys.modules.pop('xx_ui.widgets', None)
    saved_widgets_attr = xx_ui.__dict__.pop('widgets', None)
    sys.modules.update(installed)
    import settings_page as _sp
    # The menu's slow summaries probe the real system (nmcli, pactl,
    # UPower) on worker threads that outlive the test; under this
    # fixture's fake gi they crashed the run when a later test swapped
    # the modules mid-call. No real probes from these page builds.
    saved_slow = _sp.slow_summaries
    _sp.slow_summaries = lambda: {}
    try:
        import window
        sys.modules.pop('window', None)
        yield window
    finally:
        _sp.slow_summaries = saved_slow
        for name, module in saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        if saved_window is not None:
            sys.modules['window'] = saved_window
        else:
            sys.modules.pop('window', None)
        import xx_ui
        if saved_widgets is not None:
            sys.modules['xx_ui.widgets'] = saved_widgets
        else:
            sys.modules.pop('xx_ui.widgets', None)
        if saved_widgets_attr is not None:
            xx_ui.widgets = saved_widgets_attr
        elif 'widgets' in xx_ui.__dict__:
            del xx_ui.__dict__['widgets']


def _bare(window):
    w = object.__new__(window.ShellWindow)
    w.gesture_config = SimpleNamespace(get=lambda key: 'none')
    w.app_index = SimpleNamespace(entries=[])
    w.config = SimpleNamespace(data={}, label_for=lambda app_id, name: name,
                               theme=window.THEME_PRESETS['amoled'])
    w._gesture_binding_labels = {}
    w._system_gesture_labels = {}
    return w


def _title_at(y):
    title = _FakeWidget(label='section', xalign=0)
    title.props['allocation'] = SimpleNamespace(x=0, y=y)
    return title


def _bare_open(window):
    w = _bare(window)
    w.stack = _FakeStack()
    w.presented = []
    w.present = lambda: w.presented.append(1)
    w._settings_section_titles = {
        'wifi': _title_at(120), 'bluetooth': _title_at(240),
        'mobile': _title_at(360),
    }
    w.adjustment = _FakeAdjustment()
    scroll = _FakeWidget()
    scroll.props['vadjustment'] = w.adjustment
    w._settings_scroll = scroll
    w.refreshed = []
    w._refresh_wifi = lambda: w.refreshed.append('wifi')
    w._refresh_bluetooth = lambda: w.refreshed.append('bluetooth')
    return w


class TestOpenSettings:
    def test_wifi_scrolls_and_scans(self, window):
        w = _bare_open(window)
        w.open_settings('wifi')
        assert w.stack.visible == 'settings'
        assert w.presented == [1]
        assert w.adjustment.values and w.adjustment.values[-1] == 120.0
        assert w.refreshed == ['wifi']

    def test_bluetooth_scrolls_and_lists(self, window):
        w = _bare_open(window)
        w.open_settings('bluetooth')
        assert w.stack.visible == 'settings'
        assert w.adjustment.values and w.adjustment.values[-1] == 240.0
        assert w.refreshed == ['bluetooth']

    def test_mobile_lands_on_the_network_page(self, window):
        # The APN row lives on Network & internet: showing that page
        # refreshes its live rows (the Wi-Fi scan) like any visit does.
        w = _bare_open(window)
        w.open_settings('mobile')
        assert w.adjustment.values and w.adjustment.values[-1] == 360.0
        assert w.refreshed == ['wifi']

    def test_none_opens_top_without_scroll_or_refresh(self, window):
        w = _bare_open(window)
        w.open_settings(None)
        assert w.stack.visible == 'settings'
        assert w.presented == [1]
        assert w.adjustment.values == []
        assert w.refreshed == []

    def test_unknown_section_opens_top(self, window):
        w = _bare_open(window)
        w.open_settings('torch')
        assert w.stack.visible == 'settings'
        assert w.adjustment.values == []
        assert w.refreshed == []

    def test_deep_link_retries_scroll_past_layout(self, window):
        import sys
        w = _bare_open(window)
        glib = sys.modules['gi.repository.GLib']
        calls = []
        def _idle(callback, *args):
            calls.append(('idle', args))
            return callback(*args)

        def _timeout(ms, callback, *args):
            calls.append((ms, args))
            return callback(*args)

        glib.idle_add = _idle
        glib.timeout_add = _timeout
        w.open_settings('wifi')
        assert [ms for ms, _a in calls] == ['idle', 150, 300, 600, 1000,
                                            1500]
        assert all(args == ('wifi',) for _ms, args in calls)
        # One direct scroll from _open_settings plus the immediate pass,
        # the idle pass and the five scheduled retries.
        assert w.adjustment.values == [120.0] * 8

    def test_shade_handler_is_open_settings(self, window):
        src = inspect.getsource(window.ShellWindow._ensure_shade)
        # The shade's Settings button must reach the shell's settings
        # opener: _open_settings hops over apps, and open_settings fans
        # out to it before refreshing the linked section.
        assert ('on_open_settings=self._open_settings' in src
                or 'on_open_settings=self.open_settings' in src)


def _built_page(window):
    import settings_page
    w = _bare(window)
    w.stack = _FakeStack()
    w.left = []
    w._leave_settings = lambda: w.left.append(1)
    root = w._build_settings_page()
    return w, root, settings_page


def _walk(widget):
    yield widget
    for child in getattr(widget, 'children', []):
        yield from _walk(child)
    direct = getattr(widget, 'child', None)
    if isinstance(direct, _FakeWidget):
        yield from _walk(direct)


class TestSettingsMenu:
    def test_every_section_sits_in_exactly_one_category(self):
        import settings_page
        placed = [key for _cat, _title, keys in
                  settings_page.SETTINGS_CATEGORIES for key in keys]
        registered = [key for key, _cls in settings_page.SETTINGS_SECTIONS]
        assert sorted(placed) == sorted(registered)
        assert len(set(placed)) == len(placed)
        grouped = [key for group in settings_page._MENU_GROUPS
                   for key in group]
        assert sorted(grouped) == sorted(
            cat for cat, _t, _k in settings_page.SETTINGS_CATEGORIES)

    def test_menu_and_one_subpage_per_category(self, window):
        w, root, settings_page = _built_page(window)
        assert 'xx-settings' in root.classes
        pages = w._settings_stack.props['pages']
        assert list(pages) == ['menu'] + [
            'cat-' + cat for cat, _t, _k in settings_page.SETTINGS_CATEGORIES]
        menu_titles = [node.kwargs.get('label') for node in
                       _walk(pages['menu'])]
        for _cat, title, _keys in settings_page.SETTINGS_CATEGORIES:
            assert title in menu_titles

    def test_every_deep_link_key_opens_its_subpage(self, window):
        w, _root, settings_page = _built_page(window)
        page = settings_page.SettingsPage
        helper = w._settings_page
        assert isinstance(helper, page)
        pages = w._settings_stack.props['pages']
        for key in settings_page._SETTINGS_SECTION_KEYS:
            category = settings_page.category_for(key)
            assert category is not None, key
            w.open_settings(key)
            assert w.stack.visible == 'settings', key
            assert w._settings_stack.get_visible_child_name() == (
                'cat-' + category), key
            title = w._settings_section_titles[key]
            assert title in list(_walk(pages['cat-' + category])), key
            assert w._settings_scroll is helper._scrolls[category], key

    def test_back_returns_to_menu_then_leaves(self, window):
        w, _root, _settings_page = _built_page(window)
        w._settings_page.show_category('network')
        w._settings_page.back()
        assert w._settings_stack.get_visible_child_name() == 'menu'
        assert w.left == []
        w._settings_page.back()
        assert w.left == [1]

    def test_no_inline_entries_and_one_primary_per_page(self, window):
        w, _root, _settings_page = _built_page(window)
        for name, child in w._settings_stack.props['pages'].items():
            nodes = list(_walk(child))
            assert not [n for n in nodes if isinstance(n, _FakeEntry)], name
            primaries = [n for n in nodes if 'xx-btn-primary' in n.classes]
            assert len(primaries) <= 1, name

    def test_gesture_rows_are_value_rows(self, window):
        w = _bare(window)
        card = w._build_gestures_card()
        assert 'xx-set-card' in card.classes
        # Long-press home + the four system swipes; the home side swipes
        # moved to Home & launcher (Android parity).
        assert len(card.children) == 5
        assert all('clicked' in row.handlers for row in card.children)
        assert set(w._gesture_binding_labels) == {'long_press_home'}
        assert len(w._system_gesture_labels) == 4


class TestSettingsListRows:
    def _texts(self, box):
        found = []

        def _walk(widget):
            label = widget.kwargs.get('label')
            if isinstance(label, str):
                found.append(label)
            for child in widget.children:
                _walk(child)
            if isinstance(getattr(widget, 'child', None), _FakeWidget):
                _walk(widget.child)

        _walk(box)
        return found

    def test_wifi_rows_show_state_and_signal(self, window):
        import settings_page
        from gi.repository import Gtk
        w = _bare(window)
        page = settings_page.SettingsPage(w)
        w._wifi_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        nets = [SimpleNamespace(ssid='Home', signal=80, security='wpa',
                                in_use=True),
                SimpleNamespace(ssid='Cafe', signal=40, security='open',
                                in_use=False)]
        page._show_wifi(nets)
        rows = w._wifi_list_box.children
        assert len(rows) == 2
        assert all('clicked' in row.handlers for row in rows)
        assert all('xx-set-row' in row.classes for row in rows)
        texts = self._texts(w._wifi_list_box)
        assert 'Home' in texts
        assert 'Connected · 80%' in texts
        assert 'Cafe' in texts
        assert 'Open · 40%' in texts

    def test_bt_rows_show_state(self, window):
        import settings_page
        from gi.repository import Gtk
        w = _bare(window)
        page = settings_page.SettingsPage(w)
        w._bt_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        devs = [SimpleNamespace(name='Pods', address='AA:BB', paired=True,
                                connected=True),
                SimpleNamespace(name='', address='CC:DD', paired=False,
                                connected=False)]
        page._show_bt_devices(devs)
        texts = self._texts(w._bt_list_box)
        assert 'Pods' in texts
        assert 'Connected' in texts
        assert 'CC:DD' in texts

    def test_sink_rows_mark_default(self, window):
        import settings_page
        from gi.repository import Gtk
        w = _bare(window)
        page = settings_page.SettingsPage(w)
        w._sink_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sinks = [SimpleNamespace(description='Built-in Audio',
                                 is_default=True),
                 SimpleNamespace(description='USB Headset', is_default=False)]
        page._show_sinks(sinks)
        texts = self._texts(w._sink_list_box)
        assert 'Built-in Audio' in texts
        assert 'Default output' in texts


class TestShellSection:
    def test_shell_note_ends_the_system_page(self, window):
        w, _root, _settings_page = _built_page(window)
        system = w._settings_stack.props['pages']['cat-system']
        notes = [n for n in _walk(system) if 'xx-set-footnote' in n.classes]
        assert '~/.config/xx-wm/' in notes[-1].kwargs.get('label')


class TestWifiPasswordMode:
    def _bare_wifi(self, window):
        w = _bare(window)
        w.statuses = []
        w._show_status = w.statuses.append
        w.connected = []
        w._connect_wifi = lambda ssid, pwd, sec=None: w.connected.append(
            (ssid, pwd, sec))
        w.shown = []
        w.item_actions = SimpleNamespace(
            _show_entry_dialog=lambda *args, **kwargs: w.shown.append(
                (args, kwargs)))
        return w

    def test_secured_network_uses_password_mode(self, window):
        w = self._bare_wifi(window)
        net = SimpleNamespace(ssid='HomeNet', security='WPA2', in_use=False)
        w._on_wifi_selected(net, object())
        assert len(w.shown) == 1
        (args, kwargs) = w.shown[0]
        assert args[1] == 'Password for HomeNet'
        assert kwargs.get('password') is True
        assert w.connected == []

    def test_open_network_connects_directly(self, window):
        w = self._bare_wifi(window)
        net = SimpleNamespace(ssid='Cafe', security='open', in_use=False)
        w._on_wifi_selected(net, object())
        assert w.shown == []
        assert w.connected == [('Cafe', None, 'open')]

    def test_current_network_opens_menu(self, window):
        w = self._bare_wifi(window)
        w.menus = []
        w.item_actions = SimpleNamespace(
            _show_entry_dialog=lambda *args, **kwargs: w.shown.append(
                (args, kwargs)),
            _show_action_menu=lambda *args: w.menus.append(args))
        net = SimpleNamespace(ssid='HomeNet', security='WPA2', in_use=True)
        w._on_wifi_selected(net, object())
        assert w.shown == []
        assert w.connected == []
        assert w.statuses == []
        assert len(w.menus) == 1
        assert [label for label, _cb in w.menus[0][2]] == [
            'Disconnect', 'Forget', 'Details']
