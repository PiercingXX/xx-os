"""Telephony while the first-boot wizard is up (review 2026-11-11, P1.2).

The modem monitor lived only on the shell window, which is built after
the wizard, so a call during setup rang nowhere. main.WizardTelephony
runs the same CallWiring paths from the start of setup and hands over to
the shell window's monitor when the wizard finishes.

No real ModemManager monitor is built here (it would reach the host's
system bus): modem_monitor.ModemMonitor is replaced with a recorder.
"""
import types

import pytest


class _FakeMonitor:
    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False
        _FakeMonitor.instances.append(self)

    def close(self):
        self.closed = True

    def calls(self):
        return []


class _Surface:
    def __init__(self):
        self.events = []

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return lambda *a, **k: self.events.append((name, a))


@pytest.fixture
def main_mod(monkeypatch, tmp_path):
    try:
        import main
    except Exception as exc:  # pragma: no cover - env without GTK
        pytest.skip('main not importable: %s' % exc)
    import modem_monitor
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    _FakeMonitor.instances = []
    monkeypatch.setattr(modem_monitor, 'ModemMonitor', _FakeMonitor)
    return main


def _host(main_mod, lock=None):
    app = types.SimpleNamespace(_wizard_lock=lock)
    return main_mod.WizardTelephony(app), app


class TestWizardTelephony:
    def test_monitor_wired_to_the_call_paths(self, main_mod):
        host, _app = _host(main_mod)
        (monitor,) = _FakeMonitor.instances
        for key in ('on_incoming', 'on_answered', 'on_ended',
                    'on_calls_changed', 'on_sms'):
            assert callable(monitor.kwargs[key]), key

    def test_incoming_call_rings_above_the_resumed_lock(
            self, main_mod, monkeypatch):
        import call_screen
        lock = types.SimpleNamespace(get_visible=lambda: True)
        host, _app = _host(main_mod, lock=lock)
        ui, bar = _Surface(), _Surface()
        host._ensure_call_surfaces = lambda: (ui, bar)
        monkeypatch.setattr(call_screen, 'consult_check_number',
                            lambda number, cb: cb('ring', ''))
        assert host.is_locked() is True
        _FakeMonitor.instances[0].kwargs['on_incoming'](
            'Bob', '+15550001111')
        names = [name for name, _a in ui.events]
        assert names[0] == 'show_incoming'
        assert 'present' in names
        assert 'start_ringing' in names

    def test_not_locked_without_the_resume_lock(self, main_mod):
        host, _app = _host(main_mod)
        assert host.is_locked() is False

    def test_close_hands_over(self, main_mod):
        host, _app = _host(main_mod)
        ui, bar = _Surface(), _Surface()
        host._call_ui, host._call_bar = ui, bar
        host.close()
        assert _FakeMonitor.instances[0].closed is True
        assert [n for n, _a in ui.events] == ['end_call', 'destroy']
        assert [n for n, _a in bar.events] == ['hide_bar', 'destroy']
        assert host._call_ui is None and host._call_bar is None

    def test_sms_left_on_the_modem_and_app_started(
            self, main_mod, monkeypatch):
        import modem_monitor
        started = []
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda name, *a: False)
        monkeypatch.setattr(modem_monitor, 'bus_name_activatable',
                            lambda name: name == modem_monitor.TXXT_BUS_NAME)
        monkeypatch.setattr(modem_monitor, 'start_bus_name', started.append)
        host, _app = _host(main_mod)
        assert host._on_sms('+15550001111', 'hello') is False
        assert started == [modem_monitor.TXXT_BUS_NAME]


class TestAppLifecycle:
    def _app(self, main_mod, monkeypatch, order):
        app = types.SimpleNamespace(_wizard=None, _wizard_telephony=None,
                                    _shell=None)
        for name in ('_start_wizard_telephony', '_stop_wizard_telephony',
                     '_wizard_done'):
            setattr(app, name, types.MethodType(
                getattr(main_mod.XXWMApplication, name), app))
        app._show_shell = lambda: order.append(
            ('shell', app._wizard_telephony))
        return app

    def test_started_once_and_stopped_before_the_shell(
            self, main_mod, monkeypatch):
        order = []
        app = self._app(main_mod, monkeypatch, order)
        app._start_wizard_telephony()
        app._start_wizard_telephony()
        assert len(_FakeMonitor.instances) == 1
        app._wizard_done()
        assert _FakeMonitor.instances[0].closed is True
        assert order == [('shell', None)]

    def test_activation_with_wizard_starts_telephony(
            self, main_mod, monkeypatch):
        calls = []
        fake_cls = type('W', (), {'is_needed': staticmethod(lambda: True)})
        monkeypatch.setattr(main_mod, 'FirstBootWizard', fake_cls)
        for name in ('drop_portal_opt_out', 'IPCServer', '_setup_logind',
                     'NotificationDaemon'):
            monkeypatch.setattr(main_mod, name, lambda *a, **k: None)
        import config
        import font_theme
        monkeypatch.setattr(font_theme, 'apply_global_font', lambda *_a: None)
        monkeypatch.setattr(config, 'ShellConfig', lambda: types.SimpleNamespace(
            font_family='', pin_hash=None, data={}))
        app = types.SimpleNamespace(
            _shell=None, _wizard=None,
            props=types.SimpleNamespace(active_window=None),
            _on_ipc_command=None, _on_sleep=None,
            _on_notification=None, _on_notification_closed=None,
            hold=lambda: None, release=lambda: None,
            _on_late_helper_pin=lambda: None,
            _start_wizard_telephony=lambda: calls.append('telephony'),
            _start_first_boot=lambda: calls.append('wizard'))
        app._gate_startup = types.MethodType(
            main_mod.XXWMApplication._gate_startup, app)
        main_mod.XXWMApplication.do_activate(app)
        assert calls == ['telephony', 'wizard']
