"""
Launcher-wide font (design.md "Fonts"): one display-level provider sets the
configured family on every shell window and popover. Surfaces no longer
hardcode a family; anything needing a deliberate override (e.g. PIN dots)
still declares its own.
"""
from __future__ import annotations

import re

from config import DEFAULT_CONFIG, FONT_FAMILIES

_provider = None

# A family reaches CSS inside single quotes; these characters would let a
# hostile value (settable via a restored backup) escape the value or the
# rule block and break every later load_from_data call.
_CSS_UNSAFE = re.compile(r"""['"`\\;{}]""")

_DEFAULT_FAMILY = FONT_FAMILIES[DEFAULT_CONFIG['font']]


def sanitize_font_family(family: str) -> str:
    cleaned = _CSS_UNSAFE.sub('', family or '').strip()
    return cleaned or _DEFAULT_FAMILY


def _family_css(family: str) -> bytes:
    # libadwaita draws button labels bold; the house type is regular
    # (common/DESIGN.md: no bold), so every shell button starts at 400 and
    # a surface opts into 500 with its own class rule.
    return (f"window, popover {{ font-family: '{family}'; }}\n"
            "window button, popover button { font-weight: 400; }"
            ).encode('utf-8')


def apply_global_font(family: str) -> None:
    global _provider
    from gi.repository import Gdk, Gtk
    display = Gdk.Display.get_default()
    if display is None:
        return
    if _provider is None:
        _provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    try:
        _provider.load_from_data(_family_css(sanitize_font_family(family)))
    except Exception as error:
        from shell_log import get_logger
        get_logger('font_theme').warning(
            'font family %r unusable (%s); falling back to default', family, error)
        try:
            _provider.load_from_data(_family_css(_DEFAULT_FAMILY))
        except Exception as fallback_error:
            get_logger('font_theme').warning(
                'default font provider reload failed: %s', fallback_error)
