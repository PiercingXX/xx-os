"""Lock settings: change PIN and change the device password.

Change PIN verifies the current PIN — through the root helper when it
holds the PIN (async: helper_client.pin_change, lockout enforced
root-side), else against the local config hash — respects the
escalating lockout, and stores the twice-confirmed new PIN through
helper_client.pin_change (config first, then moved into the helper when
it is installed). PIN digits are never logged —
this module logs nothing at all. Change device password opens `passwd`
in a terminal, the same terminal-table path as system updates.
"""
from __future__ import annotations

import time

import settings_page
import settings_ui as ui

_MAX_PIN = 64


def _lockout_remaining(config) -> int:
    """Seconds left on the persisted lockout, by the lock screen's rule:
    never more than the fail count earns, and nothing when the wall clock
    went back before the lockout started — an RTC reset must not block a
    PIN change for years."""
    try:
        from lock_lines import resume_lockout_secs
        fails, until = config.get_lockout_state()
        start = config.get_lockout_start()
        secs = resume_lockout_secs(fails, until, start, time.time())
    except Exception:
        return 0
    return secs or 0


def _note_wrong_pin(config, fail_count: int) -> None:
    try:
        from lock_lines import _lockout_secs
        secs = _lockout_secs(fail_count)
    except Exception:
        secs = 0
    try:
        if secs > 0:
            now = time.time()
            config.set_lockout_state(fail_count, now + secs, start=now)
        else:
            config.set_lockout_state(fail_count, 0.0)
    except Exception:
        pass


def validate_new_pin(current: str, new: str, confirm: str) -> str | None:
    """Error string for a PIN change, None when the new PIN is usable."""
    if not new:
        return 'Enter a new PIN.'
    if new != confirm:
        return 'New PINs do not match.'
    if len(new) > _MAX_PIN:
        return 'New PIN is too long.'
    if new == current:
        return 'The new PIN must differ from the current one.'
    return None


def _helper_change(config, current: str, new: str, on_async) -> None:
    import helper_client

    def _done(done: bool, retry_after: int, error) -> None:
        if done:
            try:
                config.clear_lockout_state()
            except Exception:
                pass
            on_async(True, 'PIN changed.')
        elif error is not None:
            on_async(False, 'Could not reach the PIN service.')
        elif retry_after > 0:
            on_async(False, 'Locked out — try again in %ds.' % retry_after)
        else:
            on_async(False, 'Current PIN is wrong.')

    helper_client.pin_change(config, current, new, _done)


def change_pin(config, current: str, new: str, confirm: str,
               on_async=None) -> tuple[bool | None, str]:
    """Verify `current`, then store `new`.

    A live lockout refuses the change; a wrong current PIN escalates
    the lockout like the lock screen does; success clears it. When the
    root helper holds the PIN the check is async: the return is
    ``(None, message)`` and ``on_async(ok, message)`` fires later (no
    ``on_async`` → refused). Never logs — PIN digits must not reach any
    log.
    """
    if getattr(config, 'pin_in_helper', False):
        error = validate_new_pin(current, new, confirm)
        if error is not None:
            return False, error
        if on_async is None:
            return False, 'Could not verify the current PIN.'
        try:
            _helper_change(config, current, new, on_async)
        except Exception:
            return False, 'Could not verify the current PIN.'
        return None, 'Checking current PIN…'
    if _lockout_remaining(config) > 0:
        return False, 'Locked out — try again when the timer ends.'
    try:
        correct = config.verify_pin(current)
    except Exception:
        return False, 'Could not verify the current PIN.'
    if not correct:
        try:
            fails, _until = config.get_lockout_state()
        except Exception:
            fails = 0
        _note_wrong_pin(config, fails + 1)
        return False, 'Current PIN is wrong.'
    error = validate_new_pin(current, new, confirm)
    if error is not None:
        return False, error
    stored: list = []
    try:
        import helper_client
        # Local hash path: config first (sync), then moved into the helper.
        helper_client.pin_change(
            config, current, new,
            lambda done, _retry, err: stored.append((done, err)))
    except Exception:
        stored = [(False, 'error')]
    if not stored or not stored[0][0]:
        return False, 'Could not save the new PIN.'
    try:
        config.clear_lockout_state()
    except Exception:
        pass
    return True, 'PIN changed.'


def change_device_password() -> tuple[bool, str]:
    """Open `passwd` in a terminal (same terminal table as updates)."""
    from update_checker import run_terminal_command
    return run_terminal_command('passwd')


class LockSection(settings_page._Section):
    """Change PIN plus change device password."""

    key = 'lock'
    title = 'Screen lock'

    def __init__(self) -> None:
        self._status = None
        self._pin_row = None
        self._pin_entries: dict = {}

    def build(self, win) -> list:
        self._pin_row = ui.nav_row('Change PIN', '',
                                   lambda r: self._open_pin(win, r))
        built = ui.group(self.title, [
            self._pin_row,
            ui.nav_row('Change device password…', 'Opens passwd in a terminal',
                       lambda _r: self._on_change_password(win))], note='')
        self._status = built[2]
        self.refresh(win)
        return built

    def refresh(self, win) -> None:
        try:
            has_pin = bool(win.config.pin_hash)
        except Exception:
            has_pin = False
        ui.set_subtitle(self._pin_row, 'PIN is set' if has_pin
                        else 'No PIN · swipe up unlocks')

    def _open_pin(self, win, anchor):
        box, self._pin_entries = ui.form_sheet(
            win, anchor, 'Change PIN',
            [('current', 'Current PIN', '', 'pin'),
             ('new', 'New PIN', '', 'pin'),
             ('confirm', 'New PIN again', '', 'pin')],
            'Change PIN', lambda values: self._on_change_pin(win, values))
        return box, self._pin_entries

    def _on_change_pin(self, win, values: dict) -> str | None:
        """Sheet commit: an error string keeps the sheet open."""
        def _later(ok: bool, message: str) -> None:
            self._say(message)
            if ok:
                self.refresh(win)
            try:
                win._show_status(message)
            except Exception:
                pass

        ok, message = change_pin(win.config, values.get('current', ''),
                                 values.get('new', ''),
                                 values.get('confirm', ''), on_async=_later)
        # Typed PINs never linger: every attempt clears the fields.
        for entry in (getattr(self, '_pin_entries', None) or {}).values():
            try:
                entry.set_text('')
            except Exception:
                pass
        if ok is None:
            # The helper answers async: the sheet closes, the verdict
            # lands in the section note and a status toast.
            self._say(message)
            return None
        if not ok:
            return message
        self._say(message)
        self.refresh(win)
        try:
            win._show_status('PIN changed.')
        except Exception:
            pass
        return None

    def _on_change_password(self, win) -> None:
        ok, detail = change_device_password()
        self._say('Password prompt opened in terminal.' if ok else detail)
        if ok:
            try:
                win._show_status('Password prompt opened in terminal.')
            except Exception:
                pass

    def _say(self, text: str) -> None:
        ui.say(self._status, text)
