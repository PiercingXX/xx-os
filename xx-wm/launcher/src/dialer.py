from __future__ import annotations

import gi
import re
import subprocess
import threading

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, Gio, GLib, Gtk
from config import ShellConfig, ThemePreset
from contacts import Contact, shared_book


def theme_css(preset: ThemePreset) -> str:
    return f"""
.dialer-root {{
    background: {preset.background};
    color: {preset.foreground};
}}
.dialer-display {{
    font-size: 28pt;
    font-weight: 300;
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.05em;
    color: {preset.foreground};
    min-height: 72px;
}}
.dialer-button {{
    font-size: 20pt;
    font-weight: 300;
    min-width: 100px;
    min-height: 80px;
    border-radius: 50%;
    background: {preset.surface};
    color: {preset.foreground};
    border: none;
    padding: 0;
}}
.dialer-button:hover {{ background: {preset.surface_alt}; }}
.dialer-sub {{
    font-size: 8pt;
    color: {preset.muted};
    margin-top: -2px;
}}
.dialer-status {{
    font-size: 10pt;
    color: {preset.muted};
    min-height: 20px;
    margin-bottom: 8px;
}}
.call-button {{
    font-size: 14pt;
    min-width: 100px;
    min-height: 80px;
    border-radius: 50%;
    background: {preset.accent};
    color: {preset.background};
    border: none;
}}
.call-button:hover {{ background: mix({preset.accent}, {preset.background}, 0.15); }}
.del-button {{
    font-size: 14pt;
    min-width: 100px;
    min-height: 80px;
    border-radius: 50%;
    background: transparent;
    color: {preset.muted};
    border: none;
}}
.del-button:hover {{ background: {preset.surface_alt}; }}
.contact-row {{
    font-size: 14pt;
    padding: 12px 16px;
}}
.contact-name {{ font-size: 14pt; color: {preset.foreground}; }}
.contact-number {{ font-size: 10pt; color: {preset.muted}; }}
"""

_KEYPAD: list[tuple[str, str]] = [
    ('1', ''),   ('2', 'ABC'), ('3', 'DEF'),
    ('4', 'GHI'),('5', 'JKL'), ('6', 'MNO'),
    ('7', 'PQRS'),('8', 'TUV'),('9', 'WXYZ'),
    ('*', ''),   ('0', '+'),   ('#', ''),
]

_CALL_PATH_RE = re.compile(r'/org/freedesktop/ModemManager1/Call/\d+')
_MMCLI_TIMEOUT_S = 15


def _resolved_modem() -> str:
    """Modem selector for mmcli, resolved from D-Bus: modems re-index after
    resets, so the hardcoded index 0 breaks. Falls back to '0' offline."""
    try:
        from modem_monitor import resolve_modem_path
        return resolve_modem_path() or '0'
    except Exception:
        return '0'


def place_call(number: str) -> tuple[bool, str]:
    """Place a voice call to ``number``; (success, detail for the status line).

    Prefers Voice.CreateCall + Call.Start over D-Bus; only when that
    transport fails does it fall back to mmcli (create, then start via the
    returned object path — mmcli has no single dial verb, -o selects Call
    objects). Blocking: call only from a worker thread."""
    try:
        from modem_monitor import voice_create_and_start
        ok, detail = voice_create_and_start(number)
        if ok:
            return True, detail
    except Exception as err:
        detail = str(err)
    modem = _resolved_modem()
    ok, out = _run_mmcli(['-m', modem, f'--voice-create-call=number={number}'])
    if not ok:
        return False, out.strip()
    match = _CALL_PATH_RE.search(out)
    if match is None:
        return False, 'modem returned no call path'
    ok, out = _run_mmcli(['-m', modem, '-o', match.group(0), '--start'])
    return ok, out.strip()


def _run_mmcli(args: list[str]) -> tuple[bool, str]:
    """Run mmcli; returns (success, stdout on success or error detail)."""
    try:
        proc = subprocess.run(
            ['mmcli', *args], capture_output=True, text=True, timeout=_MMCLI_TIMEOUT_S,
        )
    except FileNotFoundError:
        return False, 'mmcli not installed'
    except subprocess.TimeoutExpired:
        return False, 'mmcli timed out'
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f'mmcli exited with status {proc.returncode}'
        return False, detail
    return True, proc.stdout


BUILTIN_EMERGENCY_NUMBERS = frozenset({
    '112', '911', '999', '000', '08', '110',
    '118', '119', '100', '101', '102', '108',
})

EMERGENCY_REFUSAL = 'Only emergency calls from the lock screen'

_MODEM3GPP_IFACE = 'org.freedesktop.ModemManager1.Modem.Modem3gpp'
_MODEM_IFACE = 'org.freedesktop.ModemManager1.Modem'
_SIM_IFACE = 'org.freedesktop.ModemManager1.Sim'
_PROPS_IFACE = 'org.freedesktop.DBus.Properties'
_MM_BUS = 'org.freedesktop.ModemManager1'
_DBUS_TIMEOUT_MS = 5000


def normalize_emergency_number(raw: str) -> str:
    """Digits-only form of a dial string for emergency matching: spaces,
    dashes, dots, parens and a leading + never change which service is
    dialed, so they are stripped before comparison. Pure."""
    if not isinstance(raw, str):
        return ''
    return re.sub(r'\D', '', raw)


def is_emergency_number(raw: str, extra: object = None) -> bool:
    """True when ``raw`` dials an emergency service: the built-in list
    union the modem's own list (``extra``). Exact match on the
    digits-only form — a longer number merely containing 112 is not an
    emergency call. Pure."""
    norm = normalize_emergency_number(raw)
    if not norm:
        return False
    if norm in BUILTIN_EMERGENCY_NUMBERS:
        return True
    if isinstance(extra, (list, tuple, set, frozenset)):
        for candidate in extra:
            if isinstance(candidate, str) and normalize_emergency_number(candidate) == norm:
                return True
    return False


def _str_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, str) and item]


def get_modem_emergency_numbers(bus=None, modem: str | None = None,
                                timeout_ms: int = _DBUS_TIMEOUT_MS) -> list[str]:
    """Emergency numbers the modem/SIM reports: the Modem3gpp
    EmergencyNumbers property, then the SIM's EmergencyNumbers property
    via the modem's Sim path. [] on any failure — every source is
    optional, and a modem without either list still places the built-in
    numbers. Blocking: call only from a worker thread."""
    try:
        if bus is None:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        if bus is None:
            return []
        if modem is None:
            from modem_monitor import resolve_modem_path
            modem = resolve_modem_path()
        if not modem:
            return []
        found: list[str] = []
        try:
            props = dict(bus.call_sync(
                _MM_BUS, modem, _PROPS_IFACE, 'GetAll',
                GLib.Variant('(s)', (_MODEM3GPP_IFACE,)), None,
                Gio.DBusCallFlags.NONE, timeout_ms, None,
            ).unpack()[0])
            found.extend(_str_list(props.get('EmergencyNumbers')))
        except Exception:
            pass
        try:
            sim_path = bus.call_sync(
                _MM_BUS, modem, _PROPS_IFACE, 'Get',
                GLib.Variant('(ss)', (_MODEM_IFACE, 'Sim')), None,
                Gio.DBusCallFlags.NONE, timeout_ms, None,
            ).unpack()[0]
        except Exception:
            sim_path = None
        if sim_path and sim_path != '/':
            try:
                sim_props = dict(bus.call_sync(
                    _MM_BUS, str(sim_path), _PROPS_IFACE, 'GetAll',
                    GLib.Variant('(s)', (_SIM_IFACE,)), None,
                    Gio.DBusCallFlags.NONE, timeout_ms, None,
                ).unpack()[0])
                found.extend(_str_list(sim_props.get('EmergencyNumbers')))
            except Exception:
                pass
        seen: set[str] = set()
        out: list[str] = []
        for number in found:
            norm = normalize_emergency_number(number)
            if norm and norm not in seen:
                seen.add(norm)
                out.append(norm)
        return out
    except Exception:
        return []


def place_emergency_call(number: str, extra: object = None) -> tuple[bool, str]:
    """Place ``number`` only when it dials an emergency service; anything
    else is refused without touching the modem. Blocking: call only from
    a worker thread — ModemManager places emergency calls even without
    a SIM when the modem allows, no unlock needed."""
    if not is_emergency_number(number, extra):
        return False, EMERGENCY_REFUSAL
    try:
        from modem_monitor import voice_create_and_start
        return voice_create_and_start(normalize_emergency_number(number))
    except Exception as err:
        return False, str(err)


class Dialer(Gtk.Window):
    """Standalone dialer window — numeric keypad + contact search + call via ModemManager."""

    def __init__(self, dnd_state: object | None = None,
                 config: ShellConfig | None = None) -> None:
        super().__init__(title='Dialer')
        self.set_default_size(420, 860)

        self._digits = ''
        self._suppress_zero_click = False
        self._contact_book = shared_book()
        self._dnd = dnd_state
        # The shell window threads its LIVE config so hot reloads reach this
        # surface; the fallback keeps direct no-arg construction working.
        self._config = config if config is not None else ShellConfig()

        self._theme_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), self._theme_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 4,
        )
        self.apply_theme()

        self.set_child(self._build())
        self._update_display()

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        """(Re)load this surface's display-level sheet. The shell window
        passes the freshly resolved preset on hot-reload fan-out; standalone
        falls back to the injected config's own preset."""
        data = theme_css(preset if preset is not None else self._config.theme)
        self._theme_provider.load_from_data(data.encode('utf-8'))

    def _build(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        root.add_css_class('dialer-root')
        root.set_margin_top(16)
        root.set_margin_start(16)
        root.set_margin_end(16)
        root.set_margin_bottom(24)

        # Header row: back chevron
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        back_btn = Gtk.Button(label='‹ Back')
        back_btn.add_css_class('flat')
        back_btn.connect('clicked', lambda _b: self.close())
        header.append(back_btn)
        header.set_margin_bottom(8)
        root.append(header)

        # Swipe down on the header row closes. It must not live on the root:
        # a downward swipe starting over the keypad is dialing-adjacent,
        # not a close gesture.
        swipe = Gtk.GestureSwipe.new()
        swipe.connect('swipe', lambda _g, vx, vy: self.close() if vy > 300 else None)
        header.add_controller(swipe)

        # Number display
        self._display = Gtk.Label(label='', xalign=1)
        self._display.add_css_class('dialer-display')
        self._display.set_hexpand(True)
        self._display.set_margin_bottom(16)
        self._display.set_margin_start(8)
        self._display.set_margin_end(8)

        self._status = Gtk.Label(label='', xalign=0)
        self._status.add_css_class('dialer-status')

        # Contact suggestions (shown while typing)
        self._suggestions = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._suggestions.add_css_class('text-list')
        self._suggestions.set_visible(False)

        # Keypad
        keypad = Gtk.Grid(row_spacing=10, column_spacing=10)
        keypad.set_halign(Gtk.Align.CENTER)
        keypad.set_margin_bottom(16)

        for idx, (digit, sub) in enumerate(_KEYPAD):
            col, row = idx % 3, idx // 3
            btn = self._make_digit_btn(digit, sub)
            keypad.attach(btn, col, row, 1, 1)

        # Bottom action row: delete, call, (empty)
        action_row = Gtk.Grid(row_spacing=0, column_spacing=10)
        action_row.set_halign(Gtk.Align.CENTER)

        placeholder = Gtk.Box()
        placeholder.set_size_request(100, 80)

        del_btn = Gtk.Button(label='←')
        del_btn.add_css_class('del-button')
        del_btn.connect('clicked', self._on_delete)

        long_press = Gtk.GestureLongPress.new()
        long_press.connect('pressed', lambda *_: self._clear_all())
        del_btn.add_controller(long_press)

        call_btn = Gtk.Button(label='Call')
        call_btn.add_css_class('call-button')
        call_btn.connect('clicked', self._on_call)

        action_row.attach(placeholder, 0, 0, 1, 1)
        action_row.attach(call_btn, 1, 0, 1, 1)
        action_row.attach(del_btn, 2, 0, 1, 1)

        root.append(self._display)
        root.append(self._status)
        root.append(self._suggestions)
        root.append(keypad)
        root.append(action_row)
        return root

    def _make_digit_btn(self, digit: str, sub: str) -> Gtk.Button:
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        inner.set_halign(Gtk.Align.CENTER)
        inner.set_valign(Gtk.Align.CENTER)

        d_label = Gtk.Label(label=digit)
        inner.append(d_label)

        if sub:
            s_label = Gtk.Label(label=sub)
            s_label.add_css_class('dialer-sub')
            inner.append(s_label)

        btn = Gtk.Button()
        btn.add_css_class('dialer-button')
        btn.set_child(inner)
        btn.connect('clicked', self._on_digit, digit)
        if digit == '0':
            # Long-press 0 enters '+': the release click that follows the
            # hold is suppressed so the digit is not appended twice.
            zero_hold = Gtk.GestureLongPress.new()
            zero_hold.set_touch_only(False)
            zero_hold.connect('pressed', self._on_zero_longpress)
            btn.add_controller(zero_hold)
        return btn

    def _on_zero_longpress(
        self, _gesture: Gtk.GestureLongPress, _x: float, _y: float,
    ) -> None:
        self._suppress_zero_click = True
        if len(self._digits) < 20:
            self._digits = '+' if not self._digits else f'{self._digits}+'
            self._update_display()
            self._refresh_suggestions()

    def _on_digit(self, _btn: Gtk.Button, digit: str) -> None:
        if digit == '0' and self._suppress_zero_click:
            self._suppress_zero_click = False
            return
        if len(self._digits) < 20:
            self._digits += digit
            self._update_display()
            self._refresh_suggestions()

    def _on_delete(self, _btn: Gtk.Button) -> None:
        if self._digits:
            self._digits = self._digits[:-1]
            self._update_display()
            self._refresh_suggestions()

    def _clear_all(self) -> None:
        self._digits = ''
        self._update_display()
        self._refresh_suggestions()

    def _update_display(self) -> None:
        self._display.set_text(self._format_number(self._digits))

    def _format_number(self, digits: str) -> str:
        """Keypad entry grouped by the shared xx_phone rule (the formatter
        the xx-dialer app uses). Explicit empty operator strings keep the
        country lookup off D-Bus, so this never blocks the GTK main loop."""
        if not digits:
            return ''
        try:
            import xx_phone
            return xx_phone.as_you_type(
                digits, xx_phone.user_country("", ""))
        except Exception:
            return digits

    def _refresh_suggestions(self) -> None:
        child = self._suggestions.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self._suggestions.remove(child)
            child = nxt

        if not self._digits:
            self._suggestions.set_visible(False)
            return

        matches = self._contact_book.search(self._digits, limit=4)
        if not matches:
            self._suggestions.set_visible(False)
            return

        for contact in matches:
            row = self._make_suggestion_row(contact)
            self._suggestions.append(row)

        self._suggestions.set_visible(True)

    def _make_suggestion_row(self, contact: Contact) -> Gtk.ListBoxRow:
        starred = self._dnd is not None and self._dnd.is_starred(contact.primary_number())
        name_lbl = Gtk.Label(label=('★ ' if starred else '') + contact.name, xalign=0)
        name_lbl.add_css_class('contact-name')

        num_lbl = Gtk.Label(label=contact.primary_number(), xalign=0)
        num_lbl.add_css_class('contact-number')

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        inner.set_margin_start(4)
        inner.append(name_lbl)
        inner.append(num_lbl)

        row = Gtk.ListBoxRow(selectable=False, activatable=True)
        row.add_css_class('contact-row')
        row.set_child(inner)
        row.connect('activate', lambda _r, c=contact: self._fill_from_contact(c))
        if self._dnd is not None:
            # Long-press → star/unstar: starred contacts ring through DnD
            def _toggle_star(gesture: Gtk.GestureLongPress, _x: float, _y: float,
                             c: Contact = contact, was_starred: bool = starred) -> None:
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                self._dnd.set_starred(c.primary_number(), not was_starred)
                self._refresh_suggestions()

            long_press = Gtk.GestureLongPress.new()
            long_press.set_touch_only(False)
            long_press.connect('pressed', _toggle_star)
            row.add_controller(long_press)
        return row

    def _fill_from_contact(self, contact: Contact) -> None:
        num = re.sub(r'[^\d+]', '', contact.primary_number())
        self._digits = num
        self._update_display()
        self._suggestions.set_visible(False)

    def _on_call(self, _btn: Gtk.Button) -> None:
        if not self._digits:
            return
        self._initiate_call(self._digits)

    def _initiate_call(self, number: str) -> None:
        self._set_status(f'Calling {self._format_number(number)}…')
        threading.Thread(target=self._place_call, args=(number,), daemon=True).start()

    def _place_call(self, number: str) -> None:
        ok, detail = place_call(number)
        self._finish_dial('Dialing…' if ok else f'Call failed: {detail.strip()}')

    def _finish_dial(self, message: str) -> None:
        GLib.idle_add(self._set_status, message)

    def _set_status(self, message: str) -> None:
        self._status.set_text(message)
