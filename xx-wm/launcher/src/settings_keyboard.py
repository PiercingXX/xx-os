"""Keyboard layout picker (lane sm_device, ref keyboard-layout.sh).

The session script owns the boot default from the `keyboard_layout`
config key (default `us`, QWERTY); this picker writes the key, which
xx-keyboard follows live, and applies the hardware keymap with
gsettings, which phoc reads. Piercing Num is on-screen only: its
hardware keymap is `xkb_for('piercing-num')`, plain US. Unprivileged:
fire-and-forget gsettings writes, never waited on.
"""
from __future__ import annotations

import re
import subprocess

import settings_page
import settings_ui as ui
from keyboard_layouts import xkb_for

DEFAULT_KEY = 'us'

PIERCING_NUM = 'piercing-num'

#: Shown under the picker title: Piercing Num has no hardware keymap.
OSK_ONLY_NOTE = 'Piercing Num is on-screen only; hardware keys stay QWERTY.'

# The three first-boot choices lead (keyboard_layouts.CHOICES order).
KEYBOARD_PRESETS = (
    ('QWERTY', 'us', ''),
    ('US Colemak', 'us', 'colemak'),
    ('Piercing Num', PIERCING_NUM, ''),
    ('Colemak-DH', 'us', 'colemak_dh'),
    ('Dvorak', 'us', 'dvorak'),
    ('UK', 'gb', ''),
    ('DE', 'de', ''),
)

_LAYOUT_RE = re.compile(r'^[a-z][a-z0-9-]{1,23}$')
_VARIANT_RE = re.compile(r'^[a-z0-9_-]{1,32}$')


def parse_layout_key(value: str) -> tuple[str, str]:
    """'us+colemak' → ('us', 'colemak'), 'piercing-num' →
    ('piercing-num', ''); garbage falls back to the default (QWERTY)."""
    text = str(value or '').strip().lower()
    if text == PIERCING_NUM:
        return (PIERCING_NUM, '')
    if '+' in text:
        layout, _, variant = text.partition('+')
    else:
        layout, variant = text, ''
    if not _LAYOUT_RE.match(layout):
        return (DEFAULT_KEY, '')
    if variant and not _VARIANT_RE.match(variant):
        return (layout, '')
    return (layout, variant)


def format_layout_key(layout: str, variant: str) -> str:
    """('us', 'colemak') → 'us+colemak' for the config key."""
    layout = str(layout or '').strip().lower()
    variant = str(variant or '').strip().lower()
    return '%s+%s' % (layout, variant) if variant else layout


def gsettings_sources(layout: str, variant: str) -> str:
    """gsettings value for sources/mru-sources: "[('xkb', 'us+colemak')]"."""
    return "[('xkb', '%s')]" % format_layout_key(layout, variant)


def validate_custom(layout: str, variant: str) -> str | None:
    """Error string for a custom layout+variant, None when acceptable."""
    if not _LAYOUT_RE.match(str(layout or '').strip().lower()):
        return 'Layout must be 2–24 lowercase letters, digits or dashes.'
    if variant and not _VARIANT_RE.match(str(variant).strip().lower()):
        return 'Variant must be lowercase letters, digits, _ or -.'
    return None


def apply_keyboard_layout(layout: str, variant: str) -> None:
    """Live-apply via gsettings (fire-and-forget, never waited on).

    An on-screen-only primary (Piercing Num) maps to its hardware
    keymap first, so xkb never sees a layout it does not have."""
    layout, variant = parse_layout_key(
        xkb_for(format_layout_key(layout, variant)))
    value = gsettings_sources(layout, variant)
    for key in ('sources', 'mru-sources'):
        try:
            subprocess.Popen(
                ['gsettings', 'set',
                 'org.gnome.desktop.input-sources', key, value],
                close_fds=True,
            )
        except (OSError, ValueError):
            pass


def current_label(key: str) -> str:
    """Preset label for a config key, or 'Custom (…)'."""
    for label, layout, variant in KEYBOARD_PRESETS:
        if format_layout_key(layout, variant) == key:
            return label
    return 'Custom (%s)' % key


class KeyboardSection(settings_page._Section):
    """Keyboard layout picker: presets plus a custom layout+variant."""

    key = 'keyboard'
    title = 'Keyboard'

    def __init__(self) -> None:
        self._row = None
        self._status = None

    def build(self, win) -> list:
        self._row = ui.nav_row('Keyboard layout', '',
                               lambda r: self._show_presets(win, r))
        built = ui.group(self.title, [self._row], note='')
        self._status = built[2]
        self._render(win)
        return built

    def refresh(self, win) -> None:
        self._render(win)

    def _key(self, win) -> str:
        try:
            return str(win.config.data.get('keyboard_layout', DEFAULT_KEY))
        except Exception:
            return DEFAULT_KEY

    def _render(self, win) -> None:
        key = self._key(win)
        label = current_label(key)
        if key == PIERCING_NUM:
            label = '%s · on-screen only' % label
        ui.set_subtitle(self._row, label)

    def _show_presets(self, win, anchor) -> None:
        actions = [(label, lambda layout=layout, variant=variant:
                    self._pick(win, layout, variant))
                   for label, layout, variant in KEYBOARD_PRESETS]
        actions.append(('Custom…', lambda: self._open_custom(win, anchor)))
        win.item_actions._show_action_menu(anchor, 'Keyboard layout',
                                           actions, subtitle=OSK_ONLY_NOTE)

    def _open_custom(self, win, anchor):
        layout, variant = parse_layout_key(self._key(win))
        return ui.form_sheet(
            win, anchor, 'Custom layout',
            [('layout', 'Layout (e.g. fr)', layout, 'text'),
             ('variant', 'Variant (blank for none)', variant, 'text')],
            'Apply', lambda values: self._pick_custom(win, values))

    def _pick(self, win, layout: str, variant: str) -> None:
        key = format_layout_key(layout, variant)
        try:
            win.config.set('keyboard_layout', key)
        except Exception:
            pass
        # xx-keyboard follows the key; xkb gets the hardware keymap.
        apply_keyboard_layout(*parse_layout_key(xkb_for(key)))
        self._say('')
        self._render(win)

    def _pick_custom(self, win, values: dict) -> str | None:
        layout = str(values.get('layout', '')).strip().lower()
        variant = str(values.get('variant', '')).strip().lower()
        error = validate_custom(layout, variant)
        if error is not None:
            return error
        self._pick(win, layout, variant)
        return None

    def _say(self, text: str) -> None:
        ui.say(self._status, text)
