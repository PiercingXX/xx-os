"""In-shell polkit authentication agent.

With no agent in the session nothing can prompt for the device
password, so helper ``auth_admin_keep`` actions (system upgrade,
package removal) — and NetworkManager and Flatpak system prompts —
would fail silently. This module registers a ``PolkitAgent-1.0``
``Listener`` for the session at shell start and serves each request
with a themed xx_ui password dialog: the action description, the
user's name, a password entry with OSK attach, Cancel / Authenticate,
and an inline error on a wrong password. Cancel tears the session
down cleanly. The secret travels only from the entry to
``session.response`` and is cleared right after; it is never logged.

GI choice: a direct ``PolkitAgent.Listener`` subclass. The typelib
exposes the whole flow (``do_initiate_authentication`` receives the
async callback, ``PolkitAgent.Session`` handles the authority
round-trip), so no helper subprocess or agent process is needed — a
separate process would still need this same Listener plus IPC for
every prompt. Completion goes through the captured callback with an
own ``Gio.Task``; the async finish wrapper must never be called from
Python (it segfaults on this stack — completion still works because
the C side only needs the callback fired with a valid task).
"""
from __future__ import annotations

import re

import os
from collections.abc import Callable
from dataclasses import dataclass

OBJECT_PATH = '/org/xxwm/PolkitAgent'

_agent = None

try:
    import gi

    gi.require_version('PolkitAgent', '1.0')
    gi.require_version('Polkit', '1.0')
    from gi.repository import PolkitAgent as _AgentModule  # noqa: F401
    _HAVE_AGENT = True
except Exception:
    _HAVE_AGENT = False



def meaningful_prompt(text: object) -> str:
    """PAM's request text, unless it only says "Password:" again (the
    field already reads Device password). Other prompts (OTP, finger)
    are kept."""
    value = str(text or '').strip()
    if re.fullmatch(r'(?i)(\[[^\]]*\]\s*)?password(\s+for\s+\S+)?\s*:?', value):
        return ''
    return value

def _log():
    try:
        from shell_log import get_logger
        return get_logger('polkit')
    except Exception:
        return None


# ------------------------------------------------------------------ pure bits

def _is_user_identity(ident) -> bool:
    if getattr(ident, 'kind', None) == 'user':
        return True
    try:
        import gi as _gi

        _gi.require_version('Polkit', '1.0')
        from gi.repository import Polkit as _Polkit

        return isinstance(ident, _Polkit.UnixUser)
    except Exception:
        return False


def pick_identity(identities):
    """The first user identity in ``identities``, else None.

    Duck-typed so headless tests can pass light fakes; real
    ``Polkit.UnixUser`` objects match through the typelib.
    """
    for ident in identities or ():
        if _is_user_identity(ident):
            return ident
    return None


def display_name_for_uid(uid) -> str:
    """Login name for ``uid`` ('root', the device user, ...)."""
    try:
        import pwd

        name = pwd.getpwuid(int(uid)).pw_name
        if name:
            return str(name)
    except Exception:
        pass
    return 'user %s' % (uid,)


def agent_subject(session_id: str | None = None):
    """The subject to register the agent for: this login session, or
    this process when no session id is known (never raises past the
    caller's try)."""
    from gi.repository import Polkit

    sid = session_id or os.environ.get('XDG_SESSION_ID')
    if sid:
        return Polkit.UnixSession.new(sid)
    return Polkit.UnixProcess.new(os.getpid())


def base_listener_type():
    """The GI Listener base (import seam for tests)."""
    from gi.repository import PolkitAgent

    return PolkitAgent.Listener


def register_listener(listener, subject, object_path: str = OBJECT_PATH):
    """Register ``listener`` with the authority (import seam: False or
    an exception when no authority answers)."""
    from gi.repository import PolkitAgent

    return PolkitAgent.register_listener(listener, subject, object_path)


@dataclass
class Pending:
    """One in-flight authentication: the session, its dialog, and the
    one-shot completion that fires the captured async callback."""

    cookie: str
    action_id: str
    message: str
    finish: Callable[[bool], None]
    session: object = None
    dialog: object = None
    done: bool = False

    def cancel(self, _reason: str | None = None) -> None:
        """User dismissed (or the dialog went away): stop the session
        and complete the operation so polkitd never hangs."""
        if self.done:
            return
        self.done = True
        session, dialog = self.session, self.dialog
        self.session = None
        self.dialog = None
        if session is not None:
            try:
                session.cancel()
            except Exception:
                pass
        if dialog is not None:
            try:
                dialog.close()
            except Exception:
                pass
        try:
            self.finish(False)
        except Exception:
            pass


def _complete_auth(listener, callback, user_data) -> None:
    """Fire the captured InitiateAuthentication callback with a valid
    task (boolean True: the agent ran its course; the authority still
    decides the verdict from the cookie). Never raises."""
    try:
        from gi.repository import Gio

        task = Gio.Task.new(listener, None, None, None)
        task.return_boolean(True)
        callback(listener, task, user_data)
    except Exception:
        pass


# ------------------------------------------------------------------- dialog

def build_auth_dialog(action_text: str, user_name: str, on_authenticate,
                      on_cancel):
    """Themed password dialog body in its own window.

    Returns a namespace with ``present()``, ``set_note(text,
    clear_entry)`` and ``close()``. ``on_authenticate(secret)`` fires
    on Authenticate (or Enter); the entry is cleared on every commit.
    """
    from gi.repository import Gtk
    from xx_ui import widgets

    window = Gtk.Window(title='Authentication')
    # Phone rule: no minimize/maximize chrome; Cancel dismisses.
    window.set_decorated(False)
    window.add_css_class('xx-dialog')
    body = widgets.dialog_box('Authentication required')
    # Phone width floor on the content itself: a content-sized dialog
    # shrinks until its own title and buttons ellipsize (a window-level
    # minimum alone did not hold under the compositor); 312 keeps the
    # dialog inside 360 px with height left natural.
    body.set_size_request(312, -1)
    body.append(widgets.wrap_label(action_text or 'Authentication required.'))
    body.append(widgets.caption('AS %s' % (user_name or 'THIS USER')))

    entry = widgets.deferred_entry('Device password')
    entry.set_visibility(False)
    entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
    try:
        from osk import attach, set_visible

        attach(entry, on_show=lambda: set_visible(True),
               on_hide=lambda: set_visible(False))
    except Exception:
        pass
    body.append(entry)

    note = widgets.wrap_label('')
    note.set_visible(False)
    body.append(note)

    # Full-width stacked buttons (the entry-dialog pattern): two
    # half-width buttons truncate their own labels on a 312 px dialog.
    buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    cancel_btn = widgets.outline_button('Cancel', on_cancel)
    cancel_btn.set_hexpand(True)
    buttons.append(cancel_btn)

    def _commit(*_args) -> None:
        secret = entry.get_text()
        entry.set_text('')
        on_authenticate(secret)

    auth_btn = widgets.primary_button('Authenticate', _commit)
    auth_btn.set_hexpand(True)
    buttons.append(auth_btn)
    body.append(buttons)

    entry.connect('activate', _commit)
    window.set_child(body)

    def _on_close_request(_window) -> bool:
        # The window manager asked to close: dismiss the auth the same
        # way Cancel does, then let the close proceed (cancel is
        # idempotent, so the explicit close inside it is a no-op).
        on_cancel()
        return False

    window.connect('close-request', _on_close_request)

    from types import SimpleNamespace

    def _set_note(text: str, clear_entry: bool = False) -> None:
        note.set_text(text)
        note.set_visible(bool(text))
        if clear_entry:
            entry.set_text('')

    def _close() -> None:
        try:
            window.close()
        except Exception:
            pass

    def _present() -> None:
        try:
            window.present()
        except Exception:
            pass

    return SimpleNamespace(window=window, entry=entry, set_note=_set_note,
                           close=_close, present=_present)


def show_auth_dialog(pending: Pending, user_name: str):
    """Build, present and return the dialog for ``pending``."""
    dialog = build_auth_dialog(
        pending.message or pending.action_id, user_name,
        lambda secret: _on_dialog_authenticate(pending, secret),
        lambda: pending.cancel('dialog dismissed'))
    dialog.present()
    return dialog


def _on_dialog_authenticate(pending: Pending, secret: str) -> None:
    session = pending.session
    if session is None or pending.done:
        return
    try:
        session.response(secret)
    except Exception:
        pass


# ------------------------------------------------------------------ listener

if _HAVE_AGENT:
    class ShellListener(base_listener_type()):
        """The session's authentication agent: one PolkitAgent.Session
        per cookie, each with its own dialog."""

        def __init__(self) -> None:
            super().__init__()
            self._pending: dict = {}

        def pending_cookies(self) -> list:
            return sorted(self._pending)

        def _drop(self, cookie: str) -> None:
            pending = self._pending.pop(cookie, None)
            if pending is not None:
                pending.cancel('dropped')

        def _user_name(self, ident) -> str:
            try:
                return display_name_for_uid(ident.get_uid())
            except Exception:
                pass
            name = getattr(ident, 'name', None)
            return str(name) if name else 'This user'

        def do_initiate_authentication(
                self, action_id, message, icon_name, details, cookie,
                identities, cancellable, callback, user_data):
            def finish(_ok, _cb=callback, _ud=user_data) -> None:
                _complete_auth(self, _cb, _ud)
            pending = Pending(cookie=cookie, action_id=str(action_id or ''),
                              message=str(message or action_id or ''),
                              finish=finish)
            self._pending[cookie] = pending
            ident = pick_identity(identities)
            if ident is None:
                self._drop(cookie)
                return None
            from gi.repository import PolkitAgent

            try:
                session = PolkitAgent.Session.new(ident, cookie)
            except Exception:
                self._drop(cookie)
                return None
            pending.session = session
            try:
                session.connect('request', self._on_session_request,
                                pending)
                session.connect('show-info', self._on_session_info,
                                pending)
                session.connect('show-error', self._on_session_error,
                                pending)
                session.connect('completed', self._on_session_completed,
                                pending)
            except Exception:
                self._drop(cookie)
                return None
            if cancellable is not None:
                try:
                    cancellable.connect(
                        lambda _c, p=pending: p.cancel('cancelled'))
                except Exception:
                    pass
            try:
                pending.dialog = show_auth_dialog(pending,
                                                  self._user_name(ident))
            except Exception:
                self._drop(cookie)
                return None
            try:
                session.initiate()
            except Exception:
                self._drop(cookie)
            return None

        def _on_session_request(self, session, request_text, echo_on,
                                pending) -> None:
            dialog = pending.dialog
            if dialog is None:
                return
            try:
                note = meaningful_prompt(request_text)
                if note:
                    dialog.set_note(note)
                dialog.present()
            except Exception:
                pass

        def _on_session_info(self, session, text, pending) -> None:
            try:
                if pending.dialog is not None:
                    pending.dialog.set_note(str(text or ''))
            except Exception:
                pass

        def _on_session_error(self, session, text, pending) -> None:
            try:
                if pending.dialog is not None:
                    pending.dialog.set_note(
                        str(text or 'Authentication failed.'),
                        clear_entry=True)
            except Exception:
                pass

        def _on_session_completed(self, session, gained, pending) -> None:
            log = _log()
            if log is not None:
                try:
                    log.info('authentication %s for %s',
                             'granted' if gained else 'denied',
                             pending.action_id)
                except Exception:
                    pass
            self._pending.pop(pending.cookie, None)
            dialog, finish = pending.dialog, pending.finish
            pending.dialog = None
            pending.done = True
            if dialog is not None:
                try:
                    dialog.close()
                except Exception:
                    pass
            try:
                finish(True)
            except Exception:
                pass


def start_agent():
    """Register the session agent; None when there is no authority to
    register with (headless tests, minimal images). Never raises."""
    global _agent
    log = _log()
    if not _HAVE_AGENT:
        if log is not None:
            log.info('polkit agent unavailable (no PolkitAgent typelib)')
        return None
    try:
        listener = ShellListener()
        subject = agent_subject()
        if not register_listener(listener, subject, OBJECT_PATH):
            raise RuntimeError('authority refused the agent')
    except Exception as err:
        if log is not None:
            log.warning('polkit agent not started: %s', err)
        return None
    _agent = listener
    if log is not None:
        log.info('polkit agent registered for the session')
    return listener


def stop_agent() -> None:
    """Unregister the session agent; never raises."""
    global _agent
    listener, _agent = _agent, None
    if listener is None:
        return
    try:
        listener.unregister()
    except Exception:
        pass


__all__ = ['OBJECT_PATH', 'Pending', 'agent_subject', 'base_listener_type',
           'build_auth_dialog', 'display_name_for_uid', 'pick_identity',
           'register_listener', 'show_auth_dialog', 'start_agent',
           'stop_agent'] + (['ShellListener'] if _HAVE_AGENT else [])
