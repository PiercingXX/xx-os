from __future__ import annotations

import threading
from typing import Callable

import gi

try:
    gi.require_version('Gtk4LayerShell', '1.0')
    _HAS_LAYER = True
except ValueError:
    _HAS_LAYER = False

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, Gtk

if _HAS_LAYER:
    from gi.repository import Gtk4LayerShell as LayerShell

from config import DANGER_RED, ShellConfig, ThemePreset
from session_lock import LockHosted
from shell_log import get_logger, log_exc

_log = get_logger('power')

_LOGIN1 = 'org.freedesktop.login1'
_LOGIN1_PATH = '/org/freedesktop/login1'
_LOGIN1_MANAGER = 'org.freedesktop.login1.Manager'

# systemctl does not exist on OpenRC (postmarketOS), so power actions go
# through logind's Manager interface — which elogind implements too.
_ACTIONS = {
    'suspend': 'Suspend',
    'reboot': 'Reboot',
    'poweroff': 'PowerOff',
}

# Only the destructive actions arm on first tap; lock/suspend/cancel fire
# at once. The arming text is shown inline on the row itself.
CONFIRM_TIMEOUT_S = 3
_CONFIRM_LABELS = {
    'reboot': 'Tap again to restart',
    'poweroff': 'Tap again to power off',
}


def confirm_label(action: str) -> str:
    """Inline arming text for a destructive power action."""
    return _CONFIRM_LABELS.get(action, 'Tap again?')


class ConfirmState:
    """Pure two-tap confirm machine: first tap arms, second confirms.

    Widget-free so headless tests drive the exact state machine the menu
    runs; the menu only adds the 3 s expiry around it.
    """

    def __init__(self) -> None:
        self.pending: str | None = None

    def tap(self, action: str) -> bool:
        """Record a tap on `action`; True means confirmed — execute now."""
        if self.pending == action:
            self.pending = None
            return True
        self.pending = action
        return False

    def reset(self) -> None:
        self.pending = None


def suspend_visible(can_suspend: str | None, call_live: bool = False) -> bool:
    """Whether the Suspend row shows for a login1 CanSuspend value.

    CanSuspend is a Manager method, not a property, answering 'yes'/'no'/
    'challenge'/'na'. The row stays visible until the probe answers (None);
    'yes' and 'challenge' (auth-gated, still offered) keep it — 'no', 'na'
    and failures hide it. A live call hides it whatever the probe says:
    suspending would drop the call.
    """
    if call_live:
        return False
    if can_suspend is None:
        return True
    return can_suspend in ('yes', 'challenge')


def visible_power_rows(can_suspend: str | None,
                       call_live: bool = False) -> list[str]:
    """Row keys in display order for the current CanSuspend probe value.

    Lock, Restart, Power off and Cancel always show; Suspend joins them
    only while the probe is unanswered (None) or 'yes'/'challenge', and
    never during a live call. Pure, so headless tests pin the exact rows
    the menu renders at 320x640.
    """
    rows = ['lock']
    if suspend_visible(can_suspend, call_live):
        rows.append('suspend')
    rows.extend(('reboot', 'poweroff', 'cancel'))
    return rows


def _menu_call_live(menu) -> bool:
    """The menu's injected live-call probe; False when none is wired or
    it fails (the probe must never break the menu)."""
    probe = getattr(menu, '_call_live', None)
    if not callable(probe):
        return False
    try:
        return bool(probe())
    except Exception:
        return False


def query_can_suspend_async(on_done: Callable[[str | None], None],
                            bus=None) -> None:
    """Ask login1 for CanSuspend without blocking the GTK thread.

    CanSuspend is a method on the Manager (not a Properties.Get key):
    it takes no arguments and returns one string out-arg
    ('yes'/'no'/'challenge'/'na'). `on_done(value)` fires on the main
    loop with that string, or None when the call cannot be made. Never
    raises.
    """
    from gi.repository import Gio, GLib

    def _fail(reason: str) -> bool:
        _log.warning('CanSuspend probe failed: %s', reason)
        on_done(None)
        return False

    if bus is None:
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        except Exception as error:
            GLib.idle_add(_fail, str(error) or 'no system bus')
            return

    def _reply(conn, result) -> None:
        try:
            # Gio replies are tuples of out-args; CanSuspend returns (s,).
            value = conn.call_finish(result).unpack()[0]
        except Exception as error:
            _fail(getattr(error, 'message', None) or str(error)
                  or 'CanSuspend call failed')
            return
        on_done(value if isinstance(value, str) else None)

    try:
        bus.call(
            _LOGIN1, _LOGIN1_PATH, _LOGIN1_MANAGER, 'CanSuspend',
            None,
            None, Gio.DBusCallFlags.NONE, 5000, None, _reply,
        )
    except Exception as error:
        GLib.idle_add(_fail, str(error) or 'CanSuspend call failed')


def power_action_sync(action: str, bus=None) -> tuple[bool, str]:
    """Run one power action via login1. Returns (ok, message)."""
    method = _ACTIONS.get(action)
    if method is None:
        return False, f'Unknown power action: {action}'
    try:
        from gi.repository import Gio, GLib
        conn = bus if bus is not None else Gio.bus_get_sync(
            Gio.BusType.SYSTEM, None)
        conn.call_sync(
            _LOGIN1, _LOGIN1_PATH, _LOGIN1_MANAGER, method,
            GLib.Variant('(b)', (False,)),
            None, Gio.DBusCallFlags.NONE, 30000, None,
        )
        return True, ''
    except Exception as error:
        message = getattr(error, 'message', None) or str(error)
        _log.warning('power %s failed: %s', action, message)
        return False, message or f'{action} failed'


def power_action_async(action: str,
                       on_done: Callable[[bool, str], None] | None = None,
                       bus=None) -> None:
    """Run one power action off the GTK thread.

    `on_done(ok, message)` fires exactly once on the main loop. Never raises.
    """

    def _worker() -> None:
        from gi.repository import GLib
        ok, message = power_action_sync(action, bus=bus)

        def _deliver() -> bool:
            if on_done is not None:
                on_done(ok, message)
            return False
        GLib.idle_add(_deliver)

    threading.Thread(target=_worker, daemon=True).start()


def theme_css(preset: ThemePreset) -> str:
    return f"""
window.power-menu-window {{
    background: alpha({preset.background}, 0.85);
}}
.power-menu-scrim {{
    background: alpha({preset.background}, 0.85);
}}
.power-menu-row {{
    font-size: 16pt;
    font-weight: 400;
    color: {preset.foreground};
    background: transparent;
    border: none;
    border-radius: 0;
    min-height: 64px;
}}
.power-menu-row:hover {{
    background: alpha({preset.surface_alt}, 0.6);
}}
.power-menu-row.armed {{
    color: {preset.accent};
    background: alpha({preset.surface}, 0.6);
}}
.power-menu-row.danger {{
    color: {DANGER_RED};
}}
.power-menu-cancel {{
    font-size: 15pt;
    color: {preset.muted};
    min-height: 56px;
}}
"""


class PowerMenu(LockHosted, Gtk.Window):
    """
    Full-screen scrim with a text-first action list — Lock, Suspend,
    Restart, Power off, Cancel — triggered by long-pressing the power
    button (≥600ms) or the shade power entry. Restart and Power off arm on
    the first tap and execute on the second within 3 s.
    """

    def __init__(self, config: ShellConfig | None = None,
                 on_lock: Callable[[], None] | None = None,
                 call_live: Callable[[], bool] | None = None) -> None:
        super().__init__()
        self.set_decorated(False)
        self.set_resizable(False)
        self.add_css_class('power-menu-window')

        if _HAS_LAYER and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
            LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, True)
            LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
            LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
            LayerShell.set_exclusive_zone(self, -1)
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.EXCLUSIVE)
        else:
            self.set_default_size(420, 860)
            self.fullscreen()

        # The shell window threads its LIVE config so hot reloads reach this
        # surface; the fallback keeps direct no-arg construction working.
        self._config = config if config is not None else ShellConfig()
        self._on_lock = on_lock
        # Live-call probe: Suspend hides (and refuses) while it reads True.
        self._call_live = call_live
        self._can_suspend: str | None = None
        self._confirm = ConfirmState()
        self._confirm_timeout: int | None = None
        self._theme_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 5,
        )
        self.apply_theme()

        self.set_child(self._build())

        # Suspend shows until the login1 probe answers (anything but
        # yes/challenge hides it); the probe never blocks the menu.
        query_can_suspend_async(self._on_can_suspend)

        # Tap outside the list → cancel
        click = Gtk.GestureClick.new()
        click.connect('released', self._on_scrim_tap)
        self.get_child().add_controller(click)

        # Escape key → cancel
        key = Gtk.EventControllerKey.new()
        key.connect('key-pressed', self._on_key)
        self.add_controller(key)

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        """(Re)load this surface's display-level sheet. The shell window
        passes the freshly resolved preset on hot-reload fan-out; standalone
        falls back to the injected config's own preset."""
        data = theme_css(preset if preset is not None else self._display_preset())
        self._theme_provider.load_from_data(data.encode('utf-8'))

    def _display_preset(self) -> ThemePreset:
        """custom and appearance_mode resolve as on every surface; the
        application-owned menu is themed from here before any fan-out."""
        import sys
        shell = sys.modules.get('window')
        resolver = getattr(shell, 'resolve_appearance_preset', None)
        if callable(resolver):
            try:
                return resolver(self._config)
            except Exception:
                pass
        return self._config.theme

    def _build(self) -> Gtk.Widget:
        scrim = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scrim.add_css_class('power-menu-scrim')
        scrim.set_hexpand(True)
        scrim.set_vexpand(True)
        scrim.set_halign(Gtk.Align.FILL)
        scrim.set_valign(Gtk.Align.FILL)

        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        rows.set_hexpand(True)

        self._action_rows: dict[str, tuple[Gtk.Button, str]] = {}

        def _row(label: str, action: str | None,
                 on_clicked, extra_class: str | None = None) -> Gtk.Button:
            btn = Gtk.Button(label=label)
            btn.add_css_class('power-menu-row')
            if extra_class is not None:
                btn.add_css_class(extra_class)
            btn.set_hexpand(True)
            btn.connect('clicked', on_clicked)
            if action is not None:
                self._action_rows[action] = (btn, label)
            return btn

        self._lock_row = _row('Lock', None, lambda _: self._lock())
        self._suspend_row = _row(
            'Suspend', None, lambda _: self._execute('suspend'))
        self._restart_row = _row(
            'Restart', 'reboot', lambda _: self._on_danger_tap('reboot'))
        self._poweroff_row = _row(
            'Power off', 'poweroff',
            lambda _: self._on_danger_tap('poweroff'), 'danger')
        self._cancel_row = _row(
            'Cancel', None, lambda _: self._dismiss(), 'power-menu-cancel')

        rows.append(self._lock_row)
        rows.append(self._suspend_row)
        rows.append(self._restart_row)
        rows.append(self._poweroff_row)
        rows.append(self._cancel_row)

        # The list fills the width with 24 px margins, capped at ~420 px and
        # centered on wider screens — no min-width (it measured 374 px total
        # on a 360 px screen).
        clamp = Adw.Clamp(maximum_size=420)
        clamp.set_child(rows)
        clamp.set_hexpand(True)
        clamp.set_margin_start(24)
        clamp.set_margin_end(24)

        # The scrolled middle claims its content height (every row shows
        # on a tall screen) and shrinks into an internal scroll on a
        # short one. Without propagate_natural_height the window only
        # ever shows the first row.
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_propagate_natural_height(True)
        scroll.set_child(clamp)
        scroll.set_hexpand(True)
        scroll.set_vexpand(False)
        scroll.set_valign(Gtk.Align.CENTER)

        top_gap = Gtk.Box()
        top_gap.set_vexpand(True)
        bottom_gap = Gtk.Box()
        bottom_gap.set_vexpand(True)

        scrim.append(top_gap)
        scrim.append(scroll)
        scrim.append(bottom_gap)
        self._scroll = scroll
        return scrim

    def follow_dock(self, monitor) -> None:
        """Docked: the menu opens on the monitor (dock_controller). A
        non-resizable layer surface keeps its natural size on river's
        monitor instead of filling it, so there it is resizable."""
        if _HAS_LAYER and LayerShell.is_supported():
            self.set_resizable(monitor is not None)
            LayerShell.set_monitor(self, monitor)

    def show_menu(self) -> None:
        # A call may have started or ended since the last open.
        self._on_can_suspend(getattr(self, '_can_suspend', None))
        try:
            import dock_controller
            dock_controller.sync_surface(self)
        except Exception:
            log_exc('power_menu.show_menu: dock_controller.sync_surface')
        self.set_visible(True)
        self.present()

    def _dismiss(self) -> None:
        self._clear_confirm_timeout()
        self._confirm.reset()
        self._restore_rows()
        self.set_visible(False)

    def _lock(self) -> None:
        self._dismiss()
        # Explicit "Lock now": Smart Lock must demand the PIN at the next
        # lock even inside trust, until a successful PIN unlock clears it.
        try:
            from smart_lock import note_explicit_lock
            note_explicit_lock()
        except Exception:
            log_exc('power_menu._lock: note_explicit_lock')
        # Locking ends Caffeine (keep-screen-on is for the session at hand).
        try:
            from idle_inhibit import get_hub
            get_hub().clear_caffeine('lock')
        except Exception:
            log_exc('power_menu._lock: get_hub().clear_caffeine')
        if self._on_lock is not None:
            try:
                self._on_lock()
            except Exception as error:
                _log.warning('power lock failed: %s', error)

    def _on_danger_tap(self, action: str) -> None:
        if self._confirm.tap(action):
            self._clear_confirm_timeout()
            self._restore_rows()
            self._execute(action)
            return
        self._show_armed(action)
        self._restart_confirm_timeout(action)

    def _show_armed(self, action: str) -> None:
        for name, (btn, label) in self._action_rows.items():
            if name == action:
                btn.set_label(confirm_label(name))
                btn.add_css_class('armed')
            else:
                btn.set_label(label)
                btn.remove_css_class('armed')

    def _restore_rows(self) -> None:
        for btn, label in self._action_rows.values():
            btn.set_label(label)
            btn.remove_css_class('armed')

    def _restart_confirm_timeout(self, action: str) -> None:
        from gi.repository import GLib
        self._clear_confirm_timeout()
        self._confirm_timeout = GLib.timeout_add_seconds(
            CONFIRM_TIMEOUT_S, self._on_confirm_expired, action)

    def _clear_confirm_timeout(self) -> None:
        if self._confirm_timeout is not None:
            from gi.repository import GLib
            GLib.source_remove(self._confirm_timeout)
            self._confirm_timeout = None

    def _on_confirm_expired(self, action: str) -> bool:
        self._confirm_timeout = None
        if self._confirm.pending == action:
            self._confirm.reset()
            self._restore_rows()
        return False

    def _on_can_suspend(self, can_suspend: str | None) -> None:
        self._can_suspend = can_suspend
        self._suspend_row.set_visible(
            'suspend' in visible_power_rows(can_suspend,
                                            _menu_call_live(self)))

    def _execute(self, cmd: str) -> None:
        self._dismiss()
        if cmd == 'suspend' and _menu_call_live(self):
            # The menu was opened before the call arrived: still refuse.
            _log.info('suspend refused: call in progress')
            return
        power_action_async(cmd, on_done=self._on_power_result)

    def _on_power_result(self, ok: bool, message: str) -> None:
        # No stock dialog over the shell: the phone simply stays on, and
        # the reason goes to the log.
        if not ok:
            _log.warning('power action failed: %s', message or 'no detail')

    def _on_scrim_tap(self, gesture: Gtk.GestureClick, _n: int, x: float, y: float) -> None:
        # Dismiss if the tap lands outside the list: the scrolled middle is
        # a direct child of the scrim, so its allocation and the gesture
        # coordinates share the same frame.
        scroll = getattr(self, '_scroll', None)
        if scroll is None:
            return
        alloc = scroll.get_allocation()
        if not (alloc.x <= x <= alloc.x + alloc.width
                and alloc.y <= y <= alloc.y + alloc.height):
            self._dismiss()

    def _on_key(self, _ctrl: Gtk.EventControllerKey, keyval: int, _code: int, _state: object) -> bool:
        if keyval == Gdk.KEY_Escape:
            self._dismiss()
            return True
        return False
