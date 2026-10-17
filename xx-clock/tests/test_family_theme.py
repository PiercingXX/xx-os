"""Family theme bridge (pure half of theme/ThemePresetTest.kt).

Receiver/applier behavior is OUT — Linux theming is the shared xx_ui
system. What stays: the broadcast display-name mapping and the family
contrast rule (luminance weights, the exclusive-182 threshold, custom
grounds resolving through it).
"""

from xx_clock import family_theme as theme


def test_all_seven_display_names_map_case_insensitively():
    assert theme.preset_key_for_display_name("AMOLED Night") == "amoled"
    assert theme.preset_key_for_display_name("graphite") == "graphite"
    assert theme.preset_key_for_display_name("FOREST NIGHT") == "forest"
    assert theme.preset_key_for_display_name("Ocean Drift") == "ocean"
    assert theme.preset_key_for_display_name("Burgundy") == "burgundy"
    assert theme.preset_key_for_display_name("Paper") == "paper"
    assert theme.preset_key_for_display_name("Mist") == "mist"


def test_unknown_and_missing_names_map_to_none():
    assert theme.preset_key_for_display_name("Neon") is None
    assert theme.preset_key_for_display_name(None) is None
    assert theme.preset_key_for_display_name("") is None


def test_luminance_weights_match_family_rule():
    assert theme.luminance(0xFFFF0000) == 0.299 * 255
    assert theme.luminance(0xFF00FF00) == 0.587 * 255
    assert theme.luminance(0xFF0000FF) == 0.114 * 255


def test_threshold_is_exclusive_182():
    assert abs(theme.luminance(0xFFB6B6B6) - 182.0) < 1e-9
    assert not theme.prefers_dark_foreground(0xFFB6B6B6)
    assert theme.prefers_dark_foreground(0xFFB7B7B7)


def test_named_grounds_classify_dark_and_light():
    assert not theme.prefers_dark_foreground(0xFF000000)
    assert theme.prefers_dark_foreground(0xFFF3EEE2)
    assert theme.prefers_dark_foreground(0xFFE6EDF5)


def test_foreground_picks_ink_or_white():
    assert theme.foreground_for(0xFFF3EEE2) == theme.FOREGROUND_INK
    assert theme.foreground_for(0xFF000000) == theme.FOREGROUND_WHITE


def test_custom_grounds_resolve_through_contrast_rule():
    assert theme.foreground_for(0xFF808080) == theme.FOREGROUND_WHITE
    assert theme.foreground_for(0xFFF0E0C0) == theme.FOREGROUND_INK
