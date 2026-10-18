"""One wizard and one shell per process, however often the app is
activated, and the wizard completes once however often Finish is tapped.

On the Librem, repeated activations stacked ~16 first-boot wizards: the
old guard used active_window, which layer-shell windows never become."""
import types


class _Win:
    def __init__(self):
        self.presented = 0

    def present(self):
        self.presented += 1

    def set_application(self, _app):
        pass


def test_reactivation_presents_the_existing_wizard(monkeypatch):
    import main
    built = []

    def wizard_factory(on_complete):
        w = _Win()
        built.append(w)
        return w

    fake_cls = type('W', (), {'is_needed': staticmethod(lambda: True),
                              '__new__': lambda cls, on_complete: wizard_factory(on_complete)})
    monkeypatch.setattr(main, 'FirstBootWizard', fake_cls)
    for name in ('drop_portal_opt_out', 'IPCServer', '_setup_logind',
                 'NotificationDaemon'):
        monkeypatch.setattr(main, name, lambda *a, **k: None, raising=False)
    import font_theme
    monkeypatch.setattr(font_theme, 'apply_global_font', lambda *_a: None)
    app = types.SimpleNamespace(
        _shell=None, _wizard=None,
        props=types.SimpleNamespace(active_window=None),
        _on_ipc_command=None, _on_sleep=None,
        _on_notification=None, _on_notification_closed=None,
        _wizard_done=lambda: None, _show_shell=lambda: None)
    main.XXWMApplication.do_activate(app)
    main.XXWMApplication.do_activate(app)
    main.XXWMApplication.do_activate(app)
    assert len(built) == 1
    assert built[0].presented == 3   # first show + two re-activations


def test_show_shell_twice_reuses_the_shell():
    import main
    shell = _Win()
    app = types.SimpleNamespace(_shell=shell)
    main.XXWMApplication._show_shell(app)
    assert shell.presented == 1      # presented, not rebuilt


def test_finish_completes_once(monkeypatch):
    import first_boot
    calls = []
    closes = []
    monkeypatch.setattr(first_boot.GLib, 'idle_add', lambda fn: closes.append(fn))
    wiz = types.SimpleNamespace(_on_complete=lambda: calls.append(1),
                                close=lambda: None)
    for _ in range(5):
        first_boot.FirstBootWizard._finish_tour(wiz)
    assert calls == [1]
    assert len(closes) == 1
