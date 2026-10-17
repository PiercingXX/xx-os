"""
Heads-up notification banners (design.md: DnD "silences notification sounds
and banners").

A brief Layer.TOP card over the current screen: app name + summary + body.
New arrivals replace the visible card immediately (only the latest is kept),
tap acts like the shade row tap (default action, else desktop launch), swipe
or drag up dismisses the card while the notification stays in the shade.

Headless-safe like hud.py: without PyGObject only the pure policy/state
helpers exist and BannerWindow degrades to a no-op shell with the same API.
"""
from __future__ import annotations

import logging
import subprocess

_log = logging.getLogger('banner')

from config import ShellConfig, ThemePreset

_HAS_GTK = True
try:
    import gi
except ImportError:
    _HAS_GTK = False

if _HAS_GTK:
    try:
        gi.require_version('Gtk4LayerShell', '1.0')
        _HAS_LAYER = True
    except (ValueError, ImportError):
        _HAS_LAYER = False

    gi.require_version('Gtk', '4.0')
    from gi.repository import GLib, Gtk

    if _HAS_LAYER:
        from gi.repository import Gtk4LayerShell as LayerShell

_NO_BANNER_HINT = 'x-xx-wm-no-banner'

_SHELL_INTERNAL_IDS = frozenset({999901, 999902, 999903, 999904, 999905})

_BANNER_SHORT_MS = 4000
_BANNER_LONG_MS = 5000
_LONG_BODY_CHARS = 120
_BANNER_REVEAL_MS = 120
_BANNER_HIDE_GRACE_MS = 170
_SWIPE_UP_VY = -200
_DRAG_UP_PX = 60


def _unwrap(value):
    unpack = getattr(value, 'unpack', None)
    if callable(unpack):
        try:
            return unpack()
        except Exception:
            return value
    return value


def _hint_suppresses(hints) -> bool:
    if not isinstance(hints, dict) or _NO_BANNER_HINT not in hints:
        return False
    try:
        value = _unwrap(hints.get(_NO_BANNER_HINT))
    except Exception:
        return True
    return value not in (False, 0, '', None)


def _urgency_of(urgency) -> int:
    urgency = _unwrap(urgency)
    try:
        return int(urgency)
    except (TypeError, ValueError):
        return 1


def should_show_banner(
    dnd_active: bool = False,
    is_alarm: bool = False,
    urgency=1,
    locked: bool = False,
    hints=None,
    notif_id: int = 0,
    shade_visible: bool = False,
) -> bool:
    """Pure banner policy: True when this notification earns a heads-up card.

    Suppressed for shell-internal ids, the x-xx-wm-no-banner hint, low
    urgency, a visible lock screen, an open notification shade (the card
    would cover the shade header redundantly), and DnD (alarms/urgency 2
    punch through). Muted apps and Focus-held notifications never reach
    here: main.py drops those before the shade, same as sounds.
    """
    try:
        nid = int(notif_id)
    except (TypeError, ValueError):
        nid = 0
    if nid in _SHELL_INTERNAL_IDS:
        return False
    if _hint_suppresses(hints):
        return False
    urg = _urgency_of(urgency)
    if urg == 0:
        return False
    if locked:
        return False
    if shade_visible:
        return False
    if dnd_active and not (is_alarm or urg == 2):
        return False
    return True


def banner_duration_ms(body) -> int:
    """Card hold time: longer bodies earn the longer hold."""
    try:
        length = len(body) if body is not None else 0
    except TypeError:
        length = 0
    return _BANNER_LONG_MS if length > _LONG_BODY_CHARS else _BANNER_SHORT_MS


def tap_target(actions, desktop_entry) -> str:
    """Tap dispatch without side effects: 'default', 'launch', or 'none'."""
    for item in actions or []:
        try:
            key = item[0]
        except (TypeError, IndexError):
            continue
        if key == 'default':
            return 'default'
    return 'launch' if desktop_entry else 'none'


class BannerState:
    """GTK-free current/pending card: show() always keeps only the latest."""

    def __init__(self) -> None:
        self.current: dict | None = None

    def show(
        self,
        notif_id: int,
        app_name: str = '',
        summary: str = '',
        body: str = '',
        desktop_entry: str = '',
        actions=None,
        hints=None,
    ) -> dict:
        self.current = {
            'id': notif_id,
            'app_name': app_name,
            'summary': summary,
            'body': body,
            'desktop_entry': desktop_entry,
            'actions': list(actions) if actions else [],
            'hints': dict(hints) if isinstance(hints, dict) else {},
        }
        return self.current

    def clear(self) -> None:
        self.current = None

    @property
    def current_id(self):
        return self.current['id'] if self.current is not None else None

    def resolve_tap(self) -> str:
        if self.current is None:
            return 'none'
        return tap_target(
            self.current.get('actions'), self.current.get('desktop_entry'))


def dispatch_tap(state: BannerState, daemon, launch_fn,
                 allow_fn=None) -> str:
    """Tap the current card: default action over the daemon, else launch.

    Mirrors the shade row tap (ActionInvoked then close, or desktop launch
    then close) with a fake-friendly seam: daemon and launch_fn are injected.
    Swipe/drag dismissal never calls this, so the shade row survives those.
    `allow_fn(desktop_entry)` vetoes the tap when it returns False (the
    Nope gate); a vetoed tap reports 'blocked' and launches nothing.
    """
    current = getattr(state, 'current', None)
    if not current:
        return 'none'
    target = tap_target(current.get('actions'), current.get('desktop_entry'))
    if target != 'none' and callable(allow_fn):
        try:
            if not allow_fn(current.get('desktop_entry') or ''):
                return 'blocked'
        except Exception:
            pass
    notif_id = current.get('id')
    if target == 'default':
        if daemon is not None:
            try:
                daemon.invoke_action(notif_id, 'default')
            except Exception:
                pass
            try:
                resident = bool((current.get('hints') or {}).get(
                    'resident', False))
            except Exception:
                resident = False
            if not resident:
                try:
                    daemon.close_from_shell(notif_id)
                except Exception:
                    pass
        return 'default'
    if target == 'launch':
        if launch_fn is not None:
            try:
                launch_fn(current.get('desktop_entry'))
            except Exception:
                pass
        if daemon is not None:
            try:
                daemon.close_from_shell(notif_id)
            except Exception:
                pass
        return 'launch'
    return 'none'


def _launch_desktop_entry(desktop_entry: str) -> None:
    """Launch a desktop entry off the main thread's critical path.

    Gio launch first, gtk-launch fallback like the shade row tap; Popen (never
    blocking) so the main loop stays responsive.
    """
    entry = str(desktop_entry or '')
    if not entry:
        return
    try:
        from gi.repository import Gio
        app_info = Gio.DesktopAppInfo.new(f'{entry}.desktop')
        if app_info is not None:
            app_info.launch([], None)
            return
    except Exception:
        pass
    try:
        subprocess.Popen(['gtk-launch', entry], close_fds=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (FileNotFoundError, OSError, ValueError):
        pass


def theme_css(preset: ThemePreset) -> str:
    return f"""
    .banner-card {{
        background: {preset.surface};
        color: {preset.foreground};
        border-radius: 16px;
        border: 1px solid {preset.border};
        padding: 12px 16px;
    }}
    .banner-app {{
        font-size: 11pt;
        letter-spacing: 0.14em;
        color: {preset.muted};
    }}
    .banner-summary {{
        font-size: 16pt;
        font-weight: 400;
        color: {preset.foreground};
    }}
    .banner-body {{
        font-size: 11.5pt;
        color: {preset.foreground};
    }}
    """


if _HAS_GTK:

    class BannerWindow(Gtk.Window):
        """TOP-anchored heads-up card; newer shows replace the visible one."""

        def __init__(
            self,
            config: ShellConfig | None = None,
            notif_daemon=None,
            launch_fn=None,
            nope_state=None,
        ) -> None:
            super().__init__()
            self._config = config if config is not None else ShellConfig()
            self._daemon = notif_daemon
            self._launch_fn = launch_fn or _launch_desktop_entry
            self._nope = nope_state
            self._state = BannerState()
            self._timer_id = None
            self._hide_id = None

            self.set_decorated(False)
            self.set_resizable(False)

            if _HAS_LAYER and LayerShell.is_supported():
                LayerShell.init_for_window(self)
                LayerShell.set_layer(self, LayerShell.Layer.TOP)
                LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
                LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
                LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
                LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, False)
                LayerShell.set_exclusive_zone(self, 0)
                LayerShell.set_keyboard_mode(
                    self, LayerShell.KeyboardMode.NONE)
                for edge in (LayerShell.Edge.TOP, LayerShell.Edge.LEFT,
                             LayerShell.Edge.RIGHT):
                    LayerShell.set_margin(self, edge, 8)

            self._theme_provider = Gtk.CssProvider()
            self._theme_provider.load_from_data(
                theme_css(self._display_preset()).encode('utf-8'))
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(), self._theme_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

            win_css = Gtk.CssProvider()
            win_css.load_from_data(b'window { background: transparent; }')
            self.get_style_context().add_provider(
                win_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 10,
            )

            self._revealer = Gtk.Revealer(
                transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                transition_duration=_BANNER_REVEAL_MS,
                reveal_child=False,
            )
            self._revealer.set_child(self._build_card())
            self.set_child(self._revealer)

        def _build_card(self) -> Gtk.Widget:
            from gi.repository import Pango

            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            card.add_css_class('banner-card')
            card.set_hexpand(True)
            card.set_vexpand(False)
            card.set_valign(Gtk.Align.START)

            self._app_label = Gtk.Label(xalign=0)
            self._app_label.add_css_class('banner-app')
            self._app_label.set_ellipsize(Pango.EllipsizeMode.END)
            self._app_label.set_max_width_chars(36)

            self._summary_label = Gtk.Label(xalign=0)
            self._summary_label.add_css_class('banner-summary')
            self._summary_label.set_ellipsize(Pango.EllipsizeMode.END)
            self._summary_label.set_max_width_chars(36)
            self._summary_label.set_single_line_mode(True)

            self._body_label = Gtk.Label(xalign=0, wrap=True)
            self._body_label.add_css_class('banner-body')
            self._body_label.set_ellipsize(Pango.EllipsizeMode.END)
            self._body_label.set_lines(2)
            self._body_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self._body_label.set_max_width_chars(36)

            card.append(self._app_label)
            card.append(self._summary_label)
            card.append(self._body_label)

            tap = Gtk.GestureClick.new()
            tap.connect('released', lambda *_: self._on_tap())
            card.add_controller(tap)

            swipe = Gtk.GestureSwipe.new()
            swipe.connect('swipe', self._on_swipe)
            card.add_controller(swipe)

            drag = Gtk.GestureDrag.new()
            drag.connect('drag-end', self._on_drag_end)
            card.add_controller(drag)
            return card

        def apply_theme(self, preset: ThemePreset | None = None) -> None:
            data = theme_css(
                preset if preset is not None else self._display_preset())
            self._theme_provider.load_from_data(data.encode('utf-8'))

        def _display_preset(self) -> ThemePreset:
            from window import resolve_theme
            return resolve_theme(self._config)

        def show_notification(
            self,
            notif_id: int,
            app_name: str,
            summary: str,
            body: str,
            desktop_entry: str = '',
            actions=None,
            hints=None,
        ) -> None:
            current = self._state.show(
                notif_id, app_name, summary, body, desktop_entry,
                actions, hints)
            self._app_label.set_text(str(current['app_name'] or '').upper())
            self._summary_label.set_text(str(current['summary'] or ''))
            text = str(current['body'] or '')
            if text.strip():
                from notification_shade import notif_body_to_pango_markup
                self._body_label.set_markup(notif_body_to_pango_markup(text))
                self._body_label.set_visible(True)
            else:
                self._body_label.set_visible(False)
            if self._timer_id is not None:
                GLib.source_remove(self._timer_id)
                self._timer_id = None
            if self._hide_id is not None:
                GLib.source_remove(self._hide_id)
                self._hide_id = None
            self.set_visible(True)
            self.present()
            self._revealer.set_reveal_child(True)
            self._timer_id = GLib.timeout_add(
                banner_duration_ms(text), self._on_timeout)

        def hide_banner(self) -> None:
            if self._timer_id is not None:
                try:
                    GLib.source_remove(self._timer_id)
                except Exception:
                    pass
                self._timer_id = None
            self._state.clear()
            try:
                self._revealer.set_reveal_child(False)
            except Exception:
                pass
            if self._hide_id is not None:
                try:
                    GLib.source_remove(self._hide_id)
                except Exception:
                    pass
            try:
                self._hide_id = GLib.timeout_add(
                    _BANNER_HIDE_GRACE_MS, self._hide_now)
            except Exception:
                self._hide_id = None

        def _hide_now(self) -> bool:
            self._hide_id = None
            try:
                self.set_visible(False)
            except Exception:
                pass
            return False

        def _on_timeout(self) -> bool:
            self._timer_id = None
            self.hide_banner()
            return GLib.SOURCE_REMOVE

        def _nope_allows(self, desktop_entry: str) -> bool:
            nope = getattr(self, '_nope', None)
            if nope is None or not desktop_entry:
                return True
            try:
                return not nope.is_blocked(desktop_entry)
            except Exception:
                return True

        def _on_tap(self) -> None:
            nope_label = None
            try:
                result = dispatch_tap(
                    self._state, self._daemon, self._launch_fn,
                    allow_fn=self._nope_allows)
                if result == 'blocked':
                    current = self._state.current or {}
                    nope_label = current.get('app_name') or current.get(
                        'desktop_entry') or 'App'
            finally:
                self.hide_banner()
            if nope_label is not None:
                self.show_notification(
                    999906, 'Nope-Mode', 'Nope.',
                    f'Nope. {nope_label} is blocked.', '', [], None)

        def _on_swipe(self, _gesture, _vx: float, vy: float) -> None:
            if vy < _SWIPE_UP_VY:
                self.hide_banner()

        def _on_drag_end(
            self, _gesture, _dx: float, dy: float,
        ) -> None:
            if dy < -_DRAG_UP_PX:
                self.hide_banner()

else:

    class BannerWindow:
        """Headless stand-in: same show/tap/hide/theme API, no window."""

        def __init__(
            self,
            config: ShellConfig | None = None,
            notif_daemon=None,
            launch_fn=None,
            nope_state=None,
        ) -> None:
            self._config = config if config is not None else ShellConfig()
            self._daemon = notif_daemon
            self._launch_fn = launch_fn or _launch_desktop_entry
            self._nope = nope_state
            self._state = BannerState()
            self.visible = False
            self.last_duration_ms: int | None = None

        def set_application(self, app) -> None:
            return None

        def apply_theme(self, preset=None) -> None:
            return None

        def show_notification(
            self,
            notif_id: int,
            app_name: str,
            summary: str,
            body: str,
            desktop_entry: str = '',
            actions=None,
            hints=None,
        ) -> None:
            self._state.show(
                notif_id, app_name, summary, body, desktop_entry,
                actions, hints)
            self.last_duration_ms = banner_duration_ms(body)
            self.visible = True

        def hide_banner(self) -> None:
            self._state.clear()
            self.visible = False

        def _nope_allows(self, desktop_entry: str) -> bool:
            nope = getattr(self, '_nope', None)
            if nope is None or not desktop_entry:
                return True
            try:
                return not nope.is_blocked(desktop_entry)
            except Exception:
                return True

        def _on_tap(self) -> str:
            nope_label = None
            try:
                result = dispatch_tap(
                    self._state, self._daemon, self._launch_fn,
                    allow_fn=self._nope_allows)
                if result == 'blocked':
                    current = self._state.current or {}
                    nope_label = current.get('app_name') or current.get(
                        'desktop_entry') or 'App'
            finally:
                self.hide_banner()
            if nope_label is not None:
                self.show_notification(
                    999906, 'Nope-Mode', 'Nope.',
                    f'Nope. {nope_label} is blocked.', '', [], None)
                return 'blocked'
            return result
