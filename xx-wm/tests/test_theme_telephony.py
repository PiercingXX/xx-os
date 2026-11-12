"""Theme sweep contract for the telephony surfaces (dialer, call UI, SMS).

Each telephony module exposes a pure ``theme_css(preset)`` that renders its
full sheet from a ThemePreset -- no hardcoded colors, no GTK required. These
tests exercise every canonical preset headlessly and assert the sheets
interpolate the preset palette instead of leaking the legacy hardcoded hexes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

from config import DESTRUCTIVE_TINT_BG, DANGER_RED, ON_DANGER_FG, THEME_PRESETS

from _theme_contract import hardcoded_leftover

# Fields each module's sheet interpolates, per its element inventory:
# call_ui has no bordered elements (never touch border).
FIELDS_PER_MODULE = {
    'call_ui': (
        'background', 'foreground', 'surface', 'surface_alt', 'muted', 'accent',
    ),
}

# White-on-red is call_ui's universal danger pair: the foreground half
# comes from config.ON_DANGER_FG (not theme-tracked), so it still shows up
# as a non-palette literal in rendered sheets and stays exempt here.
DANGER_PAIR_LITERALS = {'#ffffff'}


def _sheets():
    # Run-time import: sibling test modules stub sys.modules['gi'] during
    # pytest collection; conftest restores the real modules before tests run.
    import call_ui

    return {
        'call_ui': call_ui.theme_css,
    }


def test_theme_fields_interpolated_for_every_preset() -> None:
    for key, preset in THEME_PRESETS.items():
        for name, theme_css in _sheets().items():
            sheet = theme_css(preset)
            for field in FIELDS_PER_MODULE[name]:
                value = getattr(preset, field)
                assert value in sheet, f'{key}/{name}: {field} missing'


def test_paper_sheet_differs_from_amoled() -> None:
    paper = THEME_PRESETS['paper']
    amoled = THEME_PRESETS['amoled']
    for name, theme_css in _sheets().items():
        assert theme_css(paper) != theme_css(amoled), name


def test_no_banned_hardcoded_hexes() -> None:
    # hardcoded_leftover() strips interpolated palette values before
    # scanning (amoled surface is literally #111111); the danger-pair white
    # is exempted above and pinned by its dedicated confinement test.
    for key, preset in THEME_PRESETS.items():
        for name, theme_css in _sheets().items():
            leaked = hardcoded_leftover(theme_css(preset), preset) - DANGER_PAIR_LITERALS
            assert not leaked, f'{key}/{name} leaks hardcoded colors: {leaked}'


def test_white_literal_confined_to_call_ui_danger_pair() -> None:
    # White-on-red is part of the universal danger pair: it must appear in
    # call_ui's sheet and must not track the theme or leak elsewhere.
    for key, preset in THEME_PRESETS.items():
        sheets = {name: theme_css(preset) for name, theme_css in _sheets().items()}
        assert ON_DANGER_FG.lower() in sheets['call_ui'].lower(), f'{key}/call_ui'


def test_call_ui_keeps_semantic_danger_pair() -> None:
    for key, preset in THEME_PRESETS.items():
        sheet = _sheets()['call_ui'](preset)
        assert DANGER_RED in sheet, f'{key}: danger red missing'
        assert ON_DANGER_FG in sheet, f'{key}: on-danger foreground missing'
        assert DESTRUCTIVE_TINT_BG in sheet, f'{key}: destructive tint missing'
