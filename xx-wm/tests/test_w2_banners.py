"""W2 heads-up banners + HUD position (task w2_banners), headless.

Policy/replace/tap/duration execute the real banner helpers; layer-shell
wiring (anchors, revealer, gestures) and the HUD placement are asserted on
source so they hold without a display. gi is faked at import like
test_w1_notifications so collection order cannot flip the branch under test.
"""
import sys
import types
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).parent.parent


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
    glib.VariantType = lambda *a, **k: None
    glib.markup_escape_text = lambda text: (
        str(text).replace('&', '&amp;').replace('<', '&lt;')
        .replace('>', '&gt;'))

    gio = types.ModuleType('gi.repository.Gio')
    gio.DBusCallFlags = SimpleNamespace(NONE=0)

    gdk = types.ModuleType('gi.repository.Gdk')
    gtk = types.ModuleType('gi.repository.Gtk')
    for name in ('Window', 'Box', 'Label', 'Revealer', 'CssProvider',
                 'GestureClick', 'GestureSwipe', 'GestureDrag',
                 'StyleContext', 'Align', 'RevealerTransitionType',
                 'Orientation'):
        setattr(gtk, name, type(name, (), {}))

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

sys.path.insert(0, str(REPO / 'launcher' / 'src'))

import banner  # noqa: E402
import hud  # noqa: E402

for _name in [n for n in sys.modules if n == 'gi' or n.startswith('gi.')]:
    del sys.modules[_name]
sys.modules.update(_saved_gi)
del _saved_gi


class _FakeDaemon:
    def __init__(self) -> None:
        self.invoked: list = []
        self.closed: list = []

    def invoke_action(self, notif_id, key):
        self.invoked.append((notif_id, key))

    def close_from_shell(self, notif_id):
        self.closed.append(notif_id)


def _banner_src() -> str:
    return (REPO / 'launcher' / 'src' / 'banner.py').read_text(
        encoding='utf-8')


def _hud_src() -> str:
    return (REPO / 'launcher' / 'src' / 'hud.py').read_text(encoding='utf-8')


class TestBannerPolicy:
    def test_plain_notification_banners(self):
        assert banner.should_show_banner(
            False, False, 1, False, {}, 42) is True

    def test_dnd_silences_but_alarm_punches_through(self):
        assert banner.should_show_banner(
            True, False, 1, False, {}, 42) is False
        assert banner.should_show_banner(
            True, True, 1, False, {}, 42) is True

    def test_dnd_urgency2_punches_through(self):
        assert banner.should_show_banner(
            True, False, 2, False, {}, 42) is True

    def test_low_urgency_never_banners(self):
        assert banner.should_show_banner(
            False, False, 0, False, {}, 42) is False
        assert banner.should_show_banner(
            True, True, 0, False, {}, 42) is False

    def test_locked_screen_suppresses(self):
        assert banner.should_show_banner(
            False, False, 1, True, {}, 42) is False

    def test_open_shade_suppresses(self):
        assert banner.should_show_banner(
            False, False, 1, False, {}, 42, True) is False
        assert banner.should_show_banner(
            False, False, 1, False, {}, 42,
            shade_visible=True) is False

    def test_open_shade_suppresses_even_alarms(self):
        assert banner.should_show_banner(
            True, True, 1, False, {}, 42, True) is False
        assert banner.should_show_banner(
            True, False, 2, False, {}, 42, True) is False

    def test_closed_shade_banners_normally(self):
        assert banner.should_show_banner(
            False, False, 1, False, {}, 42,
            shade_visible=False) is True

    def test_no_banner_hint_suppresses(self):
        assert banner.should_show_banner(
            False, False, 1, False,
            {'x-xx-wm-no-banner': True}, 42) is False
        assert banner.should_show_banner(
            False, False, 1, False, {}, 42) is True

    def test_internal_update_notice_never_banners(self):
        assert banner.should_show_banner(
            False, False, 1, False, {}, 999901) is False


class TestBannerDuration:
    def test_short_body_holds_four_seconds(self):
        assert banner.banner_duration_ms('hello') == 4000
        assert banner.banner_duration_ms('') == 4000

    def test_long_body_holds_five_seconds(self):
        assert banner.banner_duration_ms('x' * 121) == 5000

    def test_boundary_stays_short(self):
        assert banner.banner_duration_ms('x' * 120) == 4000


class TestReplaceLatest:
    def test_newer_notification_replaces_visible(self):
        state = banner.BannerState()
        state.show(1, 'A', 'first', 'body one', '', [], {})
        state.show(2, 'B', 'second', 'body two', '', [], {})
        assert state.current_id == 2
        assert state.current['summary'] == 'second'

    def test_same_id_updates_in_place(self):
        state = banner.BannerState()
        state.show(7, 'A', 'old', 'old body', '', [], {})
        state.show(7, 'A', 'new', 'new body', '', [], {})
        assert state.current_id == 7
        assert state.current['summary'] == 'new'
        assert state.current['body'] == 'new body'


class TestTapDispatch:
    def test_default_action_invokes_then_closes(self):
        state = banner.BannerState()
        state.show(11, 'A', 's', 'b', 'some-app',
                   [('default', 'Open')], {})
        daemon = _FakeDaemon()
        launched: list = []
        assert banner.dispatch_tap(
            state, daemon, launched.append) == 'default'
        assert daemon.invoked == [(11, 'default')]
        assert daemon.closed == [11]
        assert launched == []

    def test_resident_stays_open_after_invoke(self):
        state = banner.BannerState()
        state.show(12, 'A', 's', 'b', 'some-app',
                   [('default', 'Open')], {'resident': True})
        daemon = _FakeDaemon()
        assert banner.dispatch_tap(state, daemon, lambda e: None) == 'default'
        assert daemon.invoked == [(12, 'default')]
        assert daemon.closed == []

    def test_no_default_launches_desktop_entry(self):
        state = banner.BannerState()
        state.show(13, 'A', 's', 'b', 'org.example.App',
                   [('reply', 'Reply')], {})
        daemon = _FakeDaemon()
        launched: list = []
        assert banner.dispatch_tap(
            state, daemon, launched.append) == 'launch'
        assert launched == ['org.example.App']
        assert daemon.invoked == []
        assert daemon.closed == [13]

    def test_nothing_tappable_dispatches_none(self):
        state = banner.BannerState()
        state.show(14, 'A', 's', 'b', '', [], {})
        daemon = _FakeDaemon()
        launched: list = []
        assert banner.dispatch_tap(
            state, daemon, launched.append) == 'none'
        assert daemon.invoked == [] and daemon.closed == []
        assert launched == []


class TestBannerWiring:
    def test_reuses_shade_markup_helper(self):
        src = _banner_src()
        assert 'notif_body_to_pango_markup' in src
        assert 'from notification_shade import' in src
        assert 'def notif_body_to_pango_markup' not in src

    def test_layer_top_with_margins_and_no_keyboard(self):
        src = _banner_src()
        assert 'Layer.TOP' in src
        assert 'Edge.TOP' in src and 'Edge.LEFT' in src
        assert 'Edge.RIGHT' in src
        assert 'set_exclusive_zone(self, 0)' in src
        assert 'KeyboardMode.NONE' in src
        assert src.count('set_margin(self, edge, 8)') >= 1 or \
            'set_margin' in src and ', 8)' in src

    def test_reveal_is_fast_slide(self):
        src = _banner_src()
        assert 'SLIDE_DOWN' in src
        assert '_BANNER_REVEAL_MS = 120' in src

    def test_swipe_and_drag_dismiss_only(self):
        src = _banner_src()
        assert '_SWIPE_UP_VY = -200' in src
        assert 'vy < _SWIPE_UP_VY' in src
        assert 'GestureSwipe' in src
        assert 'GestureDrag' in src
        assert 'close_from_shell' in src

    def test_window_api_surface(self):
        for name in ('show_notification', 'hide_banner', 'apply_theme'):
            assert callable(getattr(banner.BannerWindow, name, None)), name

    def test_theme_sheet_uses_preset(self):
        from config import THEME_PRESETS
        for key, preset in THEME_PRESETS.items():
            sheet = banner.theme_css(preset)
            for field in ('surface', 'foreground', 'muted'):
                assert getattr(preset, field) in sheet, f'{key}: {field}'

    def test_main_notifies_banner_lazily(self):
        main_src = (REPO / 'launcher' / 'src' / 'main.py').read_text(
            encoding='utf-8')
        assert '_maybe_banner' in main_src
        assert 'should_show_banner' in main_src
        assert '_ensure_banner' in main_src
        assert 'is_locked' in main_src
        assert '_lock_screen' in main_src
        assert 'shade_visible' in main_src
        assert '_shade_visible' in main_src
        assert 'from banner import' in main_src
        assert 'x-xx-wm-no-banner' in _banner_src()

    def test_retheme_fans_out_to_banner(self):
        window_src = (REPO / 'launcher' / 'src' / 'window.py').read_text(
            encoding='utf-8')
        assert "'_banner'" in window_src or '"_banner"' in window_src
        assert 'banner' in window_src.split('def _retheme_surfaces')[1].split(
            'def _setup_idle_timer')[0]

    def test_meson_lists_banner(self):
        meson = (REPO / 'launcher' / 'meson.build').read_text(
            encoding='utf-8')
        assert "'src/banner.py'" in meson


class TestHudPosition:
    def test_top_margin_is_twenty_percent(self):
        # Middle of the top third: center at height / 6.
        assert hud.hud_top_margin(720) == 120
        assert hud.hud_top_margin(1000, 60) == 136
        assert hud.hud_top_margin(100, 60) == 0

    def test_hud_centers_box_at_top_fraction(self):
        src = _hud_src()
        assert 'Edge.BOTTOM, False' in src
        assert 'Edge.LEFT, True' in src
        assert 'Edge.RIGHT, True' in src
        assert 'Gtk.Align.CENTER' in src
        assert 'Gtk.Align.START' in src
        assert 'hud_top_margin' in src
        assert 'set_input_region' in src
