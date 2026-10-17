"""Primary keyboard layout contract shared with the setup screen.

The ``keyboard_layout`` key in ``~/.config/xx-wm/config.json`` holds the
user's PRIMARY layout. Valid values for the new setup screen:

- ``us``: QWERTY, the default
- ``us+colemak``: Colemak
- ``piercing-num``: Piercing Num, OSK-only and not an xkb layout

The hardware keymap (gsettings ``org.gnome.desktop.input-sources``, which
phoc reads) must stay a real xkb layout, so ``piercing-num`` maps to
``us`` there. The session script duplicates this mapping inline (it runs
before the shell is importable); keep the two in sync. Pure, no imports.
"""
from __future__ import annotations

import re

DEFAULT_PRIMARY = 'us'

CHOICES: tuple[tuple[str, str], ...] = (
    ('us', 'QWERTY'),
    ('us+colemak', 'Colemak'),
    ('piercing-num', 'Piercing Num'),
)

_VALID_KEYS = frozenset(key for key, _label in CHOICES)

_LAYOUT_RE = re.compile(r'^[a-z][a-z0-9-]{1,23}$')
_VARIANT_RE = re.compile(r'^[a-z0-9_-]{1,32}$')


def normalize(primary: object) -> str:
    """Lowercased valid primary, or the default when garbage."""
    text = str(primary or '').strip().lower()
    if text in _VALID_KEYS:
        return text
    if '+' in text:
        layout, _, variant = text.partition('+')
    else:
        layout, variant = text, ''
    if not _LAYOUT_RE.match(layout):
        return DEFAULT_PRIMARY
    if variant and not _VARIANT_RE.match(variant):
        return layout
    return text


def xkb_for(primary: object) -> str:
    """Hardware xkb layout for a primary: Piercing Num is OSK-only."""
    key = normalize(primary)
    if key == 'piercing-num':
        return 'us'
    return key
