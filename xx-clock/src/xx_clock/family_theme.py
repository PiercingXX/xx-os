"""Family theme bridge (pure half of theme/ThemePreset.kt).

On Linux there is no launcher broadcast receiver: the app wears the shared
xx_ui themes live from `~/.config/xx-wm/config.json`. This module keeps the
two portable pieces — the Android display-name → xx_ui preset-key map and
the family contrast rule (`0.299r + 0.587g + 0.114b > 182` takes the dark
ink foreground) used for custom grounds.
"""

from __future__ import annotations

ANDROID_TO_XX_UI = {
    "AMOLED Night": "amoled",
    "Graphite": "graphite",
    "Forest Night": "forest",
    "Ocean Drift": "ocean",
    "Burgundy": "burgundy",
    "Paper": "paper",
    "Mist": "mist",
    "Custom": "custom",
}

CUSTOM_THEME_NAME = "Custom"
FOREGROUND_INK = 0xFF1A1A1A
FOREGROUND_WHITE = 0xFFFFFFFF


def preset_key_for_display_name(name: str | None) -> str | None:
    if name is None:
        return None
    for display, key in ANDROID_TO_XX_UI.items():
        if display.lower() == name.strip().lower():
            return key
    return None


def luminance(argb: int) -> float:
    red = (argb >> 16) & 0xFF
    green = (argb >> 8) & 0xFF
    blue = argb & 0xFF
    return 0.299 * red + 0.587 * green + 0.114 * blue


def prefers_dark_foreground(background: int) -> bool:
    return luminance(background) > 182.0


def foreground_for(background: int) -> int:
    return FOREGROUND_INK if prefers_dark_foreground(background) else FOREGROUND_WHITE
