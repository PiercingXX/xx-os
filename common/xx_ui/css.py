"""Render the stylesheet for a theme and install it on the display.

``render`` is pure. ``apply`` installs the CSS, forces the libadwaita
color scheme, and hot-reloads the shell config file (debounced).
Libadwaita is optional: everything works without it.
"""

from __future__ import annotations

import re
from pathlib import Path
from string import Template

from . import theme as _theme

_CSS_UNSAFE = re.compile(r"""['"`\\;{}]""")

_BASE_PX = {
    'display_px': 30,
    'display_large_px': 32,
    'hero_px': 36,
    'title_px': 16,
    'body_px': 15,
    'secondary_px': 14,
    'caption_px': 11,
    'chip_px': 12,
    'tab_px': 11,
    'time_px': 13,
    'tiny_px': 10,
    'digit_px': 28,
    'keysub_px': 9,
    'dlg_title_px': 18,
    'gutter_px': 20,
    'rowmin_px': 64,
}

_DEFAULT_FAMILY = 'JetBrainsMono Nerd Font, JetBrains Mono, Monospace'


def sanitize_font_family(family: str) -> str:
    cleaned = _CSS_UNSAFE.sub('', family or '').strip()
    return cleaned or _DEFAULT_FAMILY


# Brand display face. Used only while the UI font is one of the house
# monos; any other family the user picked also sets the display role.
_DISPLAY_FACES = ('Space Mono', 'SpaceMono Nerd Font')
_HOUSE_MONO_MARKERS = ('jetbrains', 'space mono', 'spacemono')


def display_family(font_family: str) -> str:
    """Family list for ``.xx-display-face`` (pure)."""
    family = sanitize_font_family(font_family)
    if any(marker in family.lower() for marker in _HOUSE_MONO_MARKERS):
        return ', '.join([*_DISPLAY_FACES, family])
    return family


def _template() -> str:
    return (Path(__file__).with_name('style.css')).read_text(encoding='utf-8')


def render(active: _theme.Theme) -> str:
    mapping: dict[str, str] = {
        'background': active.background,
        'surface': active.surface,
        'surface_alt': active.surface_alt,
        'border': active.border,
        'foreground': active.foreground,
        'muted': active.muted,
        'accent': active.accent,
        'danger': active.danger,
        'on_danger': _theme.ON_DANGER_FG,
        'font_family': sanitize_font_family(active.font_family),
        'display_family': display_family(active.font_family),
    }
    for key, base in _BASE_PX.items():
        mapping[key] = str(round(base * active.scale))
    return Template(_template()).substitute(mapping)


_callbacks: list = []
_provider = None
_monitor = None
_monitor_key: tuple | None = None
_debounce_id = 0


def on_change(callback):
    """Register ``callback(theme)`` for live theme switches."""
    _callbacks.append(callback)
    def _unsubscribe():
        try:
            _callbacks.remove(callback)
        except ValueError:
            pass
    return _unsubscribe


def _display_for(target):
    from gi.repository import Gdk, Gtk
    if target is None:
        return Gdk.Display.get_default()
    if isinstance(target, Gdk.Display):
        return target
    get_display = getattr(target, 'get_display', None)
    if callable(get_display):
        try:
            display = get_display()
            if display is not None:
                return display
        except Exception:
            pass
    if isinstance(target, Gtk.Application):
        return Gdk.Display.get_default()
    return Gdk.Display.get_default()


def _install_css(css: str, display) -> None:
    from gi.repository import Gtk
    global _provider
    if _provider is None:
        _provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display, _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _provider.load_from_data(css.encode('utf-8'))


def _force_adw_scheme(dark: bool) -> None:
    try:
        import gi
        gi.require_version('Adw', '1')
        from gi.repository import Adw
    except Exception:
        return
    try:
        manager = Adw.StyleManager.get_default()
    except Exception:
        return
    if manager is None:
        return
    try:
        manager.set_color_scheme(
            Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
    except Exception:
        pass


def install(active: _theme.Theme) -> _theme.Theme:
    """Install one theme object on the default display (None-safe).

    Lets apps honor an app-local manual pick while still rendering
    through the shared stylesheet; ``apply``/``reload`` stay the
    config-driven paths.
    """
    from gi.repository import Gdk
    display = Gdk.Display.get_default()
    if display is not None:
        _install_css(render(active), display)
    _force_adw_scheme(active.dark)
    return active


def reload() -> _theme.Theme:
    """Re-read the config and reinstall the CSS. Returns the new theme."""
    active = _theme.load()
    from gi.repository import Gdk
    display = Gdk.Display.get_default()
    if display is not None and _provider is not None:
        _install_css(render(active), display)
    _force_adw_scheme(active.dark)
    for callback in list(_callbacks):
        callback(active)
    return active


def _schedule_reload() -> bool:
    global _debounce_id
    _debounce_id = 0
    reload()
    return False


def _on_config_event(monitor, child, other, event) -> None:
    from gi.repository import Gio, GLib
    global _debounce_id
    try:
        changed = child.get_basename() == 'config.json'
    except Exception:
        changed = False
    if not changed or event not in (
            Gio.FileMonitorEvent.CHANGES_DONE_HINT,
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CREATED):
        return
    if _debounce_id:
        GLib.source_remove(_debounce_id)
    _debounce_id = GLib.timeout_add(150, _schedule_reload)


def apply(target=None) -> _theme.Theme:
    """Install the design system. Idempotent; safe headless (no display)."""
    global _monitor, _monitor_key
    active = _theme.load()
    display = _display_for(target)
    if display is not None:
        _install_css(render(active), display)
    _force_adw_scheme(active.dark)
    path = _theme.config_path()
    key = (str(path),)
    if _monitor_key != key:
        _monitor = None
        _monitor_key = key
        try:
            from gi.repository import Gio
            watched = Gio.File.new_for_path(str(path.parent))
            _monitor = watched.monitor_directory(Gio.FileMonitorFlags.NONE, None)
            _monitor.connect('changed', _on_config_event)
        except Exception:
            _monitor = None
    return active
