"""Single source of truth for XX themes.

Preset values are copied exactly from ``xx-wm/launcher/src/config.py``
``THEME_PRESETS``. Reads the shell config file the same way the shell
does; a missing or invalid config falls back to amoled and never raises.
Pure module: no GTK import, safe headless and in tests.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ThemePreset:
    key: str
    name: str
    background: str
    surface: str
    surface_alt: str
    border: str
    foreground: str
    muted: str
    accent: str


THEME_PRESETS: dict[str, ThemePreset] = {
    'amoled': ThemePreset('amoled', 'AMOLED', '#000000', '#111111', '#181818', '#2f2f2f', '#f4f4f4', '#9a9a9a', '#d8d8d8'),
    'graphite': ThemePreset('graphite', 'Graphite', '#111827', '#1f2937', '#2d3748', '#4a5568', '#f7fafc', '#a0aec0', '#718096'),
    'forest': ThemePreset('forest', 'Forest', '#10261B', '#1a3a2a', '#244e3a', '#3d6b56', '#ecf4ee', '#a4b1a7', '#c9d8cc'),
    'ocean': ThemePreset('ocean', 'Ocean', '#0F1C2E', '#182a42', '#203856', '#34506e', '#edf4f7', '#9fb0bb', '#cad8df'),
    'paper': ThemePreset('paper', 'Paper', '#F3EEE2', '#e0d8cb', '#ccc4b4', '#b09c85', '#151515', '#585147', '#262626'),
    'mist': ThemePreset('mist', 'Mist', '#E6EDF5', '#d0d8e2', '#bac4cf', '#9aa8ba', '#151a1f', '#55606c', '#2f3943'),
    'aura': ThemePreset('aura', 'Aura', '#0d0b14', '#14112a', '#1e1a3a', '#3d3066', '#f0eeff', '#9080c0', '#a855f7'),
    'burgundy': ThemePreset('burgundy', 'Burgundy', '#2A1018', '#3b1a24', '#4c2330', '#6e3a4c', '#f6edef', '#ab949c', '#d9b3bf'),
}

FONT_FAMILIES = {
    'system-light': 'Sans Light',
    'monospace': 'Monospace',
    'space-mono': 'Space Mono, Monospace',
    'jetbrains-mono': 'JetBrains Mono, Monospace',
    'jetbrains-mono-nerd': 'JetBrainsMono Nerd Font, JetBrains Mono, Monospace',
}

DEFAULT_PRESET = 'amoled'
DEFAULT_FONT = 'jetbrains-mono-nerd'

DANGER_RED = '#ff6b6b'
#: Danger on light themes: #ff6b6b is ~2.4:1 on paper, unreadable as
#: text. This one reads at >= 4.5:1 on the light presets' background and
#: surface, and white on it stays legible for danger buttons.
DANGER_RED_LIGHT = '#a61b1b'
ON_DANGER_FG = '#ffffff'
#: WCAG AA for body text.
MIN_TEXT_CONTRAST = 4.5

_CUSTOM_HEX_RE = re.compile(r'#[0-9a-fA-F]{6}')


@dataclass(frozen=True)
class Theme:
    key: str
    background: str
    surface: str
    surface_alt: str
    border: str
    foreground: str
    muted: str
    accent: str
    font_family: str
    scale: float
    dark: bool
    #: Derived from background/dark when left empty (see danger_for).
    danger: str = ''

    def __post_init__(self) -> None:
        if not self.danger:
            object.__setattr__(self, 'danger',
                               danger_for(self.background, self.dark))


def _derive_shades(bg: str) -> tuple[str, str, str]:
    """Derive surface/surface_alt/border shades from background color."""
    match = re.match(r'#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})', bg)
    if not match:
        return ('#111111', '#181818', '#2f2f2f')
    rgb = tuple(int(match.group(i), 16) for i in (1, 2, 3))
    # Near-black has no headroom to darken into; amoled's own trio is the
    # canonical separation for that regime.
    if max(rgb) <= 0x18:
        return ('#111111', '#181818', '#2f2f2f')

    def scale(factor: float) -> str:
        return '#' + ''.join(f'{max(0, int(v * factor)):02x}' for v in rgb)

    return (scale(0.82), scale(0.72), scale(0.55))


def _luminance(bg: str) -> float:
    match = re.match(r'#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})', bg or '')
    if not match:
        return 0.0
    r, g, b = (int(match.group(i), 16) for i in (1, 2, 3))
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def _relative_luminance(color: str) -> float:
    match = re.match(r'#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})', color or '')
    if not match:
        return 0.0
    channels = []
    for index in (1, 2, 3):
        value = int(match.group(index), 16) / 255
        channels.append(value / 12.92 if value <= 0.03928
                        else ((value + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast(first: str, second: str) -> float:
    """WCAG contrast ratio of two ``#rrggbb`` colors (1.0 .. 21.0)."""
    high, low = sorted((_relative_luminance(first),
                        _relative_luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def danger_for(background: str, dark: bool) -> str:
    """The danger color for a theme: the bright red on dark themes, a
    darker red on light ones, darkened further until it reads at
    ``MIN_TEXT_CONTRAST`` on ``background`` (custom light colors)."""
    if dark:
        return DANGER_RED
    red, green, blue = (int(DANGER_RED_LIGHT[i:i + 2], 16) for i in (1, 3, 5))
    color = DANGER_RED_LIGHT
    while contrast(color, background) < MIN_TEXT_CONTRAST and red > 0x40:
        red, green, blue = (int(v * 0.9) for v in (red, green, blue))
        color = f'#{red:02x}{green:02x}{blue:02x}'
    return color


def _resolve(data: dict) -> ThemePreset | None:
    """Preset for ``theme: custom`` derived from ``custom_background``.

    Mirrors the shell's ``resolve_theme``: shades derive from the color,
    text comes from the canonical dark (amoled) or light (paper) preset by
    luminance. Returns None when the custom color is missing or garbage.
    """
    color = data.get('custom_background')
    if not isinstance(color, str) or not _CUSTOM_HEX_RE.fullmatch(color):
        return None
    surface, surface_alt, border = _derive_shades(color)
    text = THEME_PRESETS['amoled'] if _luminance(color) < 0.5 else THEME_PRESETS['paper']
    return ThemePreset(
        key='custom', name='Custom', background=color,
        surface=surface, surface_alt=surface_alt, border=border,
        foreground=text.foreground, muted=text.muted, accent=text.accent,
    )


def config_path() -> Path:
    base = os.environ.get('XDG_CONFIG_HOME') or str(Path.home() / '.config')
    return Path(base) / 'xx-wm' / 'config.json'


def read_config(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or config_path()).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_font_family(data: dict) -> str:
    key = data.get('font', DEFAULT_FONT)
    if key == 'custom':
        family = data.get('custom_font_family')
        if isinstance(family, str) and family.strip():
            return family.strip()
        return FONT_FAMILIES[DEFAULT_FONT]
    if not isinstance(key, str):
        return FONT_FAMILIES[DEFAULT_FONT]
    return FONT_FAMILIES.get(key, FONT_FAMILIES[DEFAULT_FONT])


def resolve_scale(data: dict) -> float:
    try:
        return max(0.5, min(2.0, float(data.get('text_size_scale', 1.0))))
    except (TypeError, ValueError):
        return 1.0


def load(path: Path | str | None = None) -> Theme:
    """Active theme. Missing or invalid config falls back, never raises."""
    data = read_config(Path(path) if path is not None else None)
    key = data.get('theme', DEFAULT_PRESET)
    if not isinstance(key, str):
        key = DEFAULT_PRESET
    preset: ThemePreset | None
    if key == 'custom':
        preset = _resolve(data) or THEME_PRESETS[DEFAULT_PRESET]
    else:
        preset = THEME_PRESETS.get(key, THEME_PRESETS[DEFAULT_PRESET])
    dark = _luminance(preset.background) < 0.5
    return Theme(
        key=preset.key,
        background=preset.background,
        surface=preset.surface,
        surface_alt=preset.surface_alt,
        border=preset.border,
        foreground=preset.foreground,
        muted=preset.muted,
        accent=preset.accent,
        font_family=resolve_font_family(data),
        scale=resolve_scale(data),
        dark=dark,
    )


PRESETS = THEME_PRESETS


def preset_keys() -> list[str]:
    return list(THEME_PRESETS)
