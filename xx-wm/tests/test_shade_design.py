"""Shade redesign contract: glyph assets, tile looks (on / working /
needs-you), the truthful date line and the display face. Pure or
fake-widget only; the looks themselves are verified by renders."""
import re
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import tailscale  # noqa: E402

# Private fake-gi copies (real Gtk would need a display).
from test_captions_tile import quick_actions  # noqa: E402
from test_shade_notif_ownership import notification_shade  # noqa: E402

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
ICONS = SRC / 'icons'
MESON = Path(__file__).parent.parent / 'launcher' / 'meson.build'


# --- glyph assets ---------------------------------------------------------------

def test_every_tile_has_a_glyph():
    for tile in quick_actions._TILES:
        name = quick_actions.tile_icon_name(tile.key)
        assert (ICONS / f'{name}.svg').is_file(), tile.key


def test_chrome_glyphs_exist():
    for glyph in ('settings', 'power', 'expand', 'collapse', 'brightness',
                  'volume'):
        assert (ICONS / f'xx-shade-{glyph}-symbolic.svg').is_file(), glyph


def test_glyphs_are_fill_only_symbolic_svgs():
    # Older GTK recolors only fills; a stroke would stay the source color.
    for svg in ICONS.glob('*.svg'):
        text = svg.read_text(encoding='utf-8')
        assert svg.name.endswith('-symbolic.svg'), svg.name
        assert 'viewBox="0 0 24 24"' in text, svg.name
        assert 'stroke' not in text, svg.name


def test_meson_installs_exactly_the_icon_dir():
    listed = set(re.findall(r"'src/icons/([^']+\.svg)'",
                            MESON.read_text(encoding='utf-8')))
    assert listed == {p.name for p in ICONS.glob('*.svg')}
    assert "join_paths(shell_datadir, 'icons')" in MESON.read_text(
        encoding='utf-8')


def test_icon_dir_sits_next_to_the_module():
    assert quick_actions._ICON_DIR == SRC / 'icons'


# --- tile looks ------------------------------------------------------------------

class _Btn:
    def __init__(self):
        self.classes = set()
        self.active = False

    def add_css_class(self, css):
        self.classes.add(css)

    def remove_css_class(self, css):
        self.classes.discard(css)

    def set_active(self, value):
        self.active = value


class _Label:
    text = ''

    def set_text(self, text):
        self.text = text


def _panel():
    panel = object.__new__(quick_actions.QuickActionsPanel)
    panel._tile_buttons = {'tailscale': _Btn(), 'wifi': _Btn()}
    panel._state_labels = {'tailscale': _Label(), 'wifi': _Label()}
    panel._tile_state, panel._tile_details = {}, {}
    panel._updating = False
    panel._on_open_uri = None
    panel._worker_threads = []
    return panel


def test_attention_only_for_a_tailnet_sign_in():
    assert quick_actions.tile_needs_attention(
        'tailscale', False, tailscale.SIGN_IN)
    assert not quick_actions.tile_needs_attention(
        'tailscale', True, tailscale.SIGN_IN)
    assert not quick_actions.tile_needs_attention(
        'tailscale', False, tailscale.NO_PERMISSION)
    assert not quick_actions.tile_needs_attention('wifi', False, 'Sign in')
    assert quick_actions._SIGN_IN_TEXT == tailscale.SIGN_IN


def test_sign_in_state_marks_the_tile_and_clears_when_running():
    panel = _panel()
    panel._apply_tile_state('tailscale', False, tailscale.SIGN_IN)
    assert 'attention' in panel._tile_buttons['tailscale'].classes
    assert panel._state_labels['tailscale'].text == 'Sign in'
    panel._apply_tile_state('tailscale', True, 'flx1')
    assert 'attention' not in panel._tile_buttons['tailscale'].classes
    assert 'active' in panel._tile_buttons['tailscale'].classes


def test_tailnet_toggle_is_working_until_the_worker_answers(monkeypatch):
    panel = _panel()
    tile = SimpleNamespace(key='tailscale', set_state=lambda v: None)
    btn = panel._tile_buttons['tailscale']
    btn.get_active = lambda: True
    panel._on_tile_toggled(btn, tile)
    assert 'working' in btn.classes
    assert panel._state_labels['tailscale'].text == '…'
    panel._apply_tailscale(tailscale.Status(tailscale.RUNNING, 'flx1'))
    assert 'working' not in btn.classes
    assert panel._state_labels['tailscale'].text == 'flx1'


def test_tiles_use_the_shared_glyph_builder():
    src = (SRC / 'quick_actions.py').read_text(encoding='utf-8')
    assert 'widgets.glyph_tile(' in src
    assert 'widgets.glyph_tile_state(' in src


# --- header ----------------------------------------------------------------------

def test_date_line_appends_battery_only_when_known():
    when = datetime(2026, 10, 17, 10, 58)
    assert notification_shade.format_shade_date(when) == 'SAT 17 OCT'
    assert notification_shade.format_shade_date(when, 44) == \
        'SAT 17 OCT · 44%'
    assert notification_shade.format_shade_date(
        datetime(2026, 10, 3), None) == 'SAT 3 OCT'


def test_clock_uses_display_face_class_not_a_baked_family():
    src = (SRC / 'notification_shade.py').read_text(encoding='utf-8')
    assert "add_css_class('xx-display-face')" in src


def test_display_face_is_space_mono_over_the_house_monos():
    from xx_ui import css
    family = css.display_family(
        'JetBrainsMono Nerd Font, JetBrains Mono, Monospace')
    assert family.startswith('Space Mono')
    assert family.index('Space Mono') < family.index('JetBrains')


def test_display_face_follows_a_user_picked_family():
    from xx_ui import css
    assert css.display_family('Sans Light') == 'Sans Light'


def test_sheet_carries_the_design_ramp():
    preset = SimpleNamespace(
        key='t', name='t', background='#000000', surface='#111111',
        surface_alt='#181818', border='#2f2f2f', foreground='#f4f4f4',
        muted='#9a9a9a', accent='#d8d8d8')
    css = notification_shade.theme_css(preset)
    for stop in ('0.035', '0.10', '0.25', '0.40', '0.80', '0.90'):
        assert f'alpha(#f4f4f4, {stop})' in css, stop
    assert '.shade-handle' in css
    assert 'scrolledwindow.shade-notif-card' in css


def test_show_path_reads_sliders_off_the_main_loop():
    # brightnessctl/pactl reads used to run synchronously every time the
    # shade opened
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / 'launcher' / 'src'
           / 'notification_shade.py').read_text()
    show = src[src.index('def show_shade'):]
    show = show[:show.index('\n    def ', 10)]
    assert 'sync_sliders_async' in show
    qa = (Path(__file__).resolve().parent.parent / 'launcher' / 'src'
          / 'quick_actions.py').read_text()
    assert 'def sync_sliders_async(self)' in qa
