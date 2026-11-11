"""SIM PIN / PUK prompt.

A PIN-locked SIM keeps the modem in State LOCKED: it never registers, so
calls and data silently never work. ``modem_monitor`` reports each
modem's lock (``SimLockInfo``: Modem.UnlockRequired, UnlockRetries, the
Sim path) and :func:`handle_lock` shows a themed text-first sheet:

- SIM PIN (and the other PIN-type locks): one PIN field → ``Sim.SendPin``.
- SIM PUK (and the other PUK-type locks): PUK + new PIN + confirm →
  ``Sim.SendPuk``.
- Attempts left (UnlockRetries) are always shown; a wrong code shows a
  plain error and the updated count (ModemManager republishes
  UnlockRetries, which arrives here as a fresh report).

Both calls are async (``bus.call`` + callback): nothing blocks the main
loop. PIN/PUK digits live only in the entry and the D-Bus call; they are
never logged, and the entries are cleared on every submit.

The prompt is a system prompt like the polkit dialog (own undecorated
toplevel). It is not shown over the lock screen: while the shell is
locked the prompt waits and re-checks every few seconds, and
:func:`on_shell_unlocked` lets the lock screen show it at once (lock_screen
calls it after every successful unlock). "Later" dismisses it until the
lock changes (another kind of lock, or a modem re-probe); Settings →
Network & internet → Mobile data shows a "SIM PIN" row while a SIM is
locked that brings it back (:meth:`SimPinController.reopen`).

Lock semantics follow phosh's ``phosh-wwan-mm.c`` (GPL-3.0+): locked when
State is LOCKED and UnlockRequired is not NONE.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Callable

_MM1 = 'org.freedesktop.ModemManager1'
_SIM_IFACE = 'org.freedesktop.ModemManager1.Sim'
_SEND_TIMEOUT_MS = 20_000
_DEFER_POLL_S = 2

#: MMModemLock → what the user is asked for.
LOCK_NAMES = {
    2: 'SIM PIN', 3: 'SIM PIN2', 4: 'SIM PUK', 5: 'SIM PUK2',
    6: 'Service provider PIN', 7: 'Service provider PUK',
    8: 'Network PIN', 9: 'Network PUK', 10: 'Phone-to-SIM PIN',
    11: 'Corporate PIN', 12: 'Corporate PUK',
    13: 'Phone-to-first-SIM PIN', 14: 'Phone-to-first-SIM PUK',
    15: 'Network subset PIN', 16: 'Network subset PUK',
}
PUK_LOCKS = frozenset({4, 5, 7, 9, 12, 14, 16})

_controller = None


def _log():
    try:
        from shell_log import get_logger
        return get_logger('sim_pin')
    except Exception:  # pragma: no cover - logging is optional
        import logging
        return logging.getLogger('sim_pin')


# ------------------------------------------------------------------ pure

def lock_name(lock: int) -> str:
    return LOCK_NAMES.get(int(lock or 0), 'SIM PIN')


def is_puk(lock: int) -> bool:
    return int(lock or 0) in PUK_LOCKS


def title_for(lock: int) -> str:
    return '%s required' % lock_name(lock)


def explanation_for(lock: int) -> str:
    if is_puk(lock):
        return ('The SIM is blocked. Enter the 8-digit %s from your carrier '
                'and choose a new PIN.' % lock_name(lock))
    return 'Enter the %s to use calls, texts and mobile data.' % lock_name(lock)


def retries_text(retries: int | None, lock: int = 2) -> str:
    """'3 attempts left'; '' when the modem does not say."""
    if retries is None:
        return ''
    if retries <= 0:
        return 'No attempts left'
    if retries == 1:
        warn = ('the SIM will be unusable after this' if is_puk(lock)
                else 'the SIM will need its PUK after this')
        return '1 attempt left: %s' % warn
    return '%d attempts left' % retries


def validate_pin(pin: str) -> str | None:
    """None when ``pin`` is a valid SIM PIN (4–8 digits), else the error."""
    if not re.fullmatch(r'\d{4,8}', pin or ''):
        return 'A PIN is 4 to 8 digits.'
    return None


def validate_puk(puk: str, new_pin: str, confirm: str) -> str | None:
    if not re.fullmatch(r'\d{8}', puk or ''):
        return 'A PUK is 8 digits.'
    error = validate_pin(new_pin)
    if error:
        return 'New PIN: ' + error[0].lower() + error[1:]
    if new_pin != confirm:
        return 'The new PINs do not match.'
    return None


def plain_error(err: object, lock: int = 2) -> str:
    """ModemManager error → one plain sentence (never the digits)."""
    message = getattr(err, 'message', None) or str(err or '')
    if 'IncorrectPassword' in message or 'incorrect password' in message.lower():
        return 'Wrong %s.' % ('PUK' if is_puk(lock) else 'PIN')
    if 'SimPuk' in message:
        return 'Too many wrong PINs: the SIM now needs its PUK.'
    message = re.sub(r'^(GDBus\.Error:)?[\w.\-]+\.Error[\w.\-]*:\s*', '',
                     message.strip()).strip()
    if not message:
        return 'The modem rejected the code.'
    return message[0].upper() + message[1:]


# ---------------------------------------------------------------- D-Bus

def send_unlock(bus, sim_path: str, lock: int, code: str, new_pin: str,
                on_done: Callable[[bool, str], None], glib=None,
                gio=None) -> None:
    """Sim.SendPin(code) or, for PUK locks, Sim.SendPuk(code, new_pin).
    ``on_done(ok, error_text)`` fires exactly once."""
    if bus is None:
        on_done(False, 'The system bus is unavailable.')
        return
    if not sim_path:
        on_done(False, 'The modem reports no SIM to unlock.')
        return
    if glib is None or gio is None:
        from gi.repository import GLib, Gio
        glib = glib or GLib
        gio = gio or Gio
    if is_puk(lock):
        method, params = 'SendPuk', glib.Variant('(ss)', (code, new_pin))
    else:
        method, params = 'SendPin', glib.Variant('(s)', (code,))

    def _done(conn, result) -> None:
        try:
            conn.call_finish(result)
        except Exception as err:  # noqa: BLE001 - shown as text
            # Only the error name/message is logged, never the code.
            _log().warning('%s on %s failed: %s', method, sim_path,
                           getattr(err, 'message', None) or type(err).__name__)
            on_done(False, plain_error(err, lock))
            return
        on_done(True, '')

    try:
        bus.call(_MM1, sim_path, _SIM_IFACE, method, params, None,
                 gio.DBusCallFlags.NONE, _SEND_TIMEOUT_MS, None, _done)
    except Exception as err:  # noqa: BLE001
        on_done(False, plain_error(err, lock))


# ----------------------------------------------------------- controller

def _default_bus():
    try:
        from modem_monitor import _last_monitor
        return _last_monitor._bus if _last_monitor is not None else None
    except Exception:
        return None


def _default_shell_locked() -> bool:
    """True while the shell's lock screen is up (no lock_screen import:
    the application's shell window answers)."""
    try:
        from gi.repository import Gio
        app = Gio.Application.get_default()
        shell = getattr(app, '_shell', None)
        is_locked = getattr(shell, 'is_locked', None)
        return bool(is_locked()) if callable(is_locked) else False
    except Exception:
        return False


def _default_timeout(seconds: int, callback) -> int:
    from gi.repository import GLib
    return GLib.timeout_add_seconds(seconds, callback)


class SimPinController:
    """Keeps the latest lock report per modem and the one open prompt."""

    def __init__(self, view_factory=None, bus_provider=None,
                 shell_locked=None, timeout_add=None, sender=None) -> None:
        self._view_factory = view_factory or build_view
        self._bus_provider = bus_provider or _default_bus
        self._shell_locked = shell_locked or _default_shell_locked
        self._timeout_add = timeout_add or _default_timeout
        self._send = sender or send_unlock
        self._locks: dict = {}
        self._dismissed: set = set()
        self._view = None
        self._view_modem: str | None = None
        self._busy = False
        self._defer_src = None

    # -- reports ------------------------------------------------------

    def handle(self, info) -> None:
        modem = info.modem_path
        if not info.locked:
            self._locks.pop(modem, None)
            self._dismissed = {k for k in self._dismissed if k[0] != modem}
            if self._view_modem == modem:
                self._close_view()
            return
        previous = self._locks.get(modem)
        self._locks[modem] = info
        if previous is not None and previous.unlock_required != info.unlock_required:
            self._dismissed.discard((modem, previous.unlock_required))
        if self._view is not None and self._view_modem == modem:
            self._busy = False
            self._view.update(info)
            return
        self._show_when_unlocked()

    def on_shell_unlocked(self) -> None:
        self._show_when_unlocked()

    def reopen(self) -> None:
        """Show a dismissed prompt again (a settings row can call this)."""
        self._dismissed.clear()
        self._show_when_unlocked()

    @property
    def visible(self) -> bool:
        return self._view is not None

    def locked_sims(self) -> list:
        """Every locked SIM's latest report, dismissed ones included."""
        return [self._locks[modem] for modem in sorted(self._locks)]

    # -- presentation -------------------------------------------------

    def _next_info(self):
        for modem in sorted(self._locks):
            info = self._locks[modem]
            if (modem, info.unlock_required) not in self._dismissed:
                return info
        return None

    def _show_when_unlocked(self) -> None:
        if self._view is not None:
            return
        info = self._next_info()
        if info is None:
            return
        try:
            locked = bool(self._shell_locked())
        except Exception:
            locked = False
        if locked:
            if self._defer_src is None:
                self._defer_src = self._timeout_add(_DEFER_POLL_S, self._on_defer_tick)
            return
        self._view_modem = info.modem_path
        self._busy = False
        self._view = self._view_factory(info, self._submit, self._later)

    def _on_defer_tick(self) -> bool:
        self._defer_src = None
        self._show_when_unlocked()
        return False

    def _close_view(self) -> None:
        view, self._view, self._view_modem = self._view, None, None
        self._busy = False
        if view is not None:
            try:
                view.close()
            except Exception:
                pass
        # Another modem may still be waiting.
        self._show_when_unlocked()

    def _later(self) -> None:
        info = self._locks.get(self._view_modem)
        if info is not None:
            self._dismissed.add((info.modem_path, info.unlock_required))
        self._close_view()

    def _submit(self, code: str, new_pin: str = '', confirm: str = '') -> None:
        info = self._locks.get(self._view_modem)
        view = self._view
        if info is None or view is None or self._busy:
            return
        lock = info.unlock_required
        error = (validate_puk(code, new_pin, confirm) if is_puk(lock)
                 else validate_pin(code))
        if error:
            view.set_note(error)
            return
        self._busy = True
        view.set_busy(True)
        view.set_note('Unlocking…')
        modem = info.modem_path

        def _done(ok: bool, error_text: str) -> None:
            if self._view is not view:
                return
            self._busy = False
            view.set_busy(False)
            if ok:
                # ModemManager re-probes the modem; its unlocked report
                # (or the modem vanishing) closes the prompt too.
                _log().info('SIM on %s unlocked', modem)
                self._locks.pop(modem, None)
                self._close_view()
                return
            view.set_note(error_text)

        self._send(self._bus_provider(), info.sim_path, lock, code, new_pin, _done)


def controller() -> SimPinController:
    global _controller
    if _controller is None:
        _controller = SimPinController()
    return _controller


def handle_lock(info) -> None:
    """modem_monitor's default ``on_sim_lock`` target."""
    controller().handle(info)


def on_shell_unlocked() -> None:
    """Hook for the lock screen: show a deferred SIM prompt right away."""
    if _controller is not None:
        _controller.on_shell_unlocked()


def locked_summary() -> str | None:
    """Settings row value while a SIM waits for its PIN or PUK ('SIM PIN
    required'), None otherwise. Never creates the controller: no report
    yet means no locked SIM."""
    if _controller is None:
        return None
    try:
        infos = _controller.locked_sims()
    except Exception:
        return None
    if not infos:
        return None
    return '%s required' % lock_name(infos[0].unlock_required)


# ------------------------------------------------------------------ view

def build_view(info, on_submit, on_later):
    """The themed prompt window. Returns a namespace with ``update(info)``,
    ``set_note(text)``, ``set_busy(bool)`` and ``close()``."""
    from gi.repository import Gtk
    from xx_ui import widgets

    window = Gtk.Window(title='SIM')
    window.set_decorated(False)
    window.add_css_class('xx-dialog')
    body = widgets.dialog_box(title_for(info.unlock_required))
    body.set_size_request(312, -1)
    explanation = widgets.wrap_label(explanation_for(info.unlock_required))
    body.append(explanation)
    attempts = widgets.caption('')
    body.append(attempts)

    def _entry(placeholder: str) -> Gtk.Entry:
        entry = widgets.deferred_entry(placeholder)
        entry.set_visibility(False)
        entry.set_input_purpose(Gtk.InputPurpose.PIN)
        try:
            from osk import attach, set_visible
            attach(entry, on_show=lambda: set_visible(True),
                   on_hide=lambda: set_visible(False))
        except Exception:
            pass
        body.append(entry)
        return entry

    code_entry = _entry('PIN')
    new_entry = _entry('New PIN')
    confirm_entry = _entry('Confirm new PIN')

    note = widgets.wrap_label('')
    note.set_visible(False)
    body.append(note)

    buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    later_btn = widgets.outline_button('Later', lambda *_a: on_later())
    later_btn.set_hexpand(True)
    buttons.append(later_btn)

    def _commit(*_args) -> None:
        code, new_pin, confirm = (code_entry.get_text(), new_entry.get_text(),
                                  confirm_entry.get_text())
        for entry in (code_entry, new_entry, confirm_entry):
            entry.set_text('')
        on_submit(code, new_pin, confirm)

    unlock_btn = widgets.primary_button('Unlock', _commit)
    unlock_btn.set_hexpand(True)
    buttons.append(unlock_btn)
    body.append(buttons)
    for entry in (code_entry, new_entry, confirm_entry):
        entry.connect('activate', _commit)
    window.set_child(body)

    closing = {'done': False}

    def _on_close_request(_win) -> bool:
        if not closing['done']:
            closing['done'] = True
            on_later()
        return False

    window.connect('close-request', _on_close_request)

    def _update(fresh) -> None:
        lock = fresh.unlock_required
        puk = is_puk(lock)
        title = body.get_first_child()
        if isinstance(title, Gtk.Label):
            title.set_text(title_for(lock))
        explanation.set_text(explanation_for(lock))
        code_entry.set_placeholder_text(lock_name(lock).split()[-1] if puk else 'PIN')
        new_entry.set_visible(puk)
        confirm_entry.set_visible(puk)
        text = retries_text(fresh.retries_left(), lock)
        attempts.set_text(text.upper())
        attempts.set_visible(bool(text))

    def _set_note(text: str) -> None:
        note.set_text(text or '')
        note.set_visible(bool(text))

    def _set_busy(busy: bool) -> None:
        unlock_btn.set_sensitive(not busy)

    def _close() -> None:
        closing['done'] = True
        try:
            window.close()
        except Exception:
            pass

    _update(info)
    window.present()
    return SimpleNamespace(window=window, update=_update, set_note=_set_note,
                           set_busy=_set_busy, close=_close)
