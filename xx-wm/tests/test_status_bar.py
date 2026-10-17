"""Top status bar (status_bar.py): the exclusive zone that keeps apps
clear of the FLX1's punch-hole camera.

Headless: no widget is constructed. The pure helpers are tested directly,
the layer setup against a recording fake LayerShell, and the GTK methods
(gesture, visibility) plus the window/main wiring as unbound methods on
hand-built namespaces (the test_w2_lock idiom). Modules import inside the
fixtures, after conftest has put the real gi stack back.
"""
import importlib
import json
import sys
import types
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
sys.path.insert(0, str(SRC))


@pytest.fixture
def sb():
    return importlib.import_module('status_bar')


class _Cfg:
    def __init__(self, **data):
        self.data = dict(data)

    def get(self, key, default=None):
        return self.data.get(key, default)


class _FakeLayerShell:
    """Records every Gtk4LayerShell call made against a window."""

    Layer = types.SimpleNamespace(BACKGROUND=0, BOTTOM=1, TOP=2, OVERLAY=3)
    Edge = types.SimpleNamespace(LEFT=0, RIGHT=1, TOP=2, BOTTOM=3)
    KeyboardMode = types.SimpleNamespace(NONE=0, EXCLUSIVE=1)

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _record(*args):
            self.calls.append((name,) + args[1:])
        return _record


class _FakeWindow:
    def __init__(self):
        self.sizes = []

    def set_default_size(self, w, h):
        self.sizes.append(('default', w, h))

    def set_size_request(self, w, h):
        self.sizes.append(('request', w, h))


# ---- height comes from config ------------------------------------------

class TestHeightFromConfig:
    def test_config_value_wins_over_device_default(self, sb):
        assert sb.resolve_height(_Cfg(status_bar_height=40),
                                 device='furiphone-flx1') == 40

    def test_unset_uses_device_default(self, sb):
        assert sb.resolve_height(_Cfg(), device='furiphone-flx1') \
            == sb.FLX1_HEIGHT
        assert sb.resolve_height(_Cfg(status_bar_height=None),
                                 device='librem-5') == sb.DEFAULT_HEIGHT

    def test_default_is_a_sensible_phone_bar(self, sb):
        assert 28 <= sb.DEFAULT_HEIGHT <= 32

    @pytest.mark.parametrize('raw', ['tall', [], {}, True, False])
    def test_garbage_falls_back_to_device_default(self, sb, raw):
        assert sb.resolve_height(_Cfg(status_bar_height=raw),
                                 device=None) == sb.DEFAULT_HEIGHT

    def test_clamped(self, sb):
        assert sb.resolve_height(_Cfg(status_bar_height=4), device=None) \
            == sb.MIN_HEIGHT
        assert sb.resolve_height(_Cfg(status_bar_height=999), device=None) \
            == sb.MAX_HEIGHT
        assert sb.resolve_height(_Cfg(status_bar_height='52'), device=None) \
            == 52

    def test_real_config_default_is_auto(self, sb, tmp_path, monkeypatch):
        monkeypatch.setenv('HOME', str(tmp_path))
        from config import DEFAULT_CONFIG, ShellConfig
        assert DEFAULT_CONFIG[sb.CONFIG_KEY] is None
        cfg_dir = tmp_path / '.config' / 'xx-wm'
        cfg_dir.mkdir(parents=True)
        (cfg_dir / 'config.json').write_text(
            json.dumps({sb.CONFIG_KEY: 58}), encoding='utf-8')
        assert sb.resolve_height(ShellConfig(), device=None) == 58

    def test_exclusive_zone_is_the_config_height(self, sb):
        ls = _FakeLayerShell()
        win = _FakeWindow()
        height = sb.resolve_height(_Cfg(status_bar_height=44), device=None)
        sb.configure_layer(win, height, ls)
        assert ('set_exclusive_zone', 44) in ls.calls
        assert ('request', -1, 44) in win.sizes
        assert ('set_layer', ls.Layer.TOP) in ls.calls
        anchors = {c[1]: c[2] for c in ls.calls if c[0] == 'set_anchor'}
        assert anchors == {ls.Edge.TOP: True, ls.Edge.LEFT: True,
                           ls.Edge.RIGHT: True, ls.Edge.BOTTOM: False}
        assert ('set_keyboard_mode', ls.KeyboardMode.NONE) in ls.calls

    def test_apply_config_moves_the_zone(self, sb, monkeypatch):
        ls = _FakeLayerShell()
        monkeypatch.setattr(sb, 'LayerShell', ls, raising=False)
        cfg = _Cfg(status_bar_height=30)
        bar = _FakeWindow()
        bar._config, bar._device, bar.height = cfg, None, 30
        cfg.data['status_bar_height'] = 70
        sb.StatusBar.apply_config(bar)
        assert bar.height == 70
        assert ls.calls == [('set_exclusive_zone', 70)]
        sb.StatusBar.apply_config(bar)  # unchanged: no re-layout
        assert len(ls.calls) == 1


class TestSettingsControl:
    def test_slider_writes_config_and_moves_the_bar_live(self):
        import settings_page  # noqa: F401  (first: sections bind _Section)
        import settings_display
        written, applied = {}, []
        win = types.SimpleNamespace(
            config=types.SimpleNamespace(
                set=lambda k, v: written.__setitem__(k, v)),
            _status_bar=types.SimpleNamespace(
                apply_config=lambda: applied.append(1)))
        label = types.SimpleNamespace(text='', set_text=lambda t: setattr(
            label, 'text', t))
        row = types.SimpleNamespace(value_label=label)
        scale = types.SimpleNamespace(get_value=lambda: 57.6)
        settings_display.DisplaySection._on_status_bar_height(
            None, win, scale, row)
        assert written == {'status_bar_height': 58}
        assert applied == [1]
        assert label.text == '58 px'


class TestFlx1Height:
    def test_calculation(self, sb):
        # 2 mm + 4 mm hole on a 129 mm / 1333 px panel: lower edge at
        # 62 px; 2 px clearance.
        assert sb.cutout_bar_height(2.0, 4.0, 129.0, 1333) == 64
        assert sb.FLX1_HEIGHT == 64
        assert sb.DEVICE_HEIGHTS['furiphone-flx1'] == sb.FLX1_HEIGHT

    def test_hole_sits_inside_the_bar(self, sb):
        px_per_mm = 1333 / 129.0
        for top, diameter in ((2.0, 3.5), (2.0, 4.0)):
            assert (top + diameter) * px_per_mm < sb.FLX1_HEIGHT
        assert sb.MIN_HEIGHT <= sb.FLX1_HEIGHT <= sb.MAX_HEIGHT

    def test_follows_the_live_scale(self, sb):
        # Same physical hole at scale 2 (800 px tall) and at 1.2 (1333).
        assert sb.device_default_height('furiphone-flx1', (800, 129)) == 40
        assert sb.device_default_height('furiphone-flx1', (1333, 129)) == 64
        assert sb.device_default_height('furiphone-flx1', (0, 0)) == 64
        assert sb.device_default_height('furiphone-flx1', None) == 64
        # No hole on record: the generic default, whatever the panel.
        assert sb.device_default_height(None, (800, 129)) \
            == sb.DEFAULT_HEIGHT
        # An explicit config value still wins.
        assert sb.resolve_height(_Cfg(status_bar_height=50),
                                 'furiphone-flx1', (800, 129)) == 50

    def test_monitor_panel_reads_the_long_axis(self, sb):
        monitor = types.SimpleNamespace(
            get_geometry=lambda: types.SimpleNamespace(width=600,
                                                       height=1333),
            get_width_mm=lambda: 63, get_height_mm=lambda: 129)
        monitors = types.SimpleNamespace(get_n_items=lambda: 1,
                                         get_item=lambda i: monitor)
        display = types.SimpleNamespace(get_monitors=lambda: monitors)
        assert sb.monitor_panel(display) == (1333, 129)
        assert sb.monitor_panel(None) is None

    def test_detects_flx1_by_hwcomposer_connector(self, sb, tmp_path):
        (tmp_path / 'card0-HWCOMPOSER-1').mkdir()
        assert sb.detect_device(tmp_path) == 'furiphone-flx1'

    def test_other_panels_are_generic(self, sb, tmp_path):
        (tmp_path / 'card0-DSI-1').mkdir()
        assert sb.detect_device(tmp_path) is None
        assert sb.detect_device(tmp_path / 'missing') is None


# ---- shade-open wiring --------------------------------------------------

class TestShadeOpen:
    @pytest.mark.parametrize('dx,dy,want', [
        (0, 30, True), (4, 16, True), (0, 10, False), (0, -40, False),
        (40, 30, False), (-3, 120, True),
    ])
    def test_pull_threshold(self, sb, dx, dy, want):
        assert sb.is_shade_pull(dx, dy) is want

    def test_pull_opens_shade_once_per_drag(self, sb):
        opened = []
        bar = types.SimpleNamespace(
            _pull_fired=False, _on_open_shade=lambda: opened.append(1))
        sb.StatusBar._on_drag_begin(bar, None, 10, 5)
        for dy in (4, 12, 20, 40, 90):
            sb.StatusBar._on_drag_update(bar, None, 0, dy)
        assert opened == [1]
        sb.StatusBar._on_drag_begin(bar, None, 10, 5)
        sb.StatusBar._on_drag_update(bar, None, 0, 30)
        assert opened == [1, 1]

    def test_sideways_drag_never_opens(self, sb):
        opened = []
        bar = types.SimpleNamespace(
            _pull_fired=False, _on_open_shade=lambda: opened.append(1))
        sb.StatusBar._on_drag_update(bar, None, 80, 20)
        assert opened == []

    def test_main_wires_the_bar_to_show_shade(self, sb, monkeypatch):
        import main
        built = {}

        class _Bar:
            def set_application(self, app):
                built['app'] = app

            def set_status(self, snap):
                built['status'] = snap

        def _create(config=None, on_open_shade=None):
            built['config'] = config
            built['open'] = on_open_shade
            return _Bar()

        monkeypatch.setattr(sb, 'create_status_bar', _create)
        shell = types.SimpleNamespace(
            config=_Cfg(), _show_shade=lambda: None,
            _status_snapshot={'battery': 50},
            _refresh_clock=lambda: built.setdefault('clock', True),
            _sync_status_bar=lambda: built.setdefault('synced', True))
        app = types.SimpleNamespace(_shell=shell)
        main.XXWMApplication._start_status_bar(app)
        assert built['open'] == shell._show_shade
        assert built['config'] is shell.config
        assert shell._status_bar is not None
        assert built['status'] == {'battery': 50}
        assert built['clock'] and built['synced'] and built['app'] is app

    def test_no_layer_shell_means_no_bar(self, sb, monkeypatch):
        import main
        monkeypatch.setattr(sb, 'create_status_bar', lambda **_kw: None)
        shell = types.SimpleNamespace(config=_Cfg(), _show_shade=None,
                                      _status_bar=None)
        main.XXWMApplication._start_status_bar(
            types.SimpleNamespace(_shell=shell))
        assert shell._status_bar is None

    def test_lisgd_top_edge_still_opens_the_shade(self):
        from gesture_bindings import DEFAULT_VERBS
        assert DEFAULT_VERBS['swipe_down_top'] == 'gesture.shade'
        text = (SRC / 'main.py').read_text(encoding='utf-8')
        assert "command == 'gesture.shade'" in text


# ---- hide / show rules -----------------------------------------------------

class TestVisibility:
    @pytest.mark.parametrize('locked,wizard,want', [
        (False, False, True), (True, False, False),
        (False, True, False), (True, True, False),
    ])
    def test_table(self, sb, locked, wizard, want):
        assert sb.decide_visible(locked, wizard) is want

    def _bar(self, visible):
        rec = []
        bar = types.SimpleNamespace(
            get_visible=lambda: visible,
            present=lambda: rec.append('present'),
            set_visible=lambda v: rec.append(('set_visible', v)))
        return bar, rec

    def test_sync_hides_and_shows(self, sb):
        bar, rec = self._bar(True)
        sb.StatusBar.sync_visibility(bar, True, False)
        assert rec == [('set_visible', False)]
        bar, rec = self._bar(False)
        sb.StatusBar.sync_visibility(bar, False, False)
        assert rec == ['present']
        bar, rec = self._bar(True)
        sb.StatusBar.sync_visibility(bar, False, False)
        assert rec == []

    def test_window_sync_reads_lock_and_tour(self):
        import window
        seen = []
        bar = types.SimpleNamespace(
            sync_visibility=lambda locked, wiz: seen.append((locked, wiz)))
        app = types.SimpleNamespace(_tour_visible=True)
        shell = types.SimpleNamespace(
            _status_bar=bar, is_locked=lambda: False,
            get_application=lambda: app)
        window.ShellWindow._sync_status_bar(shell)
        app._tour_visible = False
        shell.is_locked = lambda: True
        window.ShellWindow._sync_status_bar(shell)
        assert seen == [(False, True), (True, False)]

    def test_window_sync_without_bar_is_a_no_op(self):
        import window
        window.ShellWindow._sync_status_bar(
            types.SimpleNamespace(_status_bar=None))

    def test_welcome_tour_hides_then_restores(self, monkeypatch):
        import main
        seen = []
        shell = types.SimpleNamespace(
            _sync_status_bar=lambda: seen.append(app._tour_visible))
        app = types.SimpleNamespace(_shell=shell, _tour_visible=True)
        app._sync_status_bar = lambda: main.XXWMApplication._sync_status_bar(
            app)
        main.XXWMApplication._end_welcome_tour(app)
        assert seen == [False]

    def test_lock_visibility_drives_the_bar(self):
        text = (SRC / 'window.py').read_text(encoding='utf-8')
        start = text.index('    def _show_lock_screen(self)')
        body = text[start:text.index('    def _reassert_call_ui_above_lock')]
        assert "'notify::visible'" in body
        assert '_sync_status_bar' in body

    def test_first_boot_never_builds_the_bar(self):
        """The bar is created in _show_shell, which runs only after the
        first-boot wizard completes."""
        text = (SRC / 'main.py').read_text(encoding='utf-8')
        show_shell = text[text.index('    def _show_shell(self)'):
                          text.index('    def _start_status_bar(self)')]
        assert 'self._start_status_bar()' in show_shell
        activate = text[text.index('    def do_activate(self)'):
                        text.index('    def _show_shell(self)')]
        assert '_start_status_bar' not in activate


# ---- content -------------------------------------------------------------

class TestContent:
    def test_right_cluster_order(self, sb):
        snap = {'ssid': 'Home', 'battery': 80, 'charging': False}
        assert sb.right_glyphs(snap, dnd=True, focus=True, privacy=True) \
            == ['privacy', 'focus', 'dnd', 'wifi', 'battery']
        assert sb.right_glyphs({'signal': '▂▄', 'battery': 9}) \
            == ['cell', 'battery']
        assert sb.right_glyphs({}) == []

    def test_battery_text_and_low(self, sb):
        assert sb.battery_text({'battery': 84}) == '84%'
        assert sb.battery_text({'battery': 84, 'charging': True}) == '84%+'
        assert sb.battery_text({'battery': None}) == ''
        assert sb.battery_low({'battery': 12})
        assert not sb.battery_low({'battery': 12, 'charging': True})
        assert not sb.battery_low({'battery': 60})

    def test_signal_bars(self, sb):
        assert sb.signal_bars('▂▄▆') == 3
        assert sb.signal_bars('·') == 0
        assert sb.signal_bars(None) == 0

    def test_one_poll_feeds_strip_and_bar(self, monkeypatch):
        import threading

        import system_status
        from gi.repository import GLib
        monkeypatch.setattr(system_status, 'status_snapshot', lambda: {
            'ssid': 'HomeNet', 'signal': None, 'battery': 87,
            'charging': False})
        monkeypatch.setattr(GLib, 'idle_add', lambda cb, *a: cb(*a))
        got, done = [], threading.Event()
        system_status.status_line_async(
            got.append, on_snapshot=lambda s: (got.append(s), done.set()))
        assert done.wait(5)
        assert got[0] == 'HomeNet  ·  87%'
        assert got[1]['battery'] == 87

    def test_no_new_poller(self, sb):
        text = (SRC / 'status_bar.py').read_text(encoding='utf-8')
        for needle in ('timeout_add', 'subprocess', 'Thread(', 'call_sync'):
            assert needle not in text


def test_bar_content_hidden_on_home_only():
    # Owner: "hide the topbar on the home screen". The surface (and its
    # reserved zone) stays so apps behind home are never resized.
    import status_bar
    assert status_bar.decide_content(home_showing=True) is False
    assert status_bar.decide_content(home_showing=False) is True


def test_home_showing_rules():
    import types
    import window
    def fake(page, over_apps, activated):
        mgr = types.SimpleNamespace(activated_app_id=lambda: activated)
        return types.SimpleNamespace(
            stack=types.SimpleNamespace(get_visible_child_name=lambda: page),
            _over_apps=over_apps, _toplevel_manager=mgr)
    home = window.ShellWindow._home_showing
    assert home(fake('home', True, 'org.app')) is True     # went home over an app
    assert home(fake('home', False, None)) is True         # nothing open
    assert home(fake('home', False, 'org.app')) is False   # app in front
    assert home(fake('settings', True, None)) is False     # Settings page


def test_home_scale_shrinks_only_home():
    import window
    from config import ShellConfig
    cfg = ShellConfig.__new__(ShellConfig)
    cfg.data = {}
    assert cfg.home_scale == 0.8
    css = window.launcher_text_css(1.0, 0.8)
    assert 'font-size: 43.2pt' in css      # home clock 54pt x 0.8
    assert 'font-size: 14pt' in css        # drawer app rows unchanged


def test_show_status_bar_switch_governs_home_only():
    # Android's "Show Status Bar" is about the launcher's screens; apps
    # always keep their bar. Off by default, so home has no bar text.
    import status_bar
    assert status_bar.decide_content(True, show_on_home=True) is True
    assert status_bar.decide_content(True, show_on_home=False) is False
    assert status_bar.decide_content(False, show_on_home=False) is True
