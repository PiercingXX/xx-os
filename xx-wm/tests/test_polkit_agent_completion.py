"""The polkit agent completes every request exactly once (grant, deny,
Cancel, requester cancel), keeps the dialog open for a retry after a
wrong password, and implements the finish vfunc the C side calls."""
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).parent.parent / 'launcher' / 'src'


def _agent_module():
    polkit_agent = pytest.importorskip('polkit_agent')
    if not hasattr(polkit_agent, 'ShellListener'):
        pytest.skip('no PolkitAgent typelib')
    return polkit_agent


class FakeSession:
    """Mirrors PolkitAgent.Session: cancel() emits 'completed' (False)
    synchronously, like polkit_agent_session_cancel does."""

    def __init__(self, identity, cookie):
        self.identity, self.cookie = identity, cookie
        self.handlers = {}
        self.responses = []
        self.initiated = 0
        self.cancelled = 0

    def connect(self, signal, handler, data):
        self.handlers[signal] = (handler, data)

    def emit(self, signal, *args):
        handler, data = self.handlers[signal]
        handler(self, *args, data)

    def initiate(self):
        self.initiated += 1

    def response(self, secret):
        self.responses.append(secret)

    def cancel(self):
        self.cancelled += 1
        self.emit('completed', False)


class FakeDialog:
    def __init__(self, pending):
        self.pending = pending
        self.notes = []
        self.closed = 0

    def set_note(self, text, clear_entry=False):
        self.notes.append(text)

    def present(self):
        pass

    def close(self):
        self.closed += 1

    def tap_cancel(self):
        self.pending.cancel('dialog dismissed')

    def tap_authenticate(self, secret):
        polkit_agent = sys.modules['polkit_agent']
        polkit_agent._on_dialog_authenticate(self.pending, secret)


@pytest.fixture
def rig(monkeypatch):
    from gi.repository import Gio

    polkit_agent = _agent_module()
    sessions, dialogs, calls = [], [], []

    def make_session(identity, cookie):
        session = FakeSession(identity, cookie)
        sessions.append(session)
        return session

    def make_dialog(pending, _user):
        dialog = FakeDialog(pending)
        dialogs.append(dialog)
        return dialog

    monkeypatch.setattr(polkit_agent, 'new_session', make_session)
    monkeypatch.setattr(polkit_agent, 'show_auth_dialog', make_dialog)
    listener = polkit_agent.ShellListener()
    cancellable = Gio.Cancellable()

    def callback(source, task, user_data):
        calls.append(task.propagate_boolean())

    listener.do_initiate_authentication(
        'org.example.act', 'Do the thing', '', None, 'cookie-1',
        [SimpleNamespace(kind='user', get_uid=lambda: 0)], cancellable,
        callback, None)
    assert len(sessions) == 1 and sessions[0].initiated == 1
    return SimpleNamespace(listener=listener, sessions=sessions,
                           dialogs=dialogs, calls=calls,
                           cancellable=cancellable, module=polkit_agent)


def test_grant_completes_once(rig):
    rig.dialogs[0].tap_authenticate('right')
    rig.sessions[0].emit('completed', True)
    rig.dialogs[0].tap_cancel()
    rig.cancellable.cancel()
    assert rig.calls == [True]
    assert rig.dialogs[0].closed == 1
    assert rig.listener.pending_cookies() == []


def test_deny_completes_once(rig):
    rig.sessions[0].emit('completed', False)
    rig.dialogs[0].tap_cancel()
    assert rig.calls == [True]
    assert rig.dialogs[0].closed == 1
    assert len(rig.sessions) == 1


def test_cancel_completes_once(rig):
    rig.dialogs[0].tap_cancel()
    assert rig.sessions[0].cancelled == 1
    assert rig.calls == [True]
    assert rig.dialogs[0].closed == 1
    rig.cancellable.cancel()
    assert rig.calls == [True]
    assert rig.listener.pending_cookies() == []


def test_requester_cancel_closes_dialog_once(rig):
    rig.cancellable.cancel()
    assert rig.dialogs[0].closed == 1
    assert rig.sessions[0].cancelled == 1
    assert rig.calls == [True]
    rig.dialogs[0].tap_cancel()
    assert rig.calls == [True]
    assert rig.listener.pending_cookies() == []


def test_wrong_password_then_right_password_grants_once(rig):
    dialog = rig.dialogs[0]
    dialog.tap_authenticate('wrong')
    rig.sessions[0].emit('completed', False)
    assert rig.calls == []
    assert dialog.closed == 0
    assert dialog.notes[-1] == rig.module.WRONG_PASSWORD_NOTE
    assert len(rig.sessions) == 2
    retry = rig.sessions[1]
    assert retry.initiated == 1 and retry.cookie == 'cookie-1'
    dialog.tap_authenticate('right')
    assert retry.responses == ['right']
    assert rig.sessions[0].responses == ['wrong']
    retry.emit('completed', True)
    assert rig.calls == [True]
    assert dialog.closed == 1


def test_stale_session_signals_are_ignored(rig):
    rig.dialogs[0].tap_authenticate('wrong')
    rig.sessions[0].emit('completed', False)
    rig.sessions[0].emit('completed', True)
    assert rig.calls == []
    assert rig.dialogs[0].closed == 0


def test_finish_vfunc_is_implemented():
    polkit_agent = _agent_module()
    assert 'do_initiate_authentication_finish' in \
        polkit_agent.ShellListener.__dict__


_REAL_PATH = textwrap.dedent('''
    import os, sys
    sys.path.insert(0, sys.argv[1])
    import polkit_agent
    from gi.repository import Gio, Polkit

    class Session:
        def __init__(self, ident, cookie):
            self.handlers = {}
        def connect(self, signal, handler, data):
            self.handlers[signal] = (handler, data)
        def initiate(self):
            pass
        def cancel(self):
            handler, data = self.handlers['completed']
            handler(self, False, data)
        def response(self, secret):
            pass

    class Dialog:
        def set_note(self, *a, **k):
            pass
        def present(self):
            pass
        def close(self):
            pass

    sessions = []
    def new_session(ident, cookie):
        sessions.append(Session(ident, cookie))
        return sessions[-1]
    polkit_agent.new_session = new_session
    polkit_agent.show_auth_dialog = lambda pending, user: Dialog()

    def done(source, res, *_):
        print('finish', source.initiate_authentication_finish(res))

    listener = polkit_agent.ShellListener()
    ident = Polkit.UnixUser.new(os.getuid())
    listener.initiate_authentication(
        'org.example.act', 'Do the thing', '', Polkit.Details.new(),
        'cookie-1', [ident], None, done)
    handler, data = sessions[0].handlers['completed']
    handler(sessions[0], True, data)

    cancellable = Gio.Cancellable()
    listener.initiate_authentication(
        'org.example.act', 'Do the thing', '', Polkit.Details.new(),
        'cookie-2', [ident], cancellable, done)
    cancellable.cancel()
''')


def test_real_finish_path_does_not_crash():
    _agent_module()
    proc = subprocess.run(
        [sys.executable, '-c', _REAL_PATH, str(SRC)],
        capture_output=True, text=True, timeout=60,
        env={k: v for k, v in os.environ.items()
             if k != 'DBUS_SESSION_BUS_ADDRESS'})
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    assert proc.stdout.split('\n')[:2] == ['finish True', 'finish True'], \
        proc.stdout
