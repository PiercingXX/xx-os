"""Config parsing, fallbacks, presets, rendering, custom derivation."""

import re

import pytest

from xx_ui import css, theme
from xx_ui.theme import (
    DANGER_RED,
    FONT_FAMILIES,
    THEME_PRESETS,
    _derive_shades,
    load,
    preset_keys,
    resolve_scale,
)

from conftest import write_config


def test_missing_config_falls_back(isolated_home):
    active = load()
    assert active.key == 'amoled'
    assert active.background == '#000000'
    assert active.font_family == FONT_FAMILIES['jetbrains-mono-nerd']
    assert active.scale == 1.0
    assert active.dark is True


def test_invalid_json_falls_back(isolated_home):
    path = isolated_home / '.config' / 'xx-wm' / 'config.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{nope', encoding='utf-8')
    assert load().key == 'amoled'


def test_non_dict_json_falls_back(isolated_home):
    write_config(isolated_home, [1, 2, 3])
    assert load().key == 'amoled'


def test_unknown_theme_key_falls_back(isolated_home):
    write_config(isolated_home, {'theme': 'neon'})
    assert load().key == 'amoled'


def test_non_string_theme_falls_back(isolated_home):
    write_config(isolated_home, {'theme': 42})
    assert load().key == 'amoled'


def test_xdg_config_home_respected(tmp_path, monkeypatch):
    other = tmp_path / 'xdg'
    (other / 'xx-wm').mkdir(parents=True)
    import json
    (other / 'xx-wm' / 'config.json').write_text(
        json.dumps({'theme': 'forest'}), encoding='utf-8')
    monkeypatch.setenv('XDG_CONFIG_HOME', str(other))
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    assert load().key == 'forest'


def test_every_preset_loads(isolated_home):
    for key, preset in THEME_PRESETS.items():
        write_config(isolated_home, {'theme': key})
        active = load()
        assert active.key == key
        assert active.background == preset.background
        assert active.surface == preset.surface
        assert active.surface_alt == preset.surface_alt
        assert active.border == preset.border
        assert active.foreground == preset.foreground
        assert active.muted == preset.muted
        assert active.accent == preset.accent


def test_preset_keys():
    assert preset_keys() == ['amoled', 'graphite', 'forest', 'ocean',
                             'paper', 'mist', 'aura', 'burgundy']


def test_light_dark_flags(isolated_home):
    for key in ('amoled', 'graphite', 'forest', 'ocean', 'aura', 'burgundy'):
        write_config(isolated_home, {'theme': key})
        assert load().dark is True, key
    for key in ('paper', 'mist'):
        write_config(isolated_home, {'theme': key})
        assert load().dark is False, key


def test_every_preset_renders_without_placeholders(isolated_home):
    for key in preset_keys():
        write_config(isolated_home, {'theme': key})
        out = css.render(load())
        assert '${' not in out, key
        assert key and THEME_PRESETS[key].background in out
        assert THEME_PRESETS[key].foreground in out


def test_render_defines_libadwaita_colors(isolated_home):
    out = css.render(load())
    for name in ('window_bg_color', 'view_bg_color', 'accent_bg_color',
                 'accent_color', 'card_bg_color', 'headerbar_bg_color',
                 'popover_bg_color', 'dialog_bg_color', '--window-bg-color'):
        assert name in out


def test_render_has_no_libadwaita_blue(isolated_home):
    for key in preset_keys():
        write_config(isolated_home, {'theme': key})
        out = css.render(load()).lower()
        assert '#3584e4' not in out, key
        assert '#1c71d8' not in out, key
        assert '#62a0ea' not in out, key


def test_render_uses_danger_red(isolated_home):
    assert DANGER_RED in css.render(load())


def test_font_keys(isolated_home):
    for key, family in FONT_FAMILIES.items():
        write_config(isolated_home, {'font': key})
        assert load().font_family == family, key


def test_invalid_font_falls_back(isolated_home):
    write_config(isolated_home, {'font': 'comic-sans'})
    assert load().font_family == FONT_FAMILIES['jetbrains-mono-nerd']


def test_custom_font_family(isolated_home):
    write_config(isolated_home, {'font': 'custom',
                                 'custom_font_family': 'My Font'})
    assert load().font_family == 'My Font'


def test_custom_font_without_family_falls_back(isolated_home):
    write_config(isolated_home, {'font': 'custom'})
    assert load().font_family == FONT_FAMILIES['jetbrains-mono-nerd']


def test_font_family_sanitized_for_css():
    assert "'" not in css.sanitize_font_family("a'b;c{d}")
    assert css.sanitize_font_family('') != ''


def test_scale_clamped(isolated_home):
    assert resolve_scale({'text_size_scale': 3.0}) == 2.0
    assert resolve_scale({'text_size_scale': 0.1}) == 0.5
    assert resolve_scale({'text_size_scale': 'junk'}) == 1.0
    assert resolve_scale({}) == 1.0
    write_config(isolated_home, {'text_size_scale': 2.0})
    assert load().scale == 2.0


def test_scale_changes_rendered_sizes(isolated_home):
    write_config(isolated_home, {'text_size_scale': 1.0})
    base = css.render(load())
    write_config(isolated_home, {'text_size_scale': 2.0})
    big = css.render(load())
    assert 'font-size: 30px' in base
    assert 'font-size: 60px' in big


def test_custom_dark_derivation(isolated_home):
    write_config(isolated_home, {'theme': 'custom',
                                 'custom_background': '#3a2a10'})
    active = load()
    assert active.key == 'custom'
    assert active.background == '#3a2a10'
    assert (active.surface, active.surface_alt, active.border) == \
        _derive_shades('#3a2a10')
    assert active.dark is True
    assert active.foreground == THEME_PRESETS['amoled'].foreground
    assert active.muted == THEME_PRESETS['amoled'].muted


def test_custom_light_derivation(isolated_home):
    write_config(isolated_home, {'theme': 'custom',
                                 'custom_background': '#F3EEE2'})
    active = load()
    assert active.dark is False
    assert active.foreground == THEME_PRESETS['paper'].foreground


def test_custom_contrast(isolated_home):
    def luminance(hex_color: str) -> float:
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255
    for bg in ('#3a2a10', '#000000', '#10261B', '#F3EEE2', '#E6EDF5',
               '#7a1010', '#104010'):
        write_config(isolated_home, {'theme': 'custom',
                                     'custom_background': bg})
        active = load()
        gap = abs(luminance(active.foreground) - luminance(active.background))
        assert gap > 0.5, (bg, active.foreground)


def test_custom_invalid_color_falls_back(isolated_home):
    for bad in ('', 'red', '#fff', '#gggggg', None, '#3a2a10\n#000000'):
        write_config(isolated_home, {'theme': 'custom',
                                     'custom_background': bad})
        assert load().key == 'amoled', bad


def test_custom_missing_color_falls_back(isolated_home):
    write_config(isolated_home, {'theme': 'custom'})
    assert load().key == 'amoled'


def test_render_custom_has_no_placeholders(isolated_home):
    write_config(isolated_home, {'theme': 'custom',
                                 'custom_background': '#3a2a10'})
    out = css.render(load())
    assert '${' not in out
    assert '#3a2a10' in out
    assert re.search(r'#[0-9a-f]{6}', out)


def test_shell_parity_derive_shades():
    # Same rule as xx-wm config._derive_shades: near-black keeps amoled trio.
    assert _derive_shades('#000000') == ('#111111', '#181818', '#2f2f2f')
    assert _derive_shades('junk') == ('#111111', '#181818', '#2f2f2f')
    assert _derive_shades('#3a2a10') == ('#2f220d', '#291e0b', '#1f1708')


def test_on_change_roundtrip():
    seen = []
    off = css.on_change(seen.append)
    assert callable(off)
    off()
    assert seen == []


def test_config_event_triggers_debounced_reload(isolated_home):
    from gi.repository import Gio, GLib
    write_config(isolated_home, {'theme': 'ocean'})
    seen = []
    off = css.on_change(seen.append)
    try:
        child = Gio.File.new_for_path(str(theme.config_path()))
        assert css._debounce_id == 0
        css._on_config_event(
            None, child, None, Gio.FileMonitorEvent.CHANGED)
        loop = GLib.MainLoop()
        GLib.timeout_add(600, loop.quit)
        loop.run()
    finally:
        off()
    assert seen and seen[-1].key == 'ocean'


def test_config_event_ignores_other_files(isolated_home):
    from gi.repository import Gio
    other = isolated_home / '.config' / 'xx-wm' / 'other.json'
    child = Gio.File.new_for_path(str(other))
    css._on_config_event(None, child, None, Gio.FileMonitorEvent.CHANGED)
    css._on_config_event(
        None, Gio.File.new_for_path(str(theme.config_path())), None,
        Gio.FileMonitorEvent.ATTRIBUTE_CHANGED)
    assert css._debounce_id == 0


def test_danger_text_beats_button_text_color(isolated_home):
    out = css.render(load())
    match = re.search(r'button\.xx-danger-text[^{]*\{([^}]*)\}', out)
    assert match, 'no button-qualified danger rule'
    assert DANGER_RED in match.group(1)


@pytest.mark.parametrize('key', list(THEME_PRESETS) + ['custom-missing'])
def test_never_raises(isolated_home, key, tmp_path):
    if key == 'custom-missing':
        write_config(isolated_home, {'theme': 'custom'})
    else:
        write_config(isolated_home, {'theme': key})
    active = load(path=tmp_path / 'does-not-exist.json')
    assert active.key == 'amoled'


def test_install_applies_explicit_theme_object(isolated_home):
    from xx_ui.theme import Theme

    preset = THEME_PRESETS['forest']
    manual = Theme(
        key=preset.key, background=preset.background,
        surface=preset.surface, surface_alt=preset.surface_alt,
        border=preset.border, foreground=preset.foreground,
        muted=preset.muted, accent=preset.accent,
        font_family='Space Mono, Monospace', scale=1.0, dark=True,
    )
    assert css.install(manual) is manual
    out = css.render(manual)
    assert preset.background in out
    assert '${' not in out


def test_count_badge_css_exists(isolated_home):
    assert '.xx-count' in css.render(load())


def test_popover_is_a_single_surface(isolated_home):
    # Adwaita paints its own card on the popover's inner contents node;
    # the outer popover rule is the one surface, so the inner stays flat.
    out = css.render(load())
    match = re.search(r'popover\s*>\s*contents\s*\{([^}]*)\}', out)
    assert match, 'no popover-contents flattening rule'
    body = match.group(1)
    assert 'transparent' in body
    assert 'border: none' in body
    assert 'box-shadow: none' in body


def test_page_header_title_row_keeps_action_height(isolated_home):
    # Action buttons measure 54 px tall: the title row keeps that
    # height with or without them so every PageHeader aligns.
    out = css.render(load())
    match = re.search(r'\.xx-page-top\s*\{([^}]*)\}', out)
    assert match, 'no page-header title-row rule'
    assert 'min-height: 54px' in match.group(1)


def test_tile_inversion_beats_toggle_fill(isolated_home):
    # libadwaita repaints toggle :checked fills over a plain class rule,
    # so the inverted tile re-asserts the fill on a toggle-qualified
    # checked selector (same reason as the danger-text rule above).
    out = css.render(load())
    match = re.search(
        r'button\.xx-tile\.toggle\.active:checked[^{]*\{([^}]*)\}', out)
    assert match, 'no toggle-qualified inverted tile rule'
    assert 'background-color' in match.group(1)
    assert 'background-image: none' in match.group(1)


def test_tile_state_flips_paint_instantly(isolated_home):
    # A fading button background can freeze mid-fade and pin the old
    # fill, so tiles carry no transition or animation — on the base rule
    # and on the toggle-qualified checked rule (the fade lives there).
    out = css.render(load())
    base = re.search(r'\.xx-tile\s*\{([^}]*)\}', out)
    assert base, 'no base tile rule'
    assert 'transition: none' in base.group(1)
    assert 'animation: none' in base.group(1)
    checked = re.search(
        r'button\.xx-tile\.toggle\.active:checked[^{]*\{([^}]*)\}', out)
    assert checked, 'no toggle-qualified checked tile rule'
    assert 'transition: none' in checked.group(1)
    assert 'animation: none' in checked.group(1)


def test_display_face_prefers_space_mono_for_house_monos():
    from xx_ui import css
    assert css.display_family('JetBrainsMono Nerd Font, Monospace') == (
        'Space Mono, SpaceMono Nerd Font, JetBrainsMono Nerd Font, Monospace')
    assert css.display_family('Space Mono, Monospace').startswith('Space Mono')
    # Any other family the user picked keeps the display role too.
    assert css.display_family('Sans Light') == 'Sans Light'


def test_glyph_tile_rules_use_the_foreground_ramp():
    import re
    from xx_ui import css, theme
    out = css.render(theme.Theme(
        key='t', background='#101010', surface='#202020',
        surface_alt='#303030', border='#404040', foreground='#FAFAFA',
        muted='#AAAAAA', accent='#00FF00', font_family='Sans',
        scale=1.0, dark=True))
    base = re.search(r'button\.xx-gtile\.toggle:checked\s*\{([^}]*)\}', out)
    assert base and 'alpha(#FAFAFA, 0.035)' in base.group(1)
    assert 'alpha(#FAFAFA, 0.10)' in base.group(1)
    assert 'dashed alpha(#FAFAFA, 0.40)' in out
    assert '.xx-gtile.attention .xx-gtile-dot' in out
    assert '#00FF00' not in out.split('.xx-gtile')[1]
    assert '.xx-display-face' in out


def test_danger_reads_on_every_preset(isolated_home):
    # #ff6b6b is ~2.4:1 on paper: light themes take a darker red that
    # reads at WCAG AA on their background (and on the surface cards).
    for key, preset in THEME_PRESETS.items():
        write_config(isolated_home, {'theme': key})
        active = load()
        assert theme.contrast(active.danger, preset.background) >= 4.5, key
        if active.dark:
            assert active.danger == DANGER_RED, key
        else:
            assert active.danger == theme.DANGER_RED_LIGHT, key
            assert theme.contrast(active.danger, preset.surface) >= 4.5, key
        out = css.render(active)
        assert f'@define-color error_color {active.danger};' in out, key


def test_custom_light_danger_darkens_until_it_reads(isolated_home):
    write_config(isolated_home, {'theme': 'custom',
                                 'custom_background': '#8080ff'})
    active = load()
    assert not active.dark
    assert theme.contrast(active.danger, active.background) >= 4.5


def test_manual_theme_derives_danger():
    preset = THEME_PRESETS['paper']
    manual = theme.Theme(
        key=preset.key, background=preset.background,
        surface=preset.surface, surface_alt=preset.surface_alt,
        border=preset.border, foreground=preset.foreground,
        muted=preset.muted, accent=preset.accent,
        font_family='Monospace', scale=1.0, dark=False)
    assert manual.danger == theme.DANGER_RED_LIGHT


def _rule(out: str, selector: str) -> str:
    match = re.search(r'(?:^|\n)' + re.escape(selector) + r'[ ,\n][^{]*\{(.*?)\n\}',
                      out, re.S)
    assert match, f'no rule for {selector}'
    return match.group(1)


def test_shared_control_rules(isolated_home):
    write_config(isolated_home, {'theme': 'paper', 'text_size_scale': 1.5})
    active = load()
    out = css.render(active)
    icon = _rule(out, '.xx-btn-icon')
    assert 'min-width: 48px' in icon and 'min-height: 48px' in icon
    assert 'opacity: 0.4' in _rule(out, '.xx-btn:disabled')
    entry = _rule(out, '.xx-entry')
    assert f'border: 1px solid {active.border}' in entry
    error = _rule(out, 'entry.xx-entry.error')
    assert f'border-color: {active.danger}' in error
    assert f'color: {active.danger}' in error
    assert 'font-size: 24px' in _rule(out, 'spinbutton')
    switch = _rule(out, 'switch')
    assert 'background-clip: padding-box' in switch
    assert 'solid transparent' in switch
