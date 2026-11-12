"""Theme sweep contract for the overlay surfaces (switcher, back arrow, HUD, home).

Each overlay module exposes a pure ``theme_css(preset)`` that renders its full
sheet from a ThemePreset -- no hardcoded colors, no GTK required. These tests
exercise every canonical preset headlessly and assert the sheets interpolate
the preset palette instead of leaking the legacy hardcoded hexes.
"""
import functools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from config import DESTRUCTIVE_TINT_BG, DANGER_RED, THEME_PRESETS

from _theme_contract import assert_sheets_differ_by_preset, hardcoded_leftover

# Fields each module's sheet interpolates, per its element inventory:
# hud has no hint text (never touch muted); home_launcher's
# base button color was deleted to fall through to window.py's themed
# .shell-root button color, so its own sheet only carries muted.
FIELDS_PER_MODULE = {
    'app_switcher': ('foreground', 'muted', 'surface'),
    'hud': ('foreground', 'surface'),
    'home_launcher': ('muted',),
}


@functools.lru_cache(maxsize=1)
def _sheets():
    # Run-time import: sibling tests stub sys.modules['gi'] during pytest
    # collection; conftest restores the real modules before tests run.
    import app_switcher
    import home_launcher
    import hud

    return {
        'app_switcher': app_switcher.theme_css,
        'hud': hud.theme_css,
        'home_launcher': home_launcher.theme_css,
    }


def test_theme_fields_interpolated_for_every_preset() -> None:
    for key, preset in THEME_PRESETS.items():
        for name, theme_css in _sheets().items():
            for field in FIELDS_PER_MODULE[name]:
                value = getattr(preset, field)
                assert value in theme_css(preset), f'{key}/{name}: {field} missing'


def test_paper_sheet_differs_from_amoled() -> None:
    assert_sheets_differ_by_preset(
        _sheets(), THEME_PRESETS['paper'], THEME_PRESETS['amoled'],
    )


def test_no_banned_hardcoded_hexes() -> None:
    # hardcoded_leftover() strips interpolated palette values before
    # scanning (amoled background is literally #000000).
    for key, preset in THEME_PRESETS.items():
        for name, theme_css in _sheets().items():
            leaked = hardcoded_leftover(theme_css(preset), preset)
            assert not leaked, f'{key}/{name} leaks hardcoded colors: {leaked}'


def test_switcher_keeps_semantic_destructive_pair() -> None:
    for key, preset in THEME_PRESETS.items():
        sheet = _sheets()['app_switcher'](preset)
        assert DANGER_RED in sheet, f'{key}: danger red missing'
        assert DESTRUCTIVE_TINT_BG in sheet, f'{key}: destructive tint missing'


def test_window_does_not_reference_home_css_constant() -> None:
    window_text = (
        Path(__file__).parent.parent / 'launcher' / 'src' / 'window.py'
    ).read_text(encoding='utf-8')
    assert '_HOME_CSS' not in window_text
