from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import datetime

import gi

_LAYER_SHELL = False
try:
    gi.require_version('Gtk4LayerShell', '1.0')
    _LAYER_SHELL = True
except ValueError:
    pass

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, GLib, Gtk, Pango

if _LAYER_SHELL:
    from gi.repository import Gtk4LayerShell as LayerShell

from config import DANGER_RED, WARNING_ORANGE, ShellConfig, ThemePreset
from lock_lines import _FAIL_THRESHOLD, _lockout_secs, lock_screen_lines
from media_controls import MediaCard

import smart_lock


def theme_css(preset: ThemePreset) -> str:
    return f"""
.lock-root {{
    background: {preset.background};
    color: {preset.foreground};
}}
.lock-clock {{
    font-size: 64pt;
    font-weight: 300;
    font-variant-numeric: tabular-nums;
    color: {preset.foreground};
}}
.lock-clock.compact {{
    font-size: 40pt;
}}
.lock-date {{
    font-size: 16pt;
    color: {preset.muted};
}}
.lock-dots {{
    font-size: 22pt;
    letter-spacing: 0.4em;
    font-family: monospace;
    color: {preset.foreground};
    min-height: 48px;
}}
.lock-error {{
    font-size: 12pt;
    color: {DANGER_RED};
}}
.lock-lockout {{
    font-size: 13pt;
    color: {WARNING_ORANGE};
    min-height: 24px;
}}
.lock-key {{
    font-size: 22pt;
    font-weight: 400;
    min-width: 72px;
    min-height: 64px;
    border-radius: 16px;
    background: {preset.surface};
    color: {preset.foreground};
    border: none;
    padding: 0;
}}
.lock-key:hover {{ background: {preset.surface_alt}; }}
.lock-key:disabled {{ opacity: 0.25; }}
.lock-key.del {{
    font-size: 18pt;
    background: transparent;
    color: {preset.muted};
}}
.lock-key.del:hover {{ background: {preset.surface_alt}; }}
.lock-sub {{
    font-size: 8pt;
    color: {preset.muted};
    margin-top: -2px;
}}
.lock-unlock-btn {{
    font-size: 14pt;
    min-height: 60px;
    min-width: 200px;
    border-radius: 30px;
    background: transparent;
    color: {preset.foreground};
    border: none;
    box-shadow: none;
}}
.lock-unlock-btn:hover {{ background: alpha({preset.foreground}, 0.08); }}
.lock-unlock-btn:disabled {{ opacity: 0.25; }}
.lock-fp-hint {{
    font-size: 11pt;
    color: {preset.muted};
    margin-top: 8px;
}}
.lock-hint {{
    font-size: 11pt;
    color: {preset.muted};
    letter-spacing: 0.08em;
}}
.lock-notif {{
    font-size: 11pt;
    color: {preset.muted};
    background: transparent;
    border: none;
    padding: 4px 12px;
}}
.lock-emergency-btn {{
    font-size: 13pt;
    color: {preset.foreground};
    background: transparent;
    border: none;
    box-shadow: none;
    min-height: 44px;
}}
.lock-emergency-btn:hover {{ background: alpha({preset.foreground}, 0.08); }}
.emg-display {{
    font-size: 24pt;
    font-weight: 300;
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.05em;
    color: {preset.foreground};
    min-height: 56px;
}}
.emg-status {{
    font-size: 11pt;
    color: {preset.muted};
    min-height: 20px;
}}
.emg-contact {{
    font-size: 13pt;
    color: {preset.foreground};
    background: transparent;
    border: none;
    padding: 10px 12px;
}}
.emg-contact:hover {{ background: alpha({preset.foreground}, 0.08); }}
.emg-action {{
    font-size: 14pt;
    min-height: 56px;
    min-width: 140px;
    border-radius: 28px;
    background: {preset.surface};
    color: {preset.foreground};
    border: none;
}}
.emg-action:hover {{ background: {preset.surface_alt}; }}
.emg-medical {{
    font-size: 12pt;
    color: {preset.foreground};
}}
@keyframes shake {{
    0%   {{ margin-left: 0; }}
    20%  {{ margin-left: -14px; }}
    40%  {{ margin-left: 14px; }}
    60%  {{ margin-left: -8px; }}
    80%  {{ margin-left: 8px; }}
    100% {{ margin-left: 0; }}
}}
.shake {{ animation: shake 0.35s ease; }}
"""

_theme_provider: Gtk.CssProvider | None = None


def _apply_lock_theme(theme: ThemePreset) -> None:
    """Display-level lock-screen sheet: register the provider once and reload
    its data per construction. LockScreen can be built repeatedly, and a new
    display-level provider per build would accumulate without bound — same
    add-once discipline as font_theme.apply_global_font."""
    global _theme_provider
    display = Gdk.Display.get_default()
    if display is None:
        return
    if _theme_provider is None:
        _theme_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            display,
            _theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2,
        )
    _theme_provider.load_from_data(theme_css(theme).encode('utf-8'))


_KEYPAD: list[tuple[str, str]] = [
    ('1', ''),    ('2', 'ABC'), ('3', 'DEF'),
    ('4', 'GHI'), ('5', 'JKL'), ('6', 'MNO'),
    ('7', 'PQRS'),('8', 'TUV'), ('9', 'WXYZ'),
    ('',  ''),    ('0', ''),    ('←', ''),
]

_MAX_PIN = 64
_SWIPE_UNLOCK_VELOCITY = -300

_KEYPAD_SPACING = 8
_KEY_SIZE_MIN = 56
_KEY_SIZE_MAX = 96
_KEY_SIZE_MIN_WIDTH_ONLY = 64


_KEYPAD_SIDE_MARGINS = 48

# Vertical chrome around the 4 key rows once the keypad is revealed: PIN
# dots, error/lockout lines, Unlock button + margins, pin-box gaps and the
# fingerprint hint. The height bound keeps dots + keypad + Unlock inside the
# space below the collapsed clock on a 320x640 phone.
_PIN_VERTICAL_CHROME = 226
_PIN_ROW_GAPS = 3 * _KEYPAD_SPACING

# Window height kept for the collapsed clock block + margins when sizing
# the revealed keypad from the window height.
_COLLAPSED_CLOCK_RESERVE = 120
_KEYPAD_HEIGHT_MARGIN = 16


def key_size_for_width(width: int) -> int:
    """Key width for the 3-column PIN keypad in `width` px of space: three
    keys plus two gutters must fit (3 × 110 px keys never fit a 320 px
    phone), clamped to 64..96 px. Pure: headless tests pin the table."""
    try:
        w = int(width)
    except (TypeError, ValueError):
        return _KEY_SIZE_MIN_WIDTH_ONLY
    return max(_KEY_SIZE_MIN_WIDTH_ONLY,
               min(_KEY_SIZE_MAX, (w - 2 * _KEYPAD_SPACING) // 3))


def keypad_key_size(width: int, height: int | None) -> int:
    """Key size from min(width-based, height-based), clamped to 56..96 px.

    `width` is the horizontal space for the keypad (three keys + two
    gutters, same convention as key_size_for_width); `height` is the
    vertical space for dots + 4 key rows + Unlock + spacing, where a key
    is 4/5 as tall as it is wide. A missing/invalid height falls back to
    width-only. Pure: headless tests pin the table."""
    try:
        w = int(width)
    except (TypeError, ValueError):
        return _KEY_SIZE_MIN
    size_w = (w - 2 * _KEYPAD_SPACING) // 3
    try:
        h = int(height)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        h = -1
    if h <= 0:
        return max(_KEY_SIZE_MIN, min(_KEY_SIZE_MAX, size_w))
    size_h = ((h - _PIN_VERTICAL_CHROME - _PIN_ROW_GAPS) * 5) // 16
    return max(_KEY_SIZE_MIN, min(_KEY_SIZE_MAX, min(size_w, size_h)))


def format_medical_info(medical: dict) -> str:
    """Read-only Medical ID text for the lock screen: one fact per line,
    empty or bad-typed fields skipped. Pure: headless tests pin the wording."""
    if not isinstance(medical, dict):
        return 'No medical info set'

    def _text(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    lines: list[str] = []
    fields = (
        ('Name', _text(medical.get('name'))),
        ('Born', _text(medical.get('date_of_birth'))),
        ('Blood type', _text(medical.get('blood_type'))),
        ('Allergies', _text(medical.get('allergies'))),
        ('Medications', _text(medical.get('medications'))),
        ('Conditions', _text(medical.get('conditions'))),
    )
    for label, value in fields:
        if value is not None:
            lines.append(f'{label}: {value}')
    donor = medical.get('organ_donor')
    if isinstance(donor, bool):
        lines.append(f'Organ donor: {"Yes" if donor else "No"}')
    notes = _text(medical.get('notes'))
    if notes is not None:
        lines.append(f'Notes: {notes}')
    contacts = medical.get('emergency_contacts')
    if isinstance(contacts, list):
        rows: list[str] = []
        for entry in contacts:
            if not isinstance(entry, dict):
                continue
            name = _text(entry.get('name'))
            number = _text(entry.get('number'))
            relation = _text(entry.get('relation'))
            if name is None and number is None:
                continue
            row = ' — '.join(part for part in (name, number) if part is not None)
            if relation is not None:
                row += f' ({relation})'
            rows.append(row)
        if rows:
            lines.append('Emergency contacts:')
            lines.extend(rows)
    if not lines:
        return 'No medical info set'
    return '\n'.join(lines)


_FP_BUS = 'net.reactivated.Fprint'
_FP_MANAGER_PATH = '/net/reactivated/Fprint/Manager'
_FP_MANAGER_IFACE = 'net.reactivated.Fprint.Manager'
_FP_DEVICE_IFACE = 'net.reactivated.Fprint.Device'
_FP_DBUS_TIMEOUT_MS = 5000


def _fp_probe_sync() -> bool:
    """Worker-thread fingerprint probe: enrolled prints? D-Bus first,
    fprintd-list fallback. Never runs on the GTK thread."""
    enrolled = _fp_dbus_enrolled()
    if enrolled is None:
        enrolled = _fp_list_enrolled()
    return enrolled


def _fp_dbus_enrolled() -> bool | None:
    """Enrolled prints via net.reactivated.Fprint, or None when D-Bus is
    unreachable so the caller falls back to the subprocess probe."""
    try:
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        device = bus.call_sync(
            _FP_BUS, _FP_MANAGER_PATH, _FP_MANAGER_IFACE,
            'GetDefaultDevice', None, None,
            Gio.DBusCallFlags.NONE, _FP_DBUS_TIMEOUT_MS, None,
        )
        device_path = device.unpack()[0]
        if not device_path or device_path == '/':
            return False
        user = os.environ.get('USER', os.environ.get('LOGNAME', 'user'))
        fingers = bus.call_sync(
            _FP_BUS, device_path, _FP_DEVICE_IFACE,
            'ListEnrolledFingers', GLib.Variant('(s)', (user,)), None,
            Gio.DBusCallFlags.NONE, _FP_DBUS_TIMEOUT_MS, None,
        )
        return len(fingers.unpack()[0]) > 0
    except Exception:
        return None


def _fp_list_enrolled() -> bool:
    """fprintd-list fallback: True only with enrolled prints — a merely
    installed fprintd binary must not produce the unlock hint."""
    user = os.environ.get('USER', os.environ.get('LOGNAME', 'user'))
    try:
        r = subprocess.run(['fprintd-list', user],
                           capture_output=True, timeout=10, text=True)
    except (OSError, subprocess.SubprocessError):
        return False
    out = (r.stdout + r.stderr).lower()
    return (r.returncode == 0 and 'finger' in out
            and 'no fingers enrolled' not in out)



def _cutout_inset(config) -> int:
    """Top inset that keeps the camera cutout clear: the status bar's
    per-device height (0 when it cannot be resolved)."""
    try:
        import status_bar
        panel = status_bar.monitor_panel(Gdk.Display.get_default())
        return int(status_bar.resolve_height(config, panel=panel))
    except Exception:
        return 0

class LockScreen(Gtk.Window):
    def __init__(self, on_unlock: Callable[[], None],
                 config: ShellConfig,
                 get_notifications: Callable[[], list[tuple[str, str]]] | None = None,
                 dnd_active_fn: Callable[[], bool] | None = None,
                 on_open_shade: Callable[[], None] | None = None,
                 media_watcher=None) -> None:
        super().__init__(title='PiercingXX')

        if _LAYER_SHELL and LayerShell.is_supported():
            LayerShell.init_for_window(self)
            LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
            for edge in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM,
                         LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
                LayerShell.set_anchor(self, edge, True)
            LayerShell.set_exclusive_zone(self, -1)
            # ON_DEMAND is layer-shell v4+; phoc 0.56 speaks v3 only, where
            # requesting it leaves the surface without key focus. EXCLUSIVE
            # is the v3 mode that receives keys.
            LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.EXCLUSIVE)
        else:
            self.set_default_size(420, 860)
            self.fullscreen()

        self._on_unlock   = on_unlock
        self._get_notifications = get_notifications or (lambda: [])
        self._dnd_active  = dnd_active_fn or (lambda: False)
        self._on_open_shade = on_open_shade or (lambda: None)
        self._open_shade_after_unlock = False
        # Shared with the shell window: _reload_config refreshes that
        # instance's data in place, so lock/unlock decisions below always see
        # the current pin_hash — a private snapshot would enforce a dead PIN.
        self._config      = config
        self._pin         = ''
        self._fail_count  = 0
        self._lockout_src: int | None = None
        self._fp_running  = False
        self._keypad_btns: list[Gtk.Button] = []
        self._key_widgets: list[Gtk.Widget] = []
        self._key_size = 0
        # Lock-screen emergency surface (no unlock): typed digits plus the
        # modem's own emergency list, merged at open time in a worker thread.
        self._emg_digits = ''
        self._modem_emergency_extra: list[str] = []
        self._modem_emg_fetched = False
        self._start_smart_lock()
        # Now-playing card: transport only while locked (locked=True drops
        # the title tap that would raise or launch the player app), hidden
        # while nothing is playing. The watcher arrives late like the
        # shade's, so the card starts detached.
        self._media = MediaCard(watcher=media_watcher, config=config,
                               locked=True)

        _apply_lock_theme(self._display_preset())

        self.set_child(self._build())
        self._refresh_clock()
        self._resume_lockout_state()
        self.refresh_fp_state()
        GLib.timeout_add_seconds(1, self._tick_clock)

    def _display_preset(self) -> ThemePreset:
        """Preset this surface renders with: window.resolve_theme over the
        shared live config, so theme == 'custom' derives its palette instead
        of falling back to the default preset (W1-B). Lazy import: window.py
        sits above this module in the shell stack, and a module-level import
        would drag its GTK requirements into headless contexts that import
        lock_screen for its pure theme_css sheet.
        """
        from window import resolve_theme
        return resolve_theme(self._config)

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        _apply_lock_theme(preset if preset is not None else self._display_preset())

    # ------------------------------------------------------------------
    # Build UI
    # ------------------------------------------------------------------

    def _build(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class('lock-root')

        # Clock top third
        self.clock_label = Gtk.Label(xalign=0.5)
        self.clock_label.add_css_class('lock-clock')
        self.clock_label.set_hexpand(True)

        self.date_label = Gtk.Label(xalign=0.5)
        self.date_label.add_css_class('lock-date')
        self.date_label.set_hexpand(True)

        clock_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        clock_box.set_hexpand(True)
        clock_box.append(self.clock_label)
        clock_box.append(self.date_label)

        top_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top_pane.set_hexpand(True)
        top_pane.set_vexpand(True)
        self._top_pane = top_pane
        sp1 = Gtk.Box()
        sp1.set_vexpand(True)
        sp2 = Gtk.Box()
        sp2.set_vexpand(True)
        top_pane.append(sp1)
        top_pane.append(clock_box)
        top_pane.append(sp2)

        # PIN area
        self.dots_label = Gtk.Label(label='')
        self.dots_label.add_css_class('lock-dots')
        self.dots_label.set_halign(Gtk.Align.CENTER)
        # A long PIN must never force the surface wider than the phone.
        self.dots_label.set_ellipsize(Pango.EllipsizeMode.END)

        self.error_label = Gtk.Label(label='')
        self.error_label.add_css_class('lock-error')
        self.error_label.set_halign(Gtk.Align.CENTER)

        self.lockout_label = Gtk.Label(label='')
        self.lockout_label.add_css_class('lock-lockout')
        self.lockout_label.set_halign(Gtk.Align.CENTER)

        keypad = Gtk.Grid(row_spacing=_KEYPAD_SPACING,
                          column_spacing=_KEYPAD_SPACING)
        keypad.set_halign(Gtk.Align.CENTER)
        keypad.set_margin_top(8)
        self._keypad_grid = keypad

        for idx, (digit, sub) in enumerate(_KEYPAD):
            col, row = idx % 3, idx // 3
            if not digit:
                ph = Gtk.Box()
                self._key_widgets.append(ph)
                keypad.attach(ph, col, row, 1, 1)
            elif digit == '←':
                btn = Gtk.Button(label='←')
                btn.add_css_class('lock-key')
                btn.add_css_class('del')
                btn.connect('clicked', self._on_delete)
                lp = Gtk.GestureLongPress.new()
                lp.connect('pressed', lambda *_: self._clear_pin())
                btn.add_controller(lp)
                keypad.attach(btn, col, row, 1, 1)
                self._keypad_btns.append(btn)
                self._key_widgets.append(btn)
            else:
                btn = self._make_key(digit, sub)
                keypad.attach(btn, col, row, 1, 1)
                self._keypad_btns.append(btn)
                self._key_widgets.append(btn)
        # Keys size from the screen width (key_size_for_width), re-read on
        # every show so 320 px phones and rotated outputs fit. GTK4 has no
        # size-allocate signal; connecting one raises and the lock never builds.
        self._apply_keypad_size(key_size_for_width(360))

        self._unlock_btn = Gtk.Button(label='Unlock')
        self._unlock_btn.add_css_class('lock-unlock-btn')
        self._unlock_btn.set_halign(Gtk.Align.CENTER)
        self._unlock_btn.set_margin_top(16)
        self._unlock_btn.connect('clicked', lambda _b: self._check_pin())
        self._keypad_btns.append(self._unlock_btn)

        self._fp_hint = Gtk.Label(label='')
        self._fp_hint.add_css_class('lock-fp-hint')
        self._fp_hint.set_halign(Gtk.Align.CENTER)
        self._update_fp_hint()

        pin_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        pin_box.set_halign(Gtk.Align.CENTER)
        pin_box.append(self.dots_label)
        pin_box.append(self.error_label)
        pin_box.append(self.lockout_label)
        pin_box.append(keypad)
        pin_box.append(self._unlock_btn)
        pin_box.append(self._fp_hint)

        # Keypad hides behind a swipe-up reveal (design.md "Lock screen")
        self._pin_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            transition_duration=200,
            reveal_child=False,
        )
        self._pin_revealer.set_child(pin_box)

        self._notif_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._notif_box.set_halign(Gtk.Align.CENTER)

        self._swipe_hint = Gtk.Label(label='↑ swipe up to unlock')
        self._swipe_hint.add_css_class('lock-hint')
        self._swipe_hint.set_halign(Gtk.Align.CENTER)
        self._swipe_hint.set_margin_bottom(32)

        # Smart Lock line: 'Kept unlocked by …' under trust, the
        # PIN-required reason otherwise; hidden while Smart Lock is
        # unconfigured (lock_line_for returns ''). A long SSID must not
        # widen the surface past the phone.
        self._trust_label = Gtk.Label(label='')
        self._trust_label.add_css_class('lock-hint')
        self._trust_label.set_halign(Gtk.Align.CENTER)
        self._trust_label.set_ellipsize(Pango.EllipsizeMode.END)

        self._build_emergency_surface()

        mid_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        mid_box.set_hexpand(True)
        self._mid_box = mid_box
        mid_box.append(self._notif_box)
        mid_box.append(self._media)
        mid_box.append(self._pin_revealer)
        mid_box.append(self._emergency_revealer)
        mid_box.append(self._medical_revealer)

        # Overflow (many notifications, revealed keypad, large text scale)
        # scrolls inside the lower pane like the home page's launcher
        # scroll, instead of clipping off a small phone screen.
        mid_scroll = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        mid_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        mid_scroll.set_has_frame(False)
        mid_scroll.set_child(mid_box)

        bot_pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bot_pane.set_hexpand(True)
        bot_pane.set_vexpand(True)
        bot_pane.append(mid_scroll)
        bot_pane.append(self._trust_label)
        bot_pane.append(self._swipe_hint)
        # Emergency stays a bottom text button in every state — resting,
        # keypad revealed, lockout — and is never keypad-managed, so the
        # lockout desensitize pass cannot take it away.
        bot_pane.append(self._emergency_btn)

        # 1/3 clock / 2/3 rest — a homogeneous grid like the home page, so
        # there is no Paned divider handle and no realize-time ratio hack.
        # The clock block centers in the top third. While the keypad is
        # revealed the grid goes non-homogeneous and the clock block
        # collapses, so dots + keypad + Unlock fit a 640 px screen.
        grid = Gtk.Grid()
        grid.set_row_homogeneous(True)
        self._lock_grid = grid
        grid.set_hexpand(True)
        grid.set_vexpand(True)
        grid.attach(top_pane, 0, 0, 1, 1)
        grid.attach(bot_pane, 0, 1, 1, 2)

        # Upward swipe: no PIN → unlock directly; PIN set → reveal the keypad
        swipe = Gtk.GestureSwipe.new()
        swipe.set_touch_only(False)
        swipe.connect('swipe', self._on_swipe)
        root.add_controller(swipe)

        root.append(grid)
        # The status bar hides on the lock screen, so the lock keeps the
        # camera cutout clear itself (FLX1 punch-hole: the clock sat in it).
        root.set_margin_top(_cutout_inset(self._config))
        self._refresh_notifications()
        self._refresh_trust_line()
        return root

    def _on_keypad_alloc(self, _grid: Gtk.Widget, allocation) -> None:
        self._apply_keypad_size(key_size_for_width(allocation.width))

    def _fit_keypad_to_screen(self) -> None:
        apply = getattr(self, '_apply_keypad_size', None)
        if apply is None:
            return
        try:
            width = self.get_width()
            height = self.get_height()
        except Exception:
            return
        if width <= 0 or height <= 0:
            try:
                monitors = Gdk.Display.get_default().get_monitors()
                geo = monitors.get_item(0).get_geometry()
                if width <= 0:
                    width = geo.width
                if height <= 0:
                    height = geo.height
            except Exception:
                pass
        if width <= 0:
            return
        avail_h = (height - _COLLAPSED_CLOCK_RESERVE - _KEYPAD_HEIGHT_MARGIN
                   if height > 0 else None)
        apply(keypad_key_size(width - _KEYPAD_SIDE_MARGINS, avail_h))

    def _apply_keypad_size(self, size: int) -> None:
        if size == self._key_size:
            return
        self._key_size = size
        height = (size * 4) // 5
        for widget in self._key_widgets:
            widget.set_size_request(size, height)

    def _on_swipe(self, _gesture: Gtk.GestureSwipe, _vx: float, vy: float) -> None:
        if vy < _SWIPE_UNLOCK_VELOCITY:
            self._swipe_up()

    def _swipe_up(self) -> None:
        if self._config.pin_hash is None:
            self._unlock()
            return
        # Smart Lock: trust waives the keypad, but the swipe itself is
        # still required — trust alone never unlocks. getattr-tolerant:
        # headless harnesses predating Smart Lock have no gate method and
        # keep the old reveal-the-keypad behavior.
        gate = getattr(self, '_smart_lock_requires_pin', None)
        if gate is not None and not gate():
            self._unlock()
            return
        self._pin_revealer.set_reveal_child(True)
        self._swipe_hint.set_visible(False)
        self._set_clock_compact(True)
        self._fit_keypad_to_screen()

    # ------------------------------------------------------------------
    # Smart Lock
    # ------------------------------------------------------------------

    def _start_smart_lock(self) -> None:
        """Attach the process-wide trust monitor (event-driven BlueZ /
        NetworkManager watch). Never raises: without D-Bus the cache
        reads as untrusted and the PIN is required."""
        try:
            smart_lock.ensure_monitor(
                self._config, on_trust_changed=self._on_smart_lock_changed)
        except Exception:
            pass

    def _on_smart_lock_changed(self) -> None:
        try:
            GLib.idle_add(self._refresh_trust_line)
        except Exception:
            pass

    def _smart_lock_requires_pin(self) -> bool:
        """True unless Smart Lock currently waives the keypad. Fails
        closed: any error demands the PIN. Missing-attribute tolerant
        for headless harnesses driving unbound methods."""
        try:
            fail_count = getattr(self, '_fail_count', 0) or 0
            state = smart_lock.current_state(
                self._config,
                fail_count=fail_count,
                lockout_active=getattr(self, '_lockout_src', None) is not None,
            )
            return smart_lock.evaluate(state)[0]
        except Exception:
            return True

    def _refresh_trust_line(self) -> bool:
        """Render the small muted Smart Lock line for the current state;
        hidden while Smart Lock is unconfigured. One-shot for idle_add."""
        label = getattr(self, '_trust_label', None)
        if label is None:
            return False
        try:
            fail_count = getattr(self, '_fail_count', 0) or 0
            state = smart_lock.current_state(
                self._config,
                fail_count=fail_count,
                lockout_active=getattr(self, '_lockout_src', None) is not None,
            )
            line = smart_lock.lock_line_for(state)
        except Exception:
            line = ''
        try:
            label.set_text(line)
            if hasattr(label, 'set_visible'):
                label.set_visible(bool(line))
        except Exception:
            pass
        return False

    def _set_clock_compact(self, compact: bool) -> None:
        """Collapse the clock block while the keypad is revealed — small
        clock, date and lock notification lines hidden, grid split released
        — so dots + keypad + Unlock fit a 640 px screen. prepare() undoes
        all of it. Missing-widget tolerant for headless harnesses."""
        clock = getattr(self, 'clock_label', None)
        if clock is not None:
            if compact:
                clock.add_css_class('compact')
            else:
                clock.remove_css_class('compact')
        date = getattr(self, 'date_label', None)
        if date is not None:
            date.set_visible(not compact)
        notif = getattr(self, '_notif_box', None)
        if notif is not None and hasattr(notif, 'set_visible'):
            notif.set_visible(not compact)
        grid = getattr(self, '_lock_grid', None)
        if grid is not None:
            grid.set_row_homogeneous(not compact)
        top = getattr(self, '_top_pane', None)
        if top is not None:
            top.set_vexpand(not compact)
        # Revealed keypad sits low, in thumb reach: on a tall screen (FLX1
        # 600x1333) it used to hang from the top with half the screen empty.
        mid = getattr(self, '_mid_box', None)
        align = getattr(Gtk, 'Align', None)
        if mid is not None and align is not None and hasattr(mid, 'set_valign'):
            mid.set_valign(align.END if compact else align.FILL)

    def prepare(self) -> None:
        """Reset to the locked resting state before each present()."""
        self._pin = ''
        self._refresh_dots()
        # Re-evaluate trust at lock time so a stale cache cannot waive a
        # keypad the fresh signals would demand; the line re-renders from
        # the current cache immediately and again as replies land.
        try:
            smart_lock.refresh()
        except Exception:
            pass
        # getattr-tolerant like the swipe gate: pre-Smart-Lock harnesses
        # have no line method and simply skip the re-render.
        refresh_line = getattr(self, '_refresh_trust_line', None)
        if refresh_line is not None:
            refresh_line()
        self._open_shade_after_unlock = False
        self._pin_revealer.set_reveal_child(False)
        self._swipe_hint.set_visible(True)
        self._set_clock_compact(False)
        self._refresh_notifications()
        # The Emergency button is never hidden; the emergency surface
        # itself resets. getattr-tolerant: headless harnesses predate it.
        self._emg_digits = ''
        display = getattr(self, '_emg_display', None)
        if display is not None:
            display.set_text('')
        status = getattr(self, '_emg_status', None)
        if status is not None:
            status.set_text('')
        emg = getattr(self, '_emergency_revealer', None)
        if emg is not None:
            emg.set_reveal_child(False)
        med = getattr(self, '_medical_revealer', None)
        if med is not None:
            med.set_reveal_child(False)
        btn = getattr(self, '_emergency_btn', None)
        if btn is not None:
            btn.set_visible(True)
            btn.set_sensitive(True)
        media = getattr(self, '_media', None)
        if media is not None:
            try:
                media.apply_theme(self._display_preset())
            except Exception:
                pass
            try:
                media.refresh()
            except Exception:
                pass

    def _refresh_notifications(self) -> None:
        child = self._notif_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._notif_box.remove(child)
            child = nxt
        try:
            notifications = self._get_notifications()
        except Exception:
            notifications = []
        lines = lock_screen_lines(
            notifications, self._config.lock_screen_notifications, self._dnd_active())
        for line in lines:
            btn = Gtk.Button(label=line)
            btn.add_css_class('flat')
            btn.add_css_class('lock-notif')
            btn.connect('clicked', lambda _b: self._on_notification_tapped())
            self._notif_box.append(btn)

    def _on_notification_tapped(self) -> None:
        # Prompt unlock; the shade opens once the user gets through
        self._open_shade_after_unlock = True
        self._swipe_up()

    def _make_key(self, digit: str, sub: str) -> Gtk.Button:
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.set_halign(Gtk.Align.CENTER)
        inner.set_valign(Gtk.Align.CENTER)
        inner.append(Gtk.Label(label=digit))
        if sub:
            s = Gtk.Label(label=sub)
            s.add_css_class('lock-sub')
            inner.append(s)

        btn = Gtk.Button()
        btn.add_css_class('lock-key')
        btn.set_child(inner)
        btn.connect('clicked', self._on_digit, digit)
        return btn

    # ------------------------------------------------------------------
    # Emergency surface (no unlock)
    # ------------------------------------------------------------------

    def _build_emergency_surface(self) -> None:
        """Emergency dial pad + configured contact rows + Medical info,
        all usable without unlocking. Lives in the scrollable mid pane
        so it fits a 320x640 phone; the Emergency button itself sits at
        the bottom, outside every revealer and the keypad button list."""
        self._emg_status = Gtk.Label(label='')
        self._emg_status.add_css_class('emg-status')
        self._emg_status.set_halign(Gtk.Align.CENTER)
        self._emg_status.set_wrap(True)

        self._emg_display = Gtk.Label(label='')
        self._emg_display.add_css_class('emg-display')
        self._emg_display.set_halign(Gtk.Align.CENTER)
        self._emg_display.set_ellipsize(Pango.EllipsizeMode.END)

        emg_grid = Gtk.Grid(row_spacing=8, column_spacing=8)
        emg_grid.set_halign(Gtk.Align.CENTER)
        for idx, digit in enumerate('123456789*0#'):
            btn = Gtk.Button(label=digit)
            btn.add_css_class('lock-key')
            btn.connect('clicked', self._on_emg_digit, digit)
            emg_grid.attach(btn, idx % 3, idx // 3, 1, 1)
            # Sized with the PIN keys, never desensitized with them.
            self._key_widgets.append(btn)

        emg_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        emg_actions.set_halign(Gtk.Align.CENTER)
        emg_actions.set_margin_top(8)
        emg_call_btn = Gtk.Button(label='Call')
        emg_call_btn.add_css_class('emg-action')
        emg_call_btn.connect('clicked', self._on_emg_call)
        emg_del_btn = Gtk.Button(label='←')
        emg_del_btn.add_css_class('emg-action')
        emg_del_btn.connect('clicked', self._on_emg_delete)
        emg_actions.append(emg_call_btn)
        emg_actions.append(emg_del_btn)

        self._emg_contacts_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._emg_contacts_box.set_halign(Gtk.Align.CENTER)

        self._emg_medical_btn = Gtk.Button(label='Medical info')
        self._emg_medical_btn.add_css_class('flat')
        self._emg_medical_btn.add_css_class('lock-notif')
        self._emg_medical_btn.connect('clicked', self._on_emg_medical)

        emg_back_btn = Gtk.Button(label='Back')
        emg_back_btn.add_css_class('flat')
        emg_back_btn.add_css_class('lock-notif')
        emg_back_btn.connect('clicked', self._on_emg_back)

        emg_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        emg_box.set_halign(Gtk.Align.CENTER)
        emg_box.append(self._emg_status)
        emg_box.append(self._emg_display)
        emg_box.append(emg_grid)
        emg_box.append(emg_actions)
        emg_box.append(self._emg_contacts_box)
        emg_box.append(self._emg_medical_btn)
        emg_box.append(emg_back_btn)

        self._emergency_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            transition_duration=200,
            reveal_child=False,
        )
        self._emergency_revealer.set_child(emg_box)

        self._medical_label = Gtk.Label(label='')
        self._medical_label.add_css_class('emg-medical')
        self._medical_label.set_halign(Gtk.Align.START)
        self._medical_label.set_xalign(0)
        self._medical_label.set_wrap(True)
        self._medical_label.set_selectable(True)

        med_back_btn = Gtk.Button(label='Back')
        med_back_btn.add_css_class('flat')
        med_back_btn.add_css_class('lock-notif')
        med_back_btn.connect('clicked', self._on_medical_back)

        med_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        med_box.set_halign(Gtk.Align.CENTER)
        med_box.append(self._medical_label)
        med_box.append(med_back_btn)

        self._medical_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
            transition_duration=200,
            reveal_child=False,
        )
        self._medical_revealer.set_child(med_box)

        self._emergency_btn = Gtk.Button(label='Emergency')
        self._emergency_btn.add_css_class('lock-emergency-btn')
        self._emergency_btn.set_halign(Gtk.Align.CENTER)
        self._emergency_btn.connect('clicked', self._on_emergency_btn)

    def _on_emergency_btn(self, _btn: Gtk.Button) -> None:
        self.open_emergency()

    def open_emergency(self) -> None:
        """Reveal the lock-screen emergency surface: emergency-only dial
        pad, configured emergency-contact rows, and Medical info. No
        unlock — ModemManager places emergency calls without one."""
        self._refresh_emg_contacts()
        self._refresh_medical_visibility()
        self._emergency_revealer.set_reveal_child(True)
        self._medical_revealer.set_reveal_child(False)
        if not self._modem_emg_fetched:
            self._modem_emg_fetched = True
            threading.Thread(target=self._modem_emg_thread, daemon=True,
                             name='emg-numbers').start()

    def close_emergency(self) -> None:
        self._emergency_revealer.set_reveal_child(False)
        self._medical_revealer.set_reveal_child(False)

    def _on_emg_back(self, _btn: Gtk.Button) -> None:
        self.close_emergency()

    def _on_emg_digit(self, _btn: Gtk.Button, digit: str) -> None:
        if len(self._emg_digits) < 20:
            self._emg_digits += digit
            self._refresh_emg_display()

    def _on_emg_delete(self, _btn: Gtk.Button) -> None:
        if self._emg_digits:
            self._emg_digits = self._emg_digits[:-1]
            self._refresh_emg_display()

    def _refresh_emg_display(self) -> None:
        self._emg_display.set_text(self._emg_digits)
        self._emg_status.set_text('')

    def _on_emg_call(self, _btn: Gtk.Button) -> None:
        self._emg_call(self._emg_digits)

    def _on_emg_contact(self, _btn: Gtk.Button, number: str) -> None:
        self._emg_call(number, contact=True)

    def _emg_call(self, number: str, contact: bool = False) -> None:
        """Emergency dial: typed digits must match an emergency service
        (built-in union the modem's list); configured emergency-contact
        rows are explicitly allowed from the lock screen. The created
        ModemManager call reaches the in-call UI through the existing
        monitor → render path, which presents above the lock."""
        if not number:
            return
        if not contact:
            from dialer import EMERGENCY_REFUSAL, is_emergency_number
            if not is_emergency_number(number, self._modem_emergency_extra):
                self._emg_status.set_text(EMERGENCY_REFUSAL)
                return
        self._emg_status.set_text(f'Calling {number}…')
        threading.Thread(target=self._emg_call_thread, args=(number, contact),
                         daemon=True, name='emg-call').start()

    def _emg_call_thread(self, number: str, contact: bool) -> None:
        try:
            if contact:
                from modem_monitor import voice_create_and_start
                ok, detail = voice_create_and_start(number)
            else:
                from dialer import place_emergency_call
                ok, detail = place_emergency_call(number, self._modem_emergency_extra)
        except Exception as err:
            ok, detail = False, str(err)
        GLib.idle_add(self._emg_call_done, number, ok, detail)

    def _emg_call_done(self, _number: str, ok: bool, detail: str) -> bool:
        self._emg_status.set_text(
            'Dialing…' if ok else f'Call failed: {str(detail).strip()}')
        return GLib.SOURCE_REMOVE

    def _modem_emg_thread(self) -> None:
        try:
            from dialer import get_modem_emergency_numbers
            extra = get_modem_emergency_numbers()
        except Exception:
            extra = []
        GLib.idle_add(self._modem_emg_done, extra)

    def _modem_emg_done(self, extra: list) -> bool:
        try:
            self._modem_emergency_extra = [str(n) for n in extra]
        except Exception:
            self._modem_emergency_extra = []
        return GLib.SOURCE_REMOVE

    def _refresh_emg_contacts(self) -> None:
        box = self._emg_contacts_box
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt
        try:
            contacts = self._config.medical_id.get('emergency_contacts', [])
        except Exception:
            contacts = []
        if not isinstance(contacts, list):
            return
        for entry in contacts:
            if not isinstance(entry, dict):
                continue
            number = entry.get('number')
            if not isinstance(number, str) or not number:
                continue
            name = entry.get('name')
            label = f'{name} — {number}' if isinstance(name, str) and name else number
            btn = Gtk.Button(label=label)
            btn.add_css_class('emg-contact')
            inner = btn.get_child()
            if inner is not None and hasattr(inner, 'set_ellipsize'):
                inner.set_ellipsize(Pango.EllipsizeMode.END)
            btn.connect('clicked', self._on_emg_contact, number)
            box.append(btn)

    def _refresh_medical_visibility(self) -> None:
        try:
            allowed = bool(self._config.lock_screen_medical_id)
        except Exception:
            allowed = True
        self._emg_medical_btn.set_visible(allowed)

    def _on_emg_medical(self, _btn: Gtk.Button) -> None:
        try:
            medical = self._config.medical_id
        except Exception:
            medical = {}
        self._medical_label.set_text(format_medical_info(medical))
        self._emergency_revealer.set_reveal_child(False)
        self._medical_revealer.set_reveal_child(True)

    def _on_medical_back(self, _btn: Gtk.Button) -> None:
        self._medical_revealer.set_reveal_child(False)
        self._emergency_revealer.set_reveal_child(True)

    # ------------------------------------------------------------------
    # PIN input
    # ------------------------------------------------------------------

    def _on_digit(self, _btn: Gtk.Button, digit: str) -> None:
        if self._lockout_src is not None:
            return
        if len(self._pin) < _MAX_PIN:
            self._pin += digit
            self._refresh_dots()

    def _on_delete(self, _btn: Gtk.Button) -> None:
        if self._pin:
            self._pin = self._pin[:-1]
            self._refresh_dots()

    def _clear_pin(self) -> None:
        self._pin = ''
        self._refresh_dots()

    def _refresh_dots(self) -> None:
        self.dots_label.set_text('●' * len(self._pin))
        self.error_label.set_text('')

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def _check_pin(self) -> None:
        if self._lockout_src is not None:
            return
        if not self._pin:
            return
        if self._config.verify_pin(self._pin):
            # A successful PIN entry clears the boot gate, any explicit
            # "Lock now" demand, and restarts the 4-hour trust clock.
            # Fingerprint unlocks deliberately skip this: only the PIN
            # extends trust.
            try:
                smart_lock.note_pin_unlock(self._config)
            except Exception:
                pass
            self._unlock()
        else:
            self._fail_count += 1
            self._pin = ''
            self._refresh_dots()
            self._shake()
            secs = _lockout_secs(self._fail_count)
            if secs > 0:
                self.error_label.set_text('')
                self._persist_lockout_state(time.time() + secs)
                self._start_lockout(secs)
            else:
                self._persist_lockout_state(0.0)
                remaining = _FAIL_THRESHOLD - self._fail_count
                self.error_label.set_text(
                    f'Incorrect PIN — {remaining} attempt{"s" if remaining != 1 else ""} before lockout'
                )

    def _persist_lockout_state(self, until: float) -> None:
        try:
            self._config.set_lockout_state(self._fail_count, until)
        except Exception:
            pass

    def _resume_lockout_state(self) -> None:
        """A shell restart must not reset the escalating lockout: pick up
        the persisted fail count and re-arm any still-running lockout."""
        try:
            fails, until = self._config.get_lockout_state()
        except Exception:
            return
        self._fail_count = fails
        if fails > 0 and until - time.time() > 0:
            self._start_lockout(int(until - time.time()))

    def _unlock(self) -> None:
        self._fail_count = 0
        try:
            self._config.clear_lockout_state()
        except Exception:
            pass
        if self._lockout_src is not None:
            GLib.source_remove(self._lockout_src)
            self._lockout_src = None
        open_shade = self._open_shade_after_unlock
        self._open_shade_after_unlock = False
        self._on_unlock()
        self.set_visible(False)
        if open_shade:
            self._on_open_shade()

    # ------------------------------------------------------------------
    # Lockout timer
    # ------------------------------------------------------------------

    def _start_lockout(self, secs: int) -> None:
        self._lockout_until = GLib.get_monotonic_time() + secs * 1_000_000
        self._set_keypad_sensitive(False)
        self._tick_lockout()

    def _tick_lockout(self) -> bool:
        remaining_us = self._lockout_until - GLib.get_monotonic_time()
        if remaining_us <= 0:
            self._lockout_src = None
            self._set_keypad_sensitive(True)
            self.lockout_label.set_text('')
            self.error_label.set_text('Enter your PIN')
            return GLib.SOURCE_REMOVE
        secs = (remaining_us + 999_999) // 1_000_000
        self.lockout_label.set_text(f'Try again in {secs}s')
        self._lockout_src = GLib.timeout_add(1000, self._tick_lockout)
        return GLib.SOURCE_REMOVE  # the re-registered timer handles continuation

    def _set_keypad_sensitive(self, sensitive: bool) -> None:
        for btn in self._keypad_btns:
            btn.set_sensitive(sensitive)

    # ------------------------------------------------------------------
    # Fingerprint unlock
    # ------------------------------------------------------------------

    def try_fingerprint_unlock(self) -> None:
        if not self.get_visible():
            return
        if self._lockout_src is not None:
            return
        if self._fp_running:
            return
        if not self._fp_available():
            return
        self._fp_running = True
        self._fp_hint.set_label('Checking fingerprint…')
        threading.Thread(target=self._fp_thread, daemon=True, name='fp-verify').start()

    def _fp_thread(self) -> None:
        try:
            r = subprocess.run(
                ['fprintd-verify'],
                timeout=10, capture_output=True,
            )
            matched = r.returncode == 0
        except FileNotFoundError:
            matched = False  # fprintd not installed
        except Exception:
            matched = False
        GLib.idle_add(self._fp_result, matched)

    def _fp_result(self, matched: bool) -> bool:
        self._fp_running = False
        if matched:
            self._unlock()
        else:
            self._update_fp_hint()
        return GLib.SOURCE_REMOVE

    def _update_fp_hint(self) -> None:
        self._fp_hint.set_label(
            'Touch fingerprint sensor to unlock' if self._fp_available() else '')

    def on_shown(self) -> None:
        """The shell calls this after every present(): refresh the
        fingerprint probe (hardware can appear while the shell runs) — the
        probe completion auto-starts verification on enrolled devices."""
        self._fit_keypad_to_screen()
        self.refresh_fp_state()

    _fp_usable: bool | None = None
    _fp_probing = False
    _fp_guard = threading.Lock()

    @classmethod
    def _fp_available(cls) -> bool:
        """Cached probe result: True only with a reachable sensor AND
        enrolled prints. Never touches subprocess or D-Bus itself — the
        worker probe owns that, so the GTK thread never blocks here."""
        return cls._fp_usable is True

    def refresh_fp_state(self) -> None:
        """Probe fingerprint availability in a worker thread; the result
        lands back on the GTK loop via idle_add. Coalesced: at most one
        probe runs at a time."""
        with LockScreen._fp_guard:
            if LockScreen._fp_probing:
                return
            LockScreen._fp_probing = True
        threading.Thread(target=self._fp_probe_thread, daemon=True,
                         name='fp-probe').start()

    def _fp_probe_thread(self) -> None:
        usable = _fp_probe_sync()
        GLib.idle_add(self._fp_probe_done, usable)

    def _fp_probe_done(self, usable: bool) -> bool:
        LockScreen._fp_probing = False
        LockScreen._fp_usable = bool(usable)
        try:
            self._update_fp_hint()
            if (usable and self.get_visible() and not self._pin
                    and self._lockout_src is None
                    and not self._fp_running):
                self.try_fingerprint_unlock()
        except Exception:
            pass
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------------
    # Shake + clock
    # ------------------------------------------------------------------

    def _shake(self) -> None:
        self.dots_label.add_css_class('shake')
        GLib.timeout_add(380, lambda: self.dots_label.remove_css_class('shake') or False)

    def _refresh_clock(self) -> None:
        from clock_format import format_clock, resolve_24h

        now = datetime.now()
        self.clock_label.set_text(
            format_clock(now, resolve_24h(self._config)))
        self.date_label.set_text(now.strftime('%A, %d %b').replace(' 0', ' '))

    def _tick_clock(self) -> bool:
        self._refresh_clock()
        return True
