"""How a note's ``color:`` reaches the screen (pure, ported from Android).

Two modes, chosen in Settings; the file never changes meaning either way:

- ``keep`` (default): Keep's twelve names as Keep's palette, a dark set on
  dark grounds and a light set on light ones (``NoteColors.kt``).
- ``tones``: D12's six surface tones (``ColorTones.kt``): fills that blend
  the foreground into the ground at 5/9/14 %, or a hairline.

Unknown, blank or default names do less: a plain card.
"""

from __future__ import annotations

KEEP_PICKER = (
    "white", "tomato", "tangerine", "banana", "basil", "sage", "peacock",
    "blueberry", "lavender", "grape", "flamingo", "graphite",
)

KEEP_ALIASES = {
    "sand": "banana", "yellow": "banana", "orange": "tangerine",
    "gray": "graphite", "brown": "clay", "red": "tomato", "coral": "tomato",
    "pink": "flamingo", "purple": "lavender", "mint": "basil",
    "green": "basil", "teal": "sage", "blue": "peacock",
    "cerulean": "peacock", "darkblue": "blueberry",
}

KEEP_DARK = {
    "tomato": "#77172e", "tangerine": "#692b17", "banana": "#7c4a03",
    "basil": "#264d3b", "sage": "#0c625d", "peacock": "#256377",
    "blueberry": "#284255", "lavender": "#472e5b", "grape": "#5b2245",
    "flamingo": "#6c394f", "graphite": "#3c3f43", "clay": "#4b443a",
}

KEEP_LIGHT = {
    "tomato": "#faafa8", "tangerine": "#f39f76", "banana": "#fff8b8",
    "basil": "#e2f6d3", "sage": "#b4ddd3", "peacock": "#d4e4ed",
    "blueberry": "#aeccdc", "lavender": "#d3bfdb", "grape": "#e8c6de",
    "flamingo": "#f6e2dd", "graphite": "#e8eaed", "clay": "#e9e3d4",
}

# Tone per canonical name, then the aliases older vaults carry.
TONE_INK = "ink"
TONE_RAISED = "raised"
TONE_GRAPHITE = "graphite"
TONE_SLATE = "slate"
TONE_HAIR_LEFT = "hair-left"
TONE_HAIR_FULL = "hair-full"

_CANONICAL_TONES = {
    "white": TONE_INK, "graphite": TONE_INK,
    "banana": TONE_RAISED, "tangerine": TONE_RAISED,
    "tomato": TONE_GRAPHITE, "flamingo": TONE_GRAPHITE,
    "basil": TONE_SLATE, "sage": TONE_SLATE,
    "peacock": TONE_HAIR_LEFT, "blueberry": TONE_HAIR_LEFT,
    "lavender": TONE_HAIR_FULL, "grape": TONE_HAIR_FULL,
}

_ALIAS_TONES = {
    "sand": TONE_RAISED, "yellow": TONE_RAISED, "orange": TONE_RAISED,
    "gray": TONE_INK, "brown": TONE_GRAPHITE, "red": TONE_GRAPHITE,
    "coral": TONE_GRAPHITE, "pink": TONE_HAIR_FULL,
    "purple": TONE_HAIR_FULL, "mint": TONE_SLATE, "green": TONE_SLATE,
    "teal": TONE_HAIR_LEFT, "blue": TONE_HAIR_LEFT,
    "cerulean": TONE_HAIR_LEFT, "darkblue": TONE_HAIR_LEFT,
}


def keep_color_key(name: str | None) -> str | None:
    """Palette key for a ``color:`` value; None for default/blank/unknown."""
    key = (name or "").strip().lower()
    if not key or key in ("white", "default"):
        return None
    if key in KEEP_PICKER:
        return key
    return KEEP_ALIASES.get(key)


def tone_for(name: str | None) -> str:
    key = (name or "").strip().lower()
    if not key:
        return TONE_INK
    return _CANONICAL_TONES.get(key) or _ALIAS_TONES.get(key) or TONE_INK


def card_class(name: str | None, mode: str) -> str | None:
    """CSS class for a card's surface, or None for a plain card.

    ``keep`` mode: ``xx-note-keep-<key>`` (the stylesheet picks the dark or
    light hex for the current ground). ``tones`` mode: ``xx-note-tone-<tone>``.
    """
    if mode == "tones":
        tone = tone_for(name)
        return None if tone == TONE_INK else f"xx-note-tone-{tone}"
    key = keep_color_key(name)
    return None if key is None else f"xx-note-keep-{key}"
