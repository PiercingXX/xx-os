"""Dialer call placement (P2-D, W1 telephony).

Dialing prefers Voice.CreateCall + Call.Start over D-Bus and falls back
to the two-step mmcli sequence — create the call object, then start it
via the returned object path selected with ``-o`` (upstream mmcli has no
``--voice-call`` verb; verified against cli/mmcli-common.c and the
mmcli(8) CALL OPTIONS section) against the D-Bus-resolved modem path.
The shell has no dialer window of its own: the Phone app is xx-dialer.
"""
import subprocess
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


def _install_fake_gi() -> None:
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.Error = type('Error', (Exception,), {})
    glib.idle_add = lambda *a, **k: None
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None

    gdk = types.ModuleType('gi.repository.Gdk')
    gdk.Display = types.SimpleNamespace(get_default=lambda: None)

    gtk = types.ModuleType('gi.repository.Gtk')
    gtk.Window = type('Window', (), {})

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')

    def _require_version(namespace, version):
        if namespace != 'Gtk':
            raise ValueError(f'{namespace} not available')
    gi_mod.require_version = _require_version
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib
    sys.modules['gi.repository.Gdk'] = gdk
    sys.modules['gi.repository.Gtk'] = gtk


_install_fake_gi()

import modem_monitor  # noqa: E402  (needs fake gi modules installed first)
import dialer  # noqa: E402  (needs fake gi modules installed first)


class FakeLabel:
    def __init__(self, text=''):
        self.text = text

    def set_text(self, text):
        self.text = text

    def get_text(self):
        return self.text


class SyncThread:
    """Runs the worker inline so tests are deterministic."""

    def __init__(self, target=None, args=(), daemon=False):
        self.target = target
        self.args = args

    def start(self):
        self.target(*self.args)


def _script_mmcli(monkeypatch, responses):
    calls = []

    def _run(args):
        calls.append(list(args))
        ok, out = responses.pop(0)
        return ok, out

    monkeypatch.setattr(dialer, '_run_mmcli', _run)
    return calls


def _flush_idle(monkeypatch):
    glib = dialer.GLib
    pending = []

    def idle_add(cb, *args):
        pending.append((cb, args))
        return len(pending)
    monkeypatch.setattr(glib, 'idle_add', idle_add)
    return lambda: [cb(*args) for cb, args in list(pending)]


def _script_dbus_call(monkeypatch, result, created=()):
    def _place(number, created_calls=None):
        if created_calls is not None:
            created_calls.extend(created)
        return result

    monkeypatch.setattr(modem_monitor, 'voice_create_and_start', _place)


class TestPlaceCall:
    def test_dbus_success_never_touches_mmcli(self, monkeypatch):
        _script_dbus_call(
            monkeypatch, (True, '/org/freedesktop/ModemManager1/Call/3'))
        calls = _script_mmcli(monkeypatch, [])
        assert dialer.place_call('5551234567') == \
            (True, '/org/freedesktop/ModemManager1/Call/3')
        assert calls == []

    def test_dbus_failure_falls_back_with_resolved_modem(self, monkeypatch):
        _script_dbus_call(monkeypatch, (False, 'bus wedged'))
        monkeypatch.setattr(dialer, '_resolved_modem',
                            lambda: '/org/freedesktop/ModemManager1/Modem/2')
        calls = _script_mmcli(monkeypatch, [
            (True, 'Successfully created new call: /org/freedesktop/ModemManager1/Call/1\n'),
            (True, 'successfully started the call\n'),
        ])
        assert dialer.place_call('5551234567')[0] is True
        assert calls == [
            ['-m', '/org/freedesktop/ModemManager1/Modem/2',
             '--voice-create-call=number=5551234567'],
            ['-m', '/org/freedesktop/ModemManager1/Modem/2',
             '-o', '/org/freedesktop/ModemManager1/Call/1', '--start'],
        ]

    def test_dbus_exception_still_falls_back(self, monkeypatch):
        def _boom(number, created_calls=None):
            raise RuntimeError('transport exploded')

        monkeypatch.setattr(modem_monitor, 'voice_create_and_start', _boom)
        monkeypatch.setattr(dialer, '_resolved_modem', lambda: '0')
        calls = _script_mmcli(monkeypatch, [
            (True, '/org/freedesktop/ModemManager1/Call/2\n'),
            (True, 'started\n'),
        ])
        assert dialer.place_call('5551234567')[0] is True
        assert len(calls) == 2

    def test_create_failure_surfaces_stderr(self, monkeypatch):
        _script_dbus_call(monkeypatch, (False, 'bus down'))
        monkeypatch.setattr(dialer, '_resolved_modem', lambda: '0')
        calls = _script_mmcli(monkeypatch, [
            (False, "error: couldn't find modem\n"),
        ])
        ok, out = dialer.place_call('5551234567')
        assert (ok, len(calls)) == (False, 1)
        assert "couldn't find modem" in out

    def test_missing_call_path_surfaces(self, monkeypatch):
        _script_dbus_call(monkeypatch, (False, 'bus down'))
        monkeypatch.setattr(dialer, '_resolved_modem', lambda: '0')
        _script_mmcli(monkeypatch, [(True, 'unexpected output\n')])
        assert dialer.place_call('5551234567') == \
            (False, 'modem returned no call path')

    def test_start_failure_surfaces_and_deletes_the_call(self, monkeypatch):
        _script_dbus_call(monkeypatch, (False, 'bus down'))
        monkeypatch.setattr(dialer, '_resolved_modem', lambda: '0')
        calls = _script_mmcli(monkeypatch, [
            (True, '/org/freedesktop/ModemManager1/Call/2\n'),
            (False, 'error: call state change failed\n'),
            (True, 'successfully deleted call\n'),
        ])
        ok, out = dialer.place_call('5551234567')
        assert ok is False
        assert out == 'error: call state change failed'
        assert calls[-1] == [
            '-m', '0',
            '--voice-delete-call=/org/freedesktop/ModemManager1/Call/2']

    def test_dbus_start_failure_never_creates_a_second_call(
            self, monkeypatch):
        """ModemManager took the D-Bus call and refused to start it (the
        call is deleted there). mmcli is a client of the same daemon: a
        second call could only fail the same way, or leave two calls."""
        _script_dbus_call(
            monkeypatch, (False, 'Start failed: no network'),
            created=['/org/freedesktop/ModemManager1/Call/7'])
        monkeypatch.setattr(dialer, '_resolved_modem', lambda: '0')
        calls = _script_mmcli(monkeypatch, [])
        assert dialer.place_call('5551234567') == (
            False, 'Start failed: no network')
        assert calls == []

    def test_missing_binary_degrades_gracefully(self, monkeypatch):
        _script_dbus_call(monkeypatch, (False, 'bus down'))
        monkeypatch.setattr(dialer, '_resolved_modem', lambda: '0')

        def _boom(*a, **k):
            raise FileNotFoundError('mmcli')

        monkeypatch.setattr(dialer.subprocess, 'run', _boom)
        assert dialer.place_call('5551234567') == (False, 'mmcli not installed')


class TestRunMmcli:
    def test_success_returns_stdout(self, monkeypatch):
        proc = subprocess.CompletedProcess(['mmcli'], 0,
                                           stdout='ok path /org/x\n', stderr='')
        monkeypatch.setattr(dialer.subprocess, 'run', lambda *a, **k: proc)
        assert dialer._run_mmcli(['-m', '0']) == (True, 'ok path /org/x\n')

    def test_nonzero_returns_stderr_detail(self, monkeypatch):
        proc = subprocess.CompletedProcess(['mmcli'], 4, stdout='', stderr='boom\n')
        monkeypatch.setattr(dialer.subprocess, 'run', lambda *a, **k: proc)
        assert dialer._run_mmcli(['-m', '0']) == (False, 'boom')

    def test_nonzero_without_stderr_reports_status(self, monkeypatch):
        proc = subprocess.CompletedProcess(['mmcli'], 9, stdout='', stderr='')
        monkeypatch.setattr(dialer.subprocess, 'run', lambda *a, **k: proc)
        assert dialer._run_mmcli([]) == (False, 'mmcli exited with status 9')


class TestPhoneIsTheApp:
    """xx-dialer is the Phone app; the shell keeps only place_call and the
    emergency helpers, and its own SMS window is gone (TxxT owns texts)."""

    def test_no_dialer_or_sms_window(self):
        src = Path(dialer.__file__).parent
        assert not hasattr(dialer, 'Dialer')
        assert not (src / 'sms.py').exists()
        assert callable(dialer.place_call)
        assert callable(dialer.place_emergency_call)

    def test_phone_app_id_prefers_xx_dialer(self):
        import default_layout
        installed = {'org.gnome.Calls.desktop', 'io.piercingxx.XXDialer.desktop'}

        def find(app_id):
            for cand in (app_id, app_id + '.desktop'):
                if cand in installed:
                    return cand
            return None
        assert default_layout.phone_app_id(find) == (
            'io.piercingxx.XXDialer.desktop')
        assert default_layout.phone_app_id(lambda _a: None) is None

    def test_open_dialer_launches_the_phone_app(self):
        src =(Path(dialer.__file__).parent / 'window.py').read_text(
            encoding='utf-8')
        body = src.split('def _open_dialer(self)')[1].split('\n    def ')[0]
        assert 'phone_app_id' in body
        assert '_launch_app_id' in body
        assert 'Dialer(' not in body
