from __future__ import annotations

import gi
import html
import re
import subprocess
import threading
from collections.abc import Callable
from datetime import datetime

_LAYER_SHELL = False
try:
    gi.require_version('Gtk4LayerShell', '1.0')
    _LAYER_SHELL = True
except ValueError:
    pass

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gdk, Gio, GLib, Gtk

if _LAYER_SHELL:
    from gi.repository import Gtk4LayerShell as LayerShell

from quick_actions import QuickActionsPanel
from media_controls import MediaCard
from privacy_monitor import detail_text, format_indicator
from config import DANGER_RED, ShellConfig, ThemePreset, same_app_id


def notifs_for_app(notifications: object, app_id: str) -> list[int]:
    """Ids of live notifications owned by an app id: the desktop-entry
    hint first, then the sender name, both `.desktop`- and
    case-insensitive like the mute check in main.py. Pure."""
    if not app_id:
        return []
    ids: list[int] = []
    for notif in notifications or ():
        try:
            entry = getattr(notif, 'desktop_entry', '') or ''
            name = getattr(notif, 'app_name', '') or ''
            nid = int(getattr(notif, 'id'))
        except (AttributeError, TypeError, ValueError):
            continue
        if same_app_id(entry, app_id) or same_app_id(name, app_id):
            ids.append(nid)
    return ids

_NOTIF_IFACE = 'org.freedesktop.Notifications'
_NOTIF_PATH = '/org/freedesktop/Notifications'


def _session_bus() -> Gio.DBusConnection | None:
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return None


def _external_daemon_owns_notifications(
    own_unique_name: str | None = None,
) -> bool:
    """True when another process already owns org.freedesktop.Notifications.

    Notify is a method call, never a broadcast: with mako/dunst owning the
    name our in-process capture can never fire, so the shade must say so
    instead of silently showing nothing. The owner's unique name is compared
    against our own connection's unique name, so our own daemon acquiring
    the name never reads as "external".
    """
    bus = _session_bus()
    if bus is None:
        return False
    try:
        owner = bus.call_sync(
            'org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'GetNameOwner',
            GLib.Variant('(s)', (_NOTIF_IFACE,)),
            GLib.VariantType('(s)'),
            Gio.DBusCallFlags.NONE, 800, None,
        ).unpack()[0]
    except Exception:
        return False
    if not owner:
        return False
    if own_unique_name is None:
        try:
            own_unique_name = bus.get_unique_name()
        except Exception:
            own_unique_name = None
    if own_unique_name and owner == own_unique_name:
        return False
    return True


def _spawn_shade_thread(target: Callable[[], None]):
    """Run target off the GTK main thread; never raises."""
    try:
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        return thread
    except Exception:
        try:
            target()
        except Exception:
            pass
        return None


def _post_to_main(callback: Callable, *args):
    """Deliver callback results to the GTK main thread.

    Falls back to a direct call when GLib.idle_add is unavailable, which
    only happens under the headless test seams.
    """
    idle_add = getattr(GLib, 'idle_add', None)
    if idle_add is None:
        return callback(*args)
    try:
        idle_add(callback, *args)
    except Exception:
        pass


# Shell-internal notification ids never came from the daemon (e.g. the
# update notice, screenshot, screen-recording and wireless-debugging rows),
# so dismissing them must not echo-close over D-Bus.
_SHELL_INTERNAL_IDS = frozenset({999901, 999902, 999903, 999904, 999905})

_ALLOWED_BODY_TAGS = ('b', 'i', 'u')
_ANCHOR_RE = re.compile(r'&lt;a\b.*?&gt;(.*?)&lt;/a&gt;',
                       re.DOTALL | re.IGNORECASE)


def notif_body_to_pango_markup(body: str) -> str:
    """Render the advertised body-markup subset as safe Pango markup.

    Everything untrusted is escaped first; only <b>/<i>/<u> pairs are
    re-allowed and links keep their text with the target dropped. An
    unbalanced tag is left escaped so Pango never sees malformed markup.
    """
    escape = getattr(GLib, 'markup_escape_text', None)
    if callable(escape):
        try:
            text = escape(body)
        except Exception:
            text = html.escape(body, quote=True)
    else:
        text = html.escape(body, quote=True)
    for tag in _ALLOWED_BODY_TAGS:
        text = text.replace(f'&lt;{tag}&gt;', f'<{tag}>')
        text = text.replace(f'&lt;/{tag}&gt;', f'</{tag}>')
        if text.count(f'<{tag}>') != text.count(f'</{tag}>'):
            text = text.replace(f'<{tag}>', f'&lt;{tag}&gt;')
            text = text.replace(f'</{tag}>', f'&lt;/{tag}&gt;')
    return _ANCHOR_RE.sub(r'\1', text)


def _normalize_notif_actions(
    actions: list | None,
) -> list[tuple[str, str, object | None]]:
    """D-Bus (key, label) pairs and legacy (label, callback) pairs both land
    as (key, label, callback|None); D-Bus rows invoke over the daemon."""
    normalized: list[tuple[str, str, object | None]] = []
    for item in actions or []:
        try:
            parts = tuple(item)
        except TypeError:
            continue
        if len(parts) == 3:
            key, label, callback = parts
            normalized.append(
                (str(key), str(label), callback if callable(callback) else None))
        elif len(parts) == 2:
            first, second = parts
            if callable(second):
                normalized.append((str(first), str(first), second))
            else:
                normalized.append((str(first), str(second), None))
    return normalized


def theme_css(preset: ThemePreset) -> str:
    """Shade chrome. Colors follow the brand ramp: the preset foreground
    at 3.5% (raised), 10% (line), 25% (shade), 40%, 80% (strong) and 90%
    (text) over the preset background; muted text is the preset muted;
    emphasis inverts (foreground block, background text)."""
    bg = preset.background
    fg = preset.foreground
    return f"""
window.xx-wm-shade {{
    background: transparent;
}}
window.shade-window {{
    background: alpha({bg}, 0.55);
}}
.shade-root {{
    background: {bg};
    color: alpha({fg}, 0.90);
    padding: 22px 16px 14px 16px;
}}
.shade-scrim {{
    background: alpha({bg}, 0.85);
}}
button.shade-datetime,
button.shade-datetime:hover,
button.shade-datetime:active {{
    padding: 0;
    min-height: 0;
    border-radius: 0;
    background: none;
    box-shadow: none;
}}
.shade-time {{
    font-size: 46px;
    font-weight: 400;
    letter-spacing: -0.02em;
    color: alpha({fg}, 0.90);
}}
.shade-date {{
    font-size: 11px;
    letter-spacing: 0.16em;
    color: {preset.muted};
}}
button.shade-icon-btn {{
    min-width: 38px;
    min-height: 38px;
    padding: 0;
    border-radius: 12px;
    background: none;
    box-shadow: none;
    border: 1px solid alpha({fg}, 0.10);
    color: alpha({fg}, 0.80);
    -gtk-icon-size: 18px;
}}
button.shade-icon-btn:hover {{
    background: {preset.surface};
}}
button.shade-icon-btn:active {{
    background: {preset.surface_alt};
}}
button.shade-power:hover {{
    color: {DANGER_RED};
}}
button.shade-privacy {{
    font-size: 11px;
    min-height: 40px;
    padding: 0 8px;
    background: none;
    box-shadow: none;
    color: {preset.accent};
}}
.shade-privacy-detail {{
    font-size: 11px;
    color: {preset.muted};
    margin-top: 12px;
}}
.shade-section {{
    min-height: 28px;
    margin-top: 22px;
    margin-bottom: 10px;
}}
.shade-root .xx-section-label {{
    margin: 0;
    font-size: 10px;
    letter-spacing: 0.18em;
    color: {preset.muted};
}}
button.shade-chevron {{
    min-width: 32px;
    min-height: 28px;
    padding: 0;
    border-radius: 8px;
    background: none;
    box-shadow: none;
    border: none;
    color: {preset.muted};
    -gtk-icon-size: 16px;
}}
button.shade-chevron:hover {{
    background: {preset.surface};
}}
.shade-notif-count {{
    font-size: 10px;
    font-feature-settings: "tnum";
    color: alpha({fg}, 0.40);
}}
button.shade-text-btn {{
    font-size: 10px;
    letter-spacing: 0.16em;
    min-height: 28px;
    padding: 0;
    background: none;
    box-shadow: none;
    color: alpha({fg}, 0.80);
}}
button.shade-text-btn:hover {{
    color: alpha({fg}, 0.90);
}}
scrolledwindow.shade-notif-card {{
    background: alpha({fg}, 0.035);
    border: 1px solid alpha({fg}, 0.10);
    border-radius: 16px;
}}
list.shade-notif-list {{
    background: none;
}}
list.shade-notif-list > row {{
    padding: 0;
    background: none;
}}
.notif-row {{
    padding: 0 16px;
}}
.notif-row:hover {{
    background: alpha({fg}, 0.03);
}}
.notif-content {{
    padding: 14px 0;
    border-bottom: 1px solid alpha({fg}, 0.10);
}}
list.shade-notif-list > row:last-child .notif-content {{
    border-bottom: none;
}}
.notif-app {{
    font-size: 10px;
    letter-spacing: 0.14em;
    color: {preset.muted};
}}
.shade-hint {{
    font-size: 12px;
    letter-spacing: 0.02em;
    color: {preset.muted};
}}
.notif-time {{
    font-size: 10px;
    letter-spacing: 0.06em;
    color: {preset.muted};
}}
.notif-title {{
    font-size: 15px;
    font-weight: 400;
    color: alpha({fg}, 0.90);
}}
.notif-body {{
    font-size: 13px;
    font-weight: 300;
    color: alpha({fg}, 0.80);
}}
.shade-handle {{
    min-width: 40px;
    min-height: 4px;
    margin-top: 18px;
    border-radius: 2px;
    background: alpha({fg}, 0.25);
}}
.cal-header {{
    font-size: 11px;
    letter-spacing: 0.14em;
    color: {preset.muted};
}}
.cal-day {{
    font-size: 13px;
    color: alpha({fg}, 0.80);
    min-width: 34px;
    min-height: 30px;
}}
.cal-day.cal-today {{
    color: {bg};
    background: {fg};
    border-radius: 15px;
    font-weight: 500;
}}
.cal-weekday {{
    font-size: 10px;
    letter-spacing: 0.1em;
    color: {preset.muted};
    min-width: 34px;
}}
"""


def _no_ellipsis(label) -> None:
    """Section captions sit at exactly their natural width; a fractional
    rounding would otherwise swap the last letter for an ellipsis."""
    try:
        from gi.repository import Pango
        label.set_ellipsize(Pango.EllipsizeMode.NONE)
    except Exception:
        pass


def format_shade_date(now: datetime, battery: int | None = None) -> str:
    """Header date line: ``SAT 17 OCT`` plus `` · 44%`` when a battery
    level is known (missing data is hidden, never guessed). Pure."""
    text = now.strftime('%a %d %b').replace(' 0', ' ').upper()
    if battery is None:
        return text
    return f'{text} \u00b7 {int(battery)}%'


def format_rel_time(timestamp: datetime, now: datetime | None = None) -> str:
    """Compact relative age for a notification row ("now", "5m", "2h")."""
    now = now if now is not None else datetime.now()
    try:
        seconds = max(0, int((now - timestamp).total_seconds()))
    except Exception:
        return 'now'
    if seconds < 60:
        return 'now'
    if seconds < 3600:
        return f'{seconds // 60}m'
    if seconds < 86400:
        return f'{seconds // 3600}h'
    return f'{seconds // 86400}d'


def group_notification_runs(notifs: list) -> list[list]:
    """Group consecutive notifications from the same app so the group shares
    one app label; a repeat after another app starts a new group."""
    groups: list[list] = []
    for notif in notifs:
        if groups and groups[-1][0].app_name == notif.app_name:
            groups[-1].append(notif)
        else:
            groups.append([notif])
    return groups


_SWIPE_DISMISS_THRESHOLD = 140  # pixels to trigger dismiss
_SHEET_CLOSE_DY = 100  # px upward drag to close the shade


class Notification:
    __slots__ = ('id', 'app_name', 'summary', 'body', 'desktop_entry',
                 'timestamp', 'actions', 'hints')

    def __init__(
        self,
        notif_id: int,
        app_name: str,
        summary: str,
        body: str,
        desktop_entry: str = '',
        actions: list | None = None,
        hints: dict | None = None,
    ) -> None:
        self.id = notif_id
        self.app_name = app_name
        self.summary = summary
        self.body = body
        self.desktop_entry = desktop_entry
        self.timestamp = datetime.now()
        self.actions = _normalize_notif_actions(actions)
        self.hints = dict(hints) if isinstance(hints, dict) else {}


class NotificationShade(Gtk.Window):
    def __init__(self, dnd_state: object | None = None,
                 focus_state: object | None = None,
                 nope_state: object | None = None,
                 on_nope_changed=None,
                 on_open_settings: object | None = None,
                 on_power: object | None = None,
                 hud: object | None = None,
                 config: ShellConfig | None = None,
                 notif_daemon: object | None = None,
                 is_own_daemon: Callable[[], bool] | None = None,
                 media_watcher: object | None = None) -> None:
        super().__init__(title='PiercingXX Shade')
        self.add_css_class('shade-window')
        self._dnd = dnd_state
        self._focus = focus_state
        self._nope = nope_state
        self._on_open_settings = on_open_settings
        self._on_power = on_power
        self._hud = hud
        # The shell window threads its LIVE config so hot reloads reach this
        # surface; the fallback keeps direct no-arg construction working.
        self._config = config if config is not None else ShellConfig()
        # The app passes its NotificationDaemon (or a predicate) so the
        # shade can tell our own name ownership apart from an external
        # daemon's, and report user dismissals back to sending apps.
        self._notif_daemon = notif_daemon
        self._is_own_daemon = is_own_daemon
        # Now-playing card (MPRIS): hides itself while no player exists.
        self.media_card = MediaCard(
            watcher=media_watcher, config=self._config)
        self._clock_timer_id: int | None = None
        self._hide_timer_id: int | None = None
        self._worker_threads: list = []
        self._cal_year_month: tuple[int, int] | None = None

        self.add_css_class('xx-wm-shade')
        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            # OVERLAY, not TOP: a 4-edge TOP surface sat under the
            # maximized xdg app on this phoc, so tap-outside never drew.
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
            LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
            LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
            LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, True)
            LayerShell.set_exclusive_zone(self, 0)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        else:
            self.set_default_size(420, 500)

        self._notifications: list[Notification] = []
        self._privacy_snapshot: dict[str, set[str]] = {}

        self._theme_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2,
        )
        self.apply_theme()

        self.list_box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list_box.add_css_class('text-list')
        # The embedded panel shares this shade's LIVE config so its tiles and
        # sliders resolve the same preset (theme == 'custom' included).
        # Long-presses on its tiles open Settings via the shade callback.
        self.quick_actions = QuickActionsPanel(
            dnd_state=dnd_state, focus_state=focus_state,
            nope_state=nope_state, on_nope_changed=on_nope_changed,
            hud=hud,
            config=self._config,
            on_open_settings=self._open_settings_section,
            on_open_uri=self._open_uri)

        self._revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=250,
            reveal_child=False,
        )
        self._revealer.set_child(self._build_content())
        self.set_child(self._build_outer())

        self._subscribe_dbus()
        self._check_external_notif_daemon()

    def _build_outer(self) -> Gtk.Widget:
        """Full-screen surface: the panel slides down from the top while the
        scrim below it dims the rest of the screen. Tapping the scrim or
        swiping up anywhere closes the shade."""
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.append(self._revealer)
        self._scrim = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._scrim.add_css_class('shade-scrim')
        self._scrim.set_hexpand(True)
        self._scrim.set_vexpand(True)
        scrim_tap = Gtk.GestureClick.new()
        scrim_tap.connect(
            'pressed',
            lambda _g, _n, _x, _y: self.hide_shade(),
        )
        self._scrim.add_controller(scrim_tap)
        outer.append(self._scrim)
        swipe = Gtk.GestureSwipe.new()
        swipe.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        swipe.connect('swipe', self._on_swipe)
        outer.add_controller(swipe)
        sheet_drag = Gtk.GestureDrag.new()
        sheet_drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        sheet_drag.connect('drag-update', self._on_sheet_drag_update)
        sheet_drag.connect('drag-end', self._on_sheet_drag_end)
        outer.add_controller(sheet_drag)
        key = Gtk.EventControllerKey.new()
        key.connect('key-pressed', self._on_key)
        # Key events from focused children bubble to the outer box, so the
        # controller lives here instead of on the window: bare-construction
        # test doubles exercise _build_outer without a Gtk.Window base.
        outer.add_controller(key)
        return outer

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        """(Re)load this shade's display-level sheet. The shell window passes
        the freshly resolved preset on hot-reload fan-out; standalone falls
        back to the injected config's own preset. Forwards into the
        embedded quick-actions panel so tiles restyle on the same reload."""
        resolved = preset if preset is not None else self._config.theme
        data = theme_css(resolved)
        self._theme_provider.load_from_data(data.encode('utf-8'))
        panel = getattr(self, 'quick_actions', None)
        if panel is not None:
            try:
                panel.apply_theme(resolved)
            except Exception:
                pass
        card = getattr(self, 'media_card', None)
        if card is not None:
            try:
                card.apply_theme(resolved)
            except Exception:
                pass

    def _build_content(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class('shade-root')

        # Header: Space Mono clock over a caps date line on the left (tap
        # opens the inline month calendar); Settings + Power as 40 px
        # outlined glyph buttons on the right.
        self._datetime_btn = Gtk.Button()
        self._datetime_btn.add_css_class('flat')
        self._datetime_btn.add_css_class('shade-datetime')
        datetime_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._time_label = Gtk.Label(xalign=0)
        self._time_label.add_css_class('shade-time')
        # Brand display face (Space Mono) via font_theme's global provider,
        # so the clock still follows a user-picked family.
        self._time_label.add_css_class('xx-display-face')
        self._date_label = Gtk.Label(xalign=0)
        self._date_label.add_css_class('shade-date')
        # Older harnesses drive _datetime_label directly; it is the time label.
        self._datetime_label = self._time_label
        datetime_box.append(self._time_label)
        datetime_box.append(self._date_label)
        self._datetime_btn.set_child(datetime_box)
        self._datetime_btn.connect('clicked', lambda _b: self._toggle_calendar())

        settings_btn = Gtk.Button(icon_name='xx-shade-settings-symbolic')
        settings_btn.add_css_class('flat')
        settings_btn.add_css_class('shade-icon-btn')
        settings_btn.set_tooltip_text('Settings')
        settings_btn.connect('clicked', self._on_settings_clicked)

        self._privacy_btn = Gtk.Button(label='')
        self._privacy_btn.add_css_class('flat')
        self._privacy_btn.add_css_class('shade-privacy')
        self._privacy_btn.set_visible(False)
        self._privacy_btn.set_tooltip_text('Privacy indicators')
        self._privacy_btn.connect('clicked', self._toggle_privacy_detail)

        # Power button, top-right — suspend / restart / power off via PowerMenu
        power_btn = Gtk.Button(icon_name='xx-shade-power-symbolic')
        power_btn.add_css_class('flat')
        power_btn.add_css_class('shade-icon-btn')
        power_btn.add_css_class('shade-power')
        power_btn.set_tooltip_text('Power')
        power_btn.connect('clicked', self._on_power_clicked)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        actions.set_valign(Gtk.Align.START)
        actions.set_margin_top(4)
        actions.append(self._privacy_btn)
        actions.append(settings_btn)
        actions.append(power_btn)

        top_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._datetime_btn.set_hexpand(True)
        self._datetime_btn.set_halign(Gtk.Align.START)
        self._datetime_btn.set_valign(Gtk.Align.START)
        top_header.append(self._datetime_btn)
        top_header.append(actions)

        self._privacy_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=180,
            reveal_child=False,
        )
        self._privacy_detail = Gtk.Label(xalign=0, wrap=True,
                                         max_width_chars=36)
        self._privacy_detail.add_css_class('shade-privacy-detail')
        self._privacy_revealer.set_child(self._privacy_detail)
        self._refresh_privacy_indicator()

        self._calendar_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
            transition_duration=180,
            reveal_child=False,
        )
        self._calendar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._calendar_box.set_margin_top(16)
        self._calendar_revealer.set_child(self._calendar_box)
        self._refresh_datetime()

        from xx_ui import widgets
        qa_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        qa_header.add_css_class('shade-section')
        qa_label = widgets.section_label('Quick settings')
        qa_label.set_hexpand(True)
        qa_label.set_valign(Gtk.Align.CENTER)
        _no_ellipsis(qa_label)

        self._expand_btn = Gtk.Button(icon_name='xx-shade-expand-symbolic')
        self._expand_btn.add_css_class('flat')
        self._expand_btn.add_css_class('shade-chevron')
        self._expand_btn.set_tooltip_text('Show all tiles')
        self._expand_btn.connect('clicked', self._toggle_expand)

        qa_header.append(qa_label)
        qa_header.append(self._expand_btn)

        notif_label = widgets.section_label('Notifications')
        notif_label.set_valign(Gtk.Align.CENTER)
        _no_ellipsis(notif_label)
        self._notif_count = Gtk.Label(label='', xalign=0)
        self._notif_count.add_css_class('shade-notif-count')
        self._notif_count.set_valign(Gtk.Align.CENTER)
        self._notif_count.set_visible(False)
        notif_title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        notif_title.set_hexpand(True)
        notif_title.append(notif_label)
        notif_title.append(self._notif_count)

        clear_btn = Gtk.Button(label='CLEAR ALL')
        clear_btn.add_css_class('flat')
        clear_btn.add_css_class('shade-text-btn')
        clear_btn.set_tooltip_text('Clear all')
        clear_btn.connect('clicked', lambda _b: self.clear_all())

        notif_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        notif_header.add_css_class('shade-section')
        notif_header.append(notif_title)
        notif_header.append(clear_btn)

        # Honest empty state when an external daemon owns the notification
        # name: one muted line reusing .notif-app instead of a silent void.
        self._external_hint = Gtk.Label(
            label='Notifications handled by external daemon', xalign=0,
            wrap=True, max_width_chars=32)
        self._external_hint.add_css_class('notif-app')
        self._external_hint.add_css_class('shade-hint')
        self._external_hint.set_margin_bottom(10)
        self._external_hint.set_visible(False)

        # The list is one raised card (on the scroller, so its frame stays
        # whole while rows scroll): rows split by inset hairlines.
        self.list_box.add_css_class('shade-notif-list')
        # The card hugs its rows (up to 360 px, then scrolls); the scrim
        # below keeps the rest of the screen, so tap-outside still closes.
        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=False)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_max_content_height(360)
        scroller.set_propagate_natural_height(True)
        scroller.add_css_class('shade-notif-card')
        scroller.set_child(self.list_box)

        # No empty state: with nothing to show, the whole section (header,
        # CLEAR ALL, card) is hidden; the owner wants a minimal shade.
        self._notif_section = (notif_header, scroller)
        for widget in self._notif_section:
            hide = getattr(widget, 'set_visible', None)
            if callable(hide):
                hide(False)

        # Grab handle: the sheet's bottom edge (swipe up closes).
        handle = Gtk.Box()
        handle.add_css_class('shade-handle')
        handle.set_halign(Gtk.Align.CENTER)

        root.append(top_header)
        root.append(self._privacy_revealer)
        root.append(self._calendar_revealer)
        # Now-playing card: directly under the header, above Quick
        # settings. Hidden entirely while no MPRIS player exists. The
        # getattr keeps bare test harnesses (built without __init__)
        # working; a real shade always carries the card.
        media_card = getattr(self, 'media_card', None)
        if media_card is not None:
            try:
                media_card.set_margin_top(22)
            except Exception:
                pass
            root.append(media_card)
        root.append(qa_header)
        root.append(self.quick_actions)
        root.append(notif_header)
        root.append(self._external_hint)
        root.append(scroller)
        root.append(handle)
        return root

    def _toggle_expand(self, _btn: Gtk.Button) -> None:
        expanded = not self.quick_actions.tier2_grid.get_visible()
        self.quick_actions.expand(expanded)
        self._expand_btn.set_icon_name(
            'xx-shade-collapse-symbolic' if expanded
            else 'xx-shade-expand-symbolic')
        self._expand_btn.set_tooltip_text(
            'Show fewer tiles' if expanded else 'Show all tiles')

    def _refresh_datetime(self) -> bool:
        from clock_format import format_clock, resolve_24h
        now = datetime.now()
        # Same 12/24 h rule as home and the lock screen (was always 24 h)
        time_part = format_clock(now, resolve_24h(getattr(self, '_config', None)))
        try:
            from system_status import battery_pct
            battery = battery_pct()
        except Exception:
            battery = None
        time_label = getattr(self, '_datetime_label', None)
        if time_label is not None:
            time_label.set_text(time_part)
        date_label = getattr(self, '_date_label', None)
        if date_label is not None:
            date_label.set_text(format_shade_date(now, battery))
        return True

    def set_privacy_snapshot(self, snapshot: dict | None) -> None:
        """Feed the privacy monitor snapshot; the header dot-line shows
        only while something is in use."""
        try:
            self._privacy_snapshot = {
                kind: set(apps)
                for kind, apps in dict(snapshot or {}).items()}
        except Exception:
            self._privacy_snapshot = {}
        self._refresh_privacy_indicator()

    def _refresh_privacy_indicator(self) -> None:
        btn = getattr(self, '_privacy_btn', None)
        if btn is None:
            return
        try:
            text = format_indicator(
                getattr(self, '_privacy_snapshot', None) or {})
        except Exception:
            text = ''
        try:
            btn.set_label(text)
            btn.set_visible(bool(text))
            if not text:
                self._privacy_revealer.set_reveal_child(False)
            else:
                self._privacy_detail.set_text(
                    detail_text(getattr(self, '_privacy_snapshot', None)
                                or {}))
        except Exception:
            pass

    def _toggle_privacy_detail(self, _btn: Gtk.Button) -> None:
        """Tap the indicator: reveal which apps hold each kind."""
        try:
            snapshot = getattr(self, '_privacy_snapshot', None) or {}
            if not format_indicator(snapshot):
                return
            self._privacy_detail.set_text(detail_text(snapshot))
            self._privacy_revealer.set_reveal_child(
                not self._privacy_revealer.get_reveal_child())
        except Exception:
            pass

    def _on_settings_clicked(self, _btn: Gtk.Button) -> None:
        self._open_settings_section(None)

    def _open_settings_section(self, section: str | None = None) -> None:
        """Open the Settings page, ideally at the long-pressed tile's section.

        The shell's handler takes an optional section argument; a legacy
        zero-arg handler still works via the TypeError fallback.
        """
        # Hide immediately so the BOTTOM launcher (hopped to TOP) is not
        # covered by this TOP shade for the revealer timeout.
        try:
            if self._clock_timer_id is not None:
                GLib.source_remove(self._clock_timer_id)
                self._clock_timer_id = None
            self._revealer.set_reveal_child(False)
            if _LAYER_SHELL and LayerShell.is_supported():
                LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
            self.hide()
        except Exception:
            pass
        self.hide_shade()
        callback = self._on_open_settings
        if not callable(callback):
            return
        try:
            callback(section)
        except TypeError:
            try:
                callback()
            except Exception:
                pass

    def _open_uri(self, uri: str) -> None:
        """Open a link from a tile (Tailscale sign-in) in the default
        browser, with the shade out of the way."""
        self.hide_shade()
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except Exception as exc:
            from shell_log import get_logger
            get_logger('notification_shade').warning(
                'could not open link from tile: %s', exc)

    def _on_power_clicked(self, _btn: Gtk.Button) -> None:
        self.hide_shade()
        if callable(self._on_power):
            self._on_power()

    def _toggle_calendar(self) -> None:
        showing = self._calendar_revealer.get_reveal_child()
        if showing:
            self._calendar_revealer.set_reveal_child(False)
            return
        now = datetime.now()
        self._show_month(now.year, now.month)
        self._calendar_revealer.set_reveal_child(True)

    def _show_month(self, year: int, month: int) -> None:
        from calendar_grid import WEEKDAY_HEADERS, add_months, month_grid, month_title
        self._cal_year_month = (year, month)

        child = self._calendar_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._calendar_box.remove(child)
            child = nxt

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        prev_btn = Gtk.Button(label='‹')
        prev_btn.add_css_class('flat')
        prev_btn.add_css_class('cal-header')
        prev_btn.connect('clicked', lambda _b: self._show_month(*add_months(year, month, -1)))
        title = Gtk.Label(label=month_title(year, month))
        title.add_css_class('cal-header')
        title.set_hexpand(True)
        next_btn = Gtk.Button(label='›')
        next_btn.add_css_class('flat')
        next_btn.add_css_class('cal-header')
        next_btn.connect('clicked', lambda _b: self._show_month(*add_months(year, month, +1)))
        header.append(prev_btn)
        header.append(title)
        header.append(next_btn)
        self._calendar_box.append(header)

        grid = Gtk.Grid(column_homogeneous=True, row_spacing=2)
        for col, name in enumerate(WEEKDAY_HEADERS):
            lbl = Gtk.Label(label=name)
            lbl.add_css_class('cal-weekday')
            grid.attach(lbl, col, 0, 1, 1)

        today = datetime.now()
        for row_idx, week in enumerate(month_grid(year, month), start=1):
            for col, day in enumerate(week):
                lbl = Gtk.Label(label=str(day) if day else '')
                lbl.add_css_class('cal-day')
                if (day and year == today.year and month == today.month
                        and day == today.day):
                    lbl.add_css_class('cal-today')
                grid.attach(lbl, col, row_idx, 1, 1)
        self._calendar_box.append(grid)

    def _subscribe_dbus(self) -> None:
        # Only NotificationClosed is a real broadcast: per the freedesktop
        # spec Notify is a METHOD call aimed at whichever daemon owns
        # org.freedesktop.Notifications, so it is never seen as a signal.
        bus = _session_bus()
        if bus is None:
            return
        try:
            bus.signal_subscribe(
                None, _NOTIF_IFACE, 'NotificationClosed', _NOTIF_PATH,
                None, Gio.DBusSignalFlags.NONE, self._on_dbus_closed, None,
            )
        except GLib.Error:
            pass

    def _on_dbus_closed(self, _c, _s, _p, _i, _sig, params, _ud) -> None:
        # Reason 1 (expired) keeps its history row: the daemon reports back
        # to the app but the shade retains the notification.
        try:
            unpacked = params.unpack()
            if len(unpacked) > 1 and int(unpacked[1]) == 1:
                return
            self.dismiss(int(unpacked[0]), notify_daemon=False)
        except Exception:
            pass

    def _spawn_shade_worker(self, target: Callable[[], None]):
        thread = _spawn_shade_thread(target)
        workers = getattr(self, '_worker_threads', None)
        if workers is None:
            self._worker_threads = workers = []
        if thread is not None:
            workers.append(thread)
        return thread

    def _check_external_notif_daemon(self) -> None:
        # Our own daemon is authoritative and answers instantly; only the
        # undecided case pays for a bus probe, off the main thread.
        daemon = getattr(self, '_notif_daemon', None)
        if daemon is not None:
            try:
                if daemon.owns_name():
                    self._external_hint.set_visible(False)
                    return
            except Exception:
                pass
        predicate = getattr(self, '_is_own_daemon', None)
        if predicate is not None:
            try:
                if predicate():
                    self._external_hint.set_visible(False)
                    return
            except Exception:
                pass
        try:
            self._spawn_shade_worker(self._probe_external_daemon_in_worker)
        except Exception:
            pass

    def _probe_external_daemon_in_worker(self) -> None:
        try:
            daemon = getattr(self, '_notif_daemon', None)
            own: str | None = None
            if daemon is not None:
                try:
                    own = daemon.unique_name()
                except Exception:
                    own = None
            if own is not None:
                external = _external_daemon_owns_notifications(own)
            else:
                external = _external_daemon_owns_notifications()
        except Exception:
            external = False
        _post_to_main(self._finish_external_probe, external)

    def _finish_external_probe(self, external: bool) -> bool:
        try:
            self._external_hint.set_visible(bool(external))
        except Exception:
            return False
        if external:
            try:
                from shell_log import get_logger
                get_logger('notification_shade').info(
                    'org.freedesktop.Notifications owned by an external '
                    'daemon — shade cannot capture notifications')
            except Exception:
                pass
        return False

    def add_notification(
        self,
        notif_id: int,
        app_name: str,
        summary: str,
        body: str,
        desktop_entry: str = '',
        actions: list | None = None,
        hints: dict | None = None,
    ) -> None:
        self.dismiss(notif_id, notify_daemon=False)
        notif = Notification(notif_id, app_name, summary, body, desktop_entry,
                             actions, hints)
        self._notifications.insert(0, notif)
        self._rebuild_list()

    def dismiss(self, notif_id: int, notify_daemon: bool = True) -> None:
        self._notifications = [n for n in self._notifications if n.id != notif_id]
        self._rebuild_list()
        if notify_daemon:
            self._notify_daemon_of_dismissal(notif_id)

    def close_app_notifications(self, app_id: str) -> list[int]:
        """Drop every live row owned by an app id (the Disable-for… mute
        path). Rows vanish silently here; the daemon close with reason 3
        stays the caller's job so each sender hears exactly one signal."""
        ids = notifs_for_app(self._notifications, app_id)
        for notif_id in ids:
            self.dismiss(notif_id, notify_daemon=False)
        return ids

    def _notify_daemon_of_dismissal(self, notif_id: int) -> None:
        """Report a user dismissal back to the sending app (reason 2).

        Daemon-driven removals and shell-internal rows never echo here.
        """
        if notif_id in _SHELL_INTERNAL_IDS:
            return
        daemon = getattr(self, '_notif_daemon', None)
        if daemon is None:
            return
        try:
            daemon.close_from_shell(notif_id)
        except Exception:
            pass

    def _rebuild_list(self) -> None:
        child = self.list_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.list_box.remove(child)
            child = nxt
        total = len(self._notifications)
        for widget in getattr(self, '_notif_section', ()):
            try:
                widget.set_visible(total > 0)
            except Exception:
                pass
        count = getattr(self, '_notif_count', None)
        if count is not None:
            try:
                count.set_text(f'{total:02d}')
                count.set_visible(total > 0)
            except Exception:
                pass
        for group in group_notification_runs(self._notifications):
            first = True
            for notif in group:
                self.list_box.append(
                    self._make_row(notif, show_app_header=first))
                first = False

    def _make_row(self, notif: Notification,
                  show_app_header: bool = True) -> Gtk.ListBoxRow:
        title_label = Gtk.Label(label=notif.summary, xalign=0, wrap=True, max_width_chars=36)
        title_label.add_css_class('notif-title')

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        content.add_css_class('notif-content')
        content.set_hexpand(True)
        # First row of each same-app run carries the shared app label plus
        # the relative age; the rows grouped under it show summary/body only.
        if show_app_header:
            header = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            header.set_hexpand(True)
            app_label = Gtk.Label(label=notif.app_name.upper(), xalign=0)
            app_label.add_css_class('notif-app')
            app_label.set_hexpand(True)
            time_label = Gtk.Label(
                label=format_rel_time(notif.timestamp), xalign=1)
            time_label.add_css_class('notif-time')
            header.append(app_label)
            header.append(time_label)
            content.append(header)
        content.append(title_label)

        if notif.body.strip():
            body_label = Gtk.Label(xalign=0, wrap=True, max_width_chars=36)
            body_label.set_markup(notif_body_to_pango_markup(notif.body))
            body_label.add_css_class('notif-body')
            content.append(body_label)

        # Up to 3 non-default actions as text buttons; the "default" action
        # (if any) fires on row tap instead of being rendered.
        tappable = [a for a in notif.actions if a[0] != 'default'][:3]
        if tappable:
            action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            action_row.set_margin_top(6)
            for key, label, callback in tappable:
                btn = Gtk.Button(label=label)
                btn.add_css_class('flat')
                btn.add_css_class('action-link')
                if callback is not None:
                    btn.connect(
                        'clicked',
                        lambda _b, cb=callback, nid=notif.id:
                        self._run_action(cb, nid),
                    )
                else:
                    btn.connect(
                        'clicked',
                        lambda _b, k=key, n=notif:
                        self._invoke_notif_action(n, k),
                    )
                action_row.append(btn)
            content.append(action_row)

        # A row dismisses with a sideways swipe; resident rows (e.g. the
        # wireless-debugging indicator) are persistent and keep no drag, so
        # the user cannot swipe away state the shell must keep showing.
        resident = bool((notif.hints or {}).get('resident', False))

        row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row_box.add_css_class('notif-row')
        row_box.append(content)

        # Tap invokes the "default" action when the app offered one, and only
        # otherwise launches the desktop entry.
        has_default = any(a[0] == 'default' for a in notif.actions)
        if notif.desktop_entry or has_default:
            tap = Gtk.GestureClick(n_points=1)
            tap.connect(
                'released',
                lambda _g, _n, _x, _y, n=notif: self._on_notif_tapped(n),
            )
            content.add_controller(tap)

        # Swipe to dismiss (resident rows keep showing, so no drag)
        if not resident:
            drag = Gtk.GestureDrag()
            drag.connect('drag-update', lambda _g, dx, _dy, nid=notif.id, rb=row_box:
                         self._on_notif_drag(rb, dx, nid))
            drag.connect('drag-end', lambda _g, dx, _dy, nid=notif.id, rb=row_box:
                         self._on_notif_drag_end(rb, dx, nid))
            row_box.add_controller(drag)

        row = Gtk.ListBoxRow(selectable=False, activatable=False)
        row.set_child(row_box)
        return row

    def _run_action(self, callback: object, notif_id: int) -> None:
        self.dismiss(notif_id)
        self.hide_shade()
        if callable(callback):
            callback()

    def _nope_blocks_notif(self, notif: Notification) -> bool:
        """Nope gate for shade row taps: a blocked launch shows a brief
        Nope banner instead of opening the app."""
        nope = getattr(self, '_nope', None)
        if nope is None:
            return False
        try:
            blocked = bool(nope.is_blocked(notif.desktop_entry or ''))
        except Exception:
            return False
        if not blocked:
            return False
        try:
            self.hide_shade()
        except Exception:
            pass
        try:
            text = nope.notice_text(
                notif.app_name or notif.desktop_entry or 'App')
        except Exception:
            text = 'Nope.'
        self._show_nope_banner(text)
        return True

    def _show_nope_banner(self, text: str) -> None:
        try:
            banner = self.get_application()._ensure_banner()
        except Exception:
            return
        try:
            banner.show_notification(
                999906, 'Nope-Mode', 'Nope.', text, '', [], None)
        except Exception:
            pass

    def _on_notif_tapped(self, notif: Notification) -> None:
        if self._nope_blocks_notif(notif):
            return
        default = next((a for a in notif.actions if a[0] == 'default'), None)
        if default is not None:
            self._invoke_notif_action(notif, default[0])
            return
        self._launch_notif_app(notif.desktop_entry, notif.id)

    def _invoke_notif_action(self, notif: Notification, action_key: str) -> None:
        """Fire an app action over the daemon, then close (reason 2).

        Resident rows stay: the spec keeps them after invocation.
        """
        if self._nope_blocks_notif(notif):
            return
        daemon = getattr(self, '_notif_daemon', None)
        if daemon is not None:
            try:
                daemon.invoke_action(notif.id, action_key)
            except Exception:
                pass
        if bool((notif.hints or {}).get('resident', False)):
            self.hide_shade()
            return
        self.dismiss(notif.id, notify_daemon=False)
        if daemon is not None:
            try:
                daemon.close_from_shell(notif.id)
            except Exception:
                pass
        self.hide_shade()

    def _launch_notif_app(self, desktop_entry: str, notif_id: int) -> None:
        try:
            app_info = Gio.DesktopAppInfo.new(f'{desktop_entry}.desktop')
            if app_info is not None:
                app_info.launch([], None)
                self.dismiss(notif_id)
                self.hide_shade()
                return
        except (GLib.Error, Exception):
            pass
        # Fallback: gtk-launch
        try:
            subprocess.Popen(['gtk-launch', desktop_entry], close_fds=True)
            self.dismiss(notif_id)
            self.hide_shade()
        except FileNotFoundError:
            pass

    def _on_notif_drag(self, row_box: Gtk.Box, dx: float, _notif_id: int) -> None:
        # Visually offset the row during drag
        row_box.set_margin_start(max(0, int(abs(dx))) if dx > 0 else 0)
        row_box.set_margin_end(max(0, int(abs(dx))) if dx < 0 else 0)

    def _on_notif_drag_end(self, row_box: Gtk.Box, dx: float, notif_id: int) -> None:
        if abs(dx) >= _SWIPE_DISMISS_THRESHOLD:
            self.dismiss(notif_id)
        else:
            row_box.set_margin_start(0)
            row_box.set_margin_end(0)

    def _on_sheet_drag_update(self, gesture: Gtk.GestureDrag, dx: float, dy: float) -> None:
        # Claim only an upward pull so horizontal notif-row swipes still fire.
        if dy < -40 and abs(dy) > abs(dx):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_sheet_drag_end(self, _g: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if dy < -_SHEET_CLOSE_DY and abs(dy) > abs(dx):
            self.hide_shade()

    def _on_swipe(self, _g: Gtk.GestureSwipe, vel_x: float, vel_y: float) -> None:
        if vel_y < -200 and abs(vel_y) > abs(vel_x):
            self.hide_shade()

    def _on_key(self, _g: Gtk.EventControllerKey, keyval: int, *_) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.hide_shade()
            return True
        return False

    def show_shade(self) -> None:
        # A show inside the hide grace window wins: drop the pending hide.
        hide_timer = getattr(self, '_hide_timer_id', None)
        if hide_timer is not None:
            try:
                GLib.source_remove(hide_timer)
            except Exception:
                pass
            self._hide_timer_id = None
        self._refresh_datetime()
        if self._clock_timer_id is None:
            self._clock_timer_id = GLib.timeout_add_seconds(10, self._refresh_datetime)
        try:
            self.quick_actions.refresh_states()
        except Exception:
            pass
        qa = getattr(self, 'quick_actions', None)
        if qa is not None:
            # Slider reads spawn brightnessctl/pactl: never on the show path.
            sync_async = getattr(qa, 'sync_sliders_async', None)
            if callable(sync_async):
                sync_async()
            elif hasattr(qa, 'sync_sliders'):
                qa.sync_sliders()
        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.EXCLUSIVE)
        try:
            self.set_visible(True)
        except AttributeError:
            pass
        self.present()
        self._revealer.set_reveal_child(True)

    def hide_shade(self) -> None:
        if self._clock_timer_id is not None:
            GLib.source_remove(self._clock_timer_id)
            self._clock_timer_id = None
        self._calendar_revealer.set_reveal_child(False)
        self._revealer.set_reveal_child(False)
        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        old = getattr(self, '_hide_timer_id', None)
        if old is not None:
            try:
                GLib.source_remove(old)
            except Exception:
                pass
        try:
            self._hide_timer_id = GLib.timeout_add(260, self._hide_now)
        except Exception:
            self._hide_timer_id = None

    def _hide_now(self) -> bool:
        self._hide_timer_id = None
        try:
            self.hide()
        except Exception:
            pass
        return False

    def clear_all(self) -> None:
        # Resident rows survive Clear all: they describe shell state, not
        # an app event the user can clear.
        removed = [n.id for n in self._notifications
                   if not bool((n.hints or {}).get('resident', False))]
        self._notifications = [n for n in self._notifications
                               if n.id not in removed]
        self._rebuild_list()
        for notif_id in removed:
            self._notify_daemon_of_dismissal(notif_id)

    def notifications_snapshot(self) -> list[tuple[str, str]]:
        """(app_name, summary) pairs for the lock screen's text list."""
        return [(n.app_name, n.summary) for n in self._notifications]
