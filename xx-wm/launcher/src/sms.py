from __future__ import annotations

import gi
import re
import subprocess
import threading
from datetime import datetime

gi.require_version('Gtk', '4.0')

from gi.repository import Gdk, GLib, Gtk
from config import ShellConfig, ThemePreset
from contacts import shared_book, display_number, load_sms_history


def theme_css(preset: ThemePreset) -> str:
    return f"""
.sms-root {{
    background: {preset.background};
    color: {preset.foreground};
}}
.sms-header {{
    font-size: 13pt;
    font-weight: 600;
    color: {preset.foreground};
    padding: 16px 20px;
    border-bottom: 1px solid {preset.border};
}}
.sms-list {{
    background: transparent;
    padding: 12px;
}}
.bubble-out {{
    background: {preset.accent};
    color: {preset.background};
    border-radius: 18px 18px 4px 18px;
    padding: 12px 16px;
    margin-left: 60px;
    margin-bottom: 4px;
    font-size: 13pt;
}}
.bubble-in {{
    background: {preset.surface};
    color: {preset.foreground};
    border-radius: 18px 18px 18px 4px;
    padding: 12px 16px;
    margin-right: 60px;
    margin-bottom: 4px;
    font-size: 13pt;
}}
.bubble-time {{
    font-size: 9pt;
    color: {preset.muted};
    margin-bottom: 8px;
}}
.sms-input {{
    font-size: 13pt;
    min-height: 52px;
    background: {preset.surface};
    color: {preset.foreground};
    border-radius: 26px;
    border: 1px solid {preset.border};
    padding: 0 16px;
}}
.sms-send {{
    font-size: 13pt;
    min-width: 64px;
    min-height: 52px;
    border-radius: 26px;
    background: {preset.accent};
    color: {preset.background};
    border: none;
}}
.sms-send:hover {{ background: mix({preset.accent}, {preset.background}, 0.85); }}
.bubble-failed {{
    background: {preset.surface};
    color: {preset.muted};
}}
"""

_SMS_PATH_RE = re.compile(r'/org/freedesktop/ModemManager1/SMS/\d+')
_MMCLI_TIMEOUT_S = 15


def _resolved_modem() -> str:
    """Modem selector for mmcli, resolved from D-Bus; falls back to '0'."""
    try:
        from modem_monitor import resolve_modem_path
        return resolve_modem_path() or '0'
    except Exception:
        return '0'


def send_message(number: str, text: str) -> tuple[bool, str]:
    """Send an SMS; returns (success, error detail).

    Prefers Messaging.Create + Sms.Send over D-Bus (no mmcli output
    scraping, so multi-line text survives intact); falls back to mmcli
    (create, then send via the returned SMS object — -s selects SMS
    objects) only when the D-Bus transport fails. Blocking: worker thread.
    """
    try:
        from modem_monitor import messaging_create_and_send
        ok, detail = messaging_create_and_send(number, text)
        if ok:
            return True, detail
    except Exception as err:
        detail = str(err)
    modem = _resolved_modem()
    # mmcli only CREATES with --messaging-create-sms; the returned SMS
    # object must then be sent explicitly (-s selects SMS objects).
    payload = f"number='{_payload_value(number)}',text='{_payload_value(text)}'"
    ok, out = _run_mmcli(['-m', modem, f'--messaging-create-sms={payload}'])
    match = _SMS_PATH_RE.search(out) if ok else None
    if match is not None:
        ok, out = _run_mmcli(['-m', modem, '-s', match.group(0), '--send'])
    elif ok:
        ok, out = False, 'modem returned no SMS path'
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


def _payload_value(value: str) -> str:
    # mmcli splits key=value payloads on commas outside quotes and its
    # scanner cannot carry raw control chars or embedded quotes through a
    # single-quoted value, so flatten whitespace and swap apostrophes for ’.
    flattened = ''.join(ch if ch.isprintable() else '\x00' for ch in value)
    collapsed = ' '.join(part for part in flattened.split('\x00') if part)
    return collapsed.replace("'", '\u2019')


class Message:
    __slots__ = ('text', 'outgoing', 'timestamp', 'failed')

    def __init__(self, text: str, outgoing: bool, timestamp: datetime | None = None,
                 failed: bool = False) -> None:
        self.text = text
        self.outgoing = outgoing
        self.timestamp = timestamp or datetime.now()
        self.failed = failed


class SMSConversation(Gtk.Window):
    """
    SMS conversation view for a single contact/number.
    Backend: mmcli (ModemManager) for send/receive.
    Use open_conversation(number, display_name) to show.
    """

    def __init__(self, config: ShellConfig | None = None) -> None:
        super().__init__(title='Messages')
        self.set_default_size(420, 860)

        self._number = ''
        self._messages: list[Message] = []
        self._contact_book = shared_book()
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

    def apply_theme(self, preset: ThemePreset | None = None) -> None:
        """(Re)load this surface's display-level sheet. The shell window
        passes the freshly resolved preset on hot-reload fan-out; standalone
        falls back to the injected config's own preset."""
        data = theme_css(preset if preset is not None else self._config.theme)
        self._theme_provider.load_from_data(data.encode('utf-8'))

    def _build(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.add_css_class('sms-root')

        self._header_label = Gtk.Label(label='', xalign=0)
        self._header_label.add_css_class('sms-header')
        self._header_label.set_hexpand(True)

        self._msg_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._msg_list.add_css_class('sms-list')
        self._msg_list.set_vexpand(True)

        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_child(self._msg_list)

        self._input = Gtk.Entry()
        self._input.add_css_class('sms-input')
        self._input.set_placeholder_text('Message')
        self._input.set_hexpand(True)
        self._input.connect('activate', self._on_send)
        from osk import attach, set_visible
        attach(self._input, on_show=lambda: set_visible(True),
               on_hide=lambda: set_visible(False))

        send_btn = Gtk.Button(label='Send')
        send_btn.add_css_class('sms-send')
        send_btn.connect('clicked', self._on_send)

        input_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        input_row.set_margin_start(12)
        input_row.set_margin_end(12)
        input_row.set_margin_top(8)
        input_row.set_margin_bottom(20)
        input_row.append(self._input)
        input_row.append(send_btn)

        root.append(self._header_label)
        root.append(scroller)
        root.append(input_row)

        self._scroller = scroller
        return root

    def open_conversation(self, number: str, display_name: str = '') -> None:
        self._number = number
        # Prefer caller-supplied name, then contact book lookup, then the
        # shared xx_phone number display (never the raw digits).
        resolved = (display_name or self._contact_book.lookup_number(number)
                    or display_number(number))
        self._header_label.set_text(resolved)
        self._messages.clear()
        self._rebuild_messages()
        self._load_history()
        self.present()

    def receive_message(self, text: str) -> None:
        self._messages.append(Message(text, outgoing=False))
        self._append_bubble(self._messages[-1])
        self._scroll_to_bottom()

    def _on_send(self, _widget: Gtk.Widget) -> None:
        text = self._input.get_text().strip()
        if not text or not self._number:
            return
        self._input.set_text('')
        msg = Message(text, outgoing=True)
        self._messages.append(msg)
        self._append_bubble(msg)
        self._scroll_to_bottom()
        threading.Thread(target=self._deliver, args=(msg, self._number), daemon=True).start()

    def _deliver(self, msg: Message, number: str) -> None:
        ok, out = send_message(number, msg.text)
        if not ok:
            from shell_log import get_logger
            get_logger('sms').warning('send to %s failed: %s', number, out)
        GLib.idle_add(self._mark_sent, msg, ok)

    def _mark_sent(self, msg: Message, sent: bool) -> bool:
        if sent:
            return False
        msg.failed = True
        if msg in self._messages:
            self._rebuild_messages()
        return False

    def _load_history(self) -> None:
        # Run mmcli history fetch in a background thread — avoids blocking the UI
        # while mmcli queries ModemManager over DBus for potentially many SMS records
        number = self._number

        def _fetch() -> None:
            all_msgs = load_sms_history()
            # Filter to this conversation's number using tail-7 matching
            import re as _re
            stripped = _re.sub(r'[^\d]', '', number)
            tail = min(7, len(stripped))
            history: list[Message] = []
            for m in all_msgs:
                ns = _re.sub(r'[^\d]', '', m.get('number', ''))
                if tail and stripped[-tail:] == ns[-tail:]:
                    ts = datetime.now()
                    iso = m.get('timestamp_iso', '')
                    if iso:
                        try:
                            ts = datetime.fromisoformat(iso.replace('Z', '+00:00'))
                        except ValueError:
                            pass
                    history.append(Message(m['text'], m['outgoing'], ts))
            # Sort chronologically
            history.sort(key=lambda msg: msg.timestamp)
            # Prepend to conversation on the GLib main thread
            GLib.idle_add(self._prepend_history, history)

        threading.Thread(target=_fetch, daemon=True).start()

    def _prepend_history(self, history: list[Message]) -> bool:
        if not history:
            return False
        # Prepend historical messages before any already-shown new messages
        existing = list(self._messages)
        self._messages = history + existing
        self._rebuild_messages()
        # Scroll to bottom to show most recent
        self._scroll_to_bottom()
        return False

    def _rebuild_messages(self) -> None:
        child = self._msg_list.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._msg_list.remove(child)
            child = nxt
        for msg in self._messages:
            self._append_bubble(msg)

    def _append_bubble(self, msg: Message) -> None:
        bubble = Gtk.Label(label=msg.text, wrap=True, xalign=0 if not msg.outgoing else 1)
        bubble.add_css_class('bubble-out' if msg.outgoing else 'bubble-in')
        stamp = msg.timestamp.strftime('%H:%M')
        if msg.failed:
            bubble.add_css_class('bubble-failed')
            stamp += ' · not sent'

        time_label = Gtk.Label(
            label=stamp,
            xalign=1 if msg.outgoing else 0,
        )
        time_label.add_css_class('bubble-time')

        row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        row.append(bubble)
        row.append(time_label)
        self._msg_list.append(row)

    def _scroll_to_bottom(self) -> None:
        def _do_scroll() -> bool:
            adj = self._scroller.get_vadjustment()
            adj.set_value(adj.get_upper())
            return False
        GLib.idle_add(_do_scroll)
