"""Every app follows the shell theme (desktop bridge for third-party apps).

Suite apps theme themselves through xx_ui; third-party GTK3/GTK4/libadwaita
apps only see the desktop settings and gtk.css. This module mirrors the
shell's resolved preset there:

- ``org.gnome.desktop.interface color-scheme``: prefer-dark for dark
  presets, prefer-light for light ones. Darkness is the luminance of the
  preset background from the xx_ui theme table, so paper, mist and any
  future light theme resolve without hardcoded names.
- ``gtk-theme``: adw-gtk3-dark/adw-gtk3 when installed, else
  Adwaita-dark/Adwaita.
- ``~/.config/gtk-4.0/gtk.css`` and ``~/.config/gtk-3.0/gtk.css``: the
  preset palette as libadwaita/adw-gtk3 ``@define-color`` overrides.
  Running apps pick the file up at next launch; that is acceptable.

Only an absent file, or one carrying the first-line managed marker, is
written; a user's own gtk.css is logged and left alone. Writes are atomic
(tmp file in the same directory + rename). The gsettings write is a local
dconf store and the fallback is a fire-and-forget gsettings child; file
writes run on a worker thread via schedule(). Nothing here blocks the GTK
main loop, and every entry point swallows its own errors so the bridge can
never break shell theming.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

MANAGED_MARKER = '/* managed by xx-wm desktop_theme: edits are overwritten */'

INTERFACE_SCHEMA = 'org.gnome.desktop.interface'
COLOR_SCHEME_KEY = 'color-scheme'
GTK_THEME_KEY = 'gtk-theme'

_SYSTEM_THEME_DIR = Path('/usr/share/themes')

# libadwaita/adw-gtk3 named colour -> ThemePreset attribute.
_CSS_ATTRS = (
    ('window_bg_color', 'background'),
    ('window_fg_color', 'foreground'),
    ('view_bg_color', 'background'),
    ('view_fg_color', 'foreground'),
    ('headerbar_bg_color', 'surface'),
    ('headerbar_fg_color', 'foreground'),
    ('card_bg_color', 'surface'),
    ('popover_bg_color', 'surface'),
    ('dialog_bg_color', 'surface'),
    ('sidebar_bg_color', 'background'),
    ('accent_bg_color', 'accent'),
    ('accent_color', 'accent'),
    ('borders', 'border'),
    ('shade_color', 'border'),
)


def _luminance(color: str) -> float:
    """Relative luminance of a #rrggbb string, 0.0 on garbage."""
    try:
        red = int(color[1:3], 16)
        green = int(color[3:5], 16)
        blue = int(color[5:7], 16)
    except (TypeError, ValueError, IndexError):
        return 0.0
    return (0.299 * red + 0.587 * green + 0.114 * blue) / 255


def is_dark(preset) -> bool:
    """True for dark presets (same luminance rule as the shell and xx_ui)."""
    try:
        return _luminance(preset.background) < 0.5
    except Exception:
        return True


def color_scheme_for(preset) -> str:
    """Desktop color-scheme for a resolved preset."""
    return 'prefer-dark' if is_dark(preset) else 'prefer-light'


def _theme_dirs() -> list[Path]:
    return [_SYSTEM_THEME_DIR, Path.home() / '.local' / 'share' / 'themes']


def gtk_theme_for(dark: bool, theme_dirs=None) -> str:
    """adw-gtk3 variant when installed, else the Adwaita fallback."""
    want = 'adw-gtk3-dark' if dark else 'adw-gtk3'
    dirs = [Path(d) for d in
            (theme_dirs if theme_dirs is not None else _theme_dirs())]
    for directory in dirs:
        try:
            if (directory / want).is_dir():
                return want
        except OSError:
            continue
    return 'Adwaita-dark' if dark else 'Adwaita'


def _contrast_fg(color: str) -> str:
    """Readable text on the accent: black on signal-white, white on dark."""
    return '#000000' if _luminance(color) >= 0.5 else '#ffffff'


def render_css(preset) -> str:
    """gtk.css body for a resolved preset, marker line first."""
    lines = [MANAGED_MARKER]
    for css_name, attr in _CSS_ATTRS:
        lines.append(f'@define-color {css_name} {getattr(preset, attr)};')
    lines.append(f'@define-color accent_fg_color {_contrast_fg(preset.accent)};')
    return '\n'.join(lines) + '\n'


def _config_home() -> Path:
    base = os.environ.get('XDG_CONFIG_HOME')
    if base:
        return Path(base)
    return Path.home() / '.config'


def gtk_css_paths(config_home=None) -> tuple[Path, Path]:
    """(gtk-4.0, gtk-3.0) gtk.css paths, honouring XDG_CONFIG_HOME."""
    base = Path(config_home) if config_home is not None else _config_home()
    return (base / 'gtk-4.0' / 'gtk.css', base / 'gtk-3.0' / 'gtk.css')


def should_write(path) -> bool:
    """True only for an absent file or one carrying the managed marker."""
    try:
        with open(path, encoding='utf-8') as handle:
            first = handle.readline()
    except FileNotFoundError:
        return True
    except OSError as error:
        log.info('desktop theme gtk.css unreadable %s: %s', path, error)
        return False
    return first.strip() == MANAGED_MARKER


def write_managed_css(path, content: str) -> bool:
    """Atomically write a managed gtk.css; never clobber a user's file."""
    path = Path(path)
    if not should_write(path):
        log.info('desktop theme leaves user gtk.css alone: %s', path)
        return False
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
        with open(tmp, 'w', encoding='utf-8') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except OSError as error:
        log.info('desktop theme gtk.css write failed %s: %s', path, error)
        if tmp is not None:
            try:
                tmp.unlink()
            except OSError:
                pass
        return False
    return True


def _gio_settings(schema_id: str = INTERFACE_SCHEMA):
    """Interface settings when the schema exists, else None (never raises)."""
    from gi.repository import Gio
    source = Gio.SettingsSchemaSource.get_default()
    if source is None:
        return None
    schema = source.lookup(schema_id, True)
    if schema is None:
        return None
    if not schema.has_key(COLOR_SCHEME_KEY):
        return None
    if not schema.has_key(GTK_THEME_KEY):
        return None
    return Gio.Settings.new_full(schema, None)


def apply_gsettings(color_scheme: str, gtk_theme: str, settings=None,
                    spawn=None) -> bool:
    """Mirror the scheme pair to the desktop; True when either path lands."""
    if settings is None:
        try:
            settings = _gio_settings()
        except Exception as error:
            log.info('desktop theme settings lookup failed: %s', error)
            settings = None
    if settings is not None:
        try:
            settings.set_string(COLOR_SCHEME_KEY, color_scheme)
            settings.set_string(GTK_THEME_KEY, gtk_theme)
            return True
        except Exception as error:
            log.info('desktop theme gsettings write failed: %s', error)
    spawn = spawn or subprocess.Popen
    try:
        for key, value in ((COLOR_SCHEME_KEY, color_scheme),
                           (GTK_THEME_KEY, gtk_theme)):
            spawn(['gsettings', 'set', INTERFACE_SCHEMA, key, value],
                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, close_fds=True)
        return True
    except Exception as error:
        log.info('desktop theme gsettings spawn failed: %s', error)
        return False


def apply_now(preset, config_home=None, theme_dirs=None, settings=None,
              spawn=None) -> bool:
    """Synchronous bridge for the worker thread and tests; never raises."""
    try:
        dark = is_dark(preset)
        apply_gsettings(color_scheme_for(preset),
                        gtk_theme_for(dark, theme_dirs),
                        settings=settings, spawn=spawn)
        css = render_css(preset)
        ok = True
        for path in gtk_css_paths(config_home):
            ok = write_managed_css(path, css) and ok
        return ok
    except Exception as error:
        log.info('desktop theme apply failed: %s', error)
        return False


def _idle_apply(preset) -> bool:
    threading.Thread(target=apply_now, args=(preset,), daemon=True,
                     name='xx-wm-desktop-theme').start()
    return False


def schedule(preset) -> None:
    """Mirror the resolved preset to the desktop without blocking."""
    try:
        from gi.repository import GLib
    except Exception:
        apply_now(preset)
        return
    try:
        GLib.idle_add(_idle_apply, preset)
    except Exception as error:
        log.info('desktop theme schedule failed: %s', error)
