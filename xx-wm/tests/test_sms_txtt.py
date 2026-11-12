"""SMS deferral to the device's messaging app (Chatty and TxxT).

When a full messaging app owns its bus name it posts its own
notifications, so the shell must stay quiet: the TxxT daemon
(io.piercingxx.TxxT.Daemon) on XX-WM devices, checked first, then Chatty
(sm.puri.Chatty). Only when neither owns the bus does the shell post
its own SMS notification.
"""
import sys
import types
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import modem_monitor  # noqa: E402
from call_wiring import CallWiring  # noqa: E402


def _win(**overrides):
    """Bare CallWiring window namespace with an SMS recording app."""
    posted: list[tuple] = []

    class _App:
        def _on_notification(self, notif_id, app, summary, body, *rest):
            posted.append((notif_id, app, summary, body))

    win = types.SimpleNamespace(
        _txtt_owner_warned=False,
        _chatty_owner_warned=False,
        _sms_notif_seq=0,
        get_application=lambda: _App(),
    )
    for key, value in overrides.items():
        setattr(win, key, value)
    win._posted = posted
    return win


def _owners(mapping: dict) -> callable:
    def _has_owner(name: str, timeout_ms: int = 500) -> bool:
        return bool(mapping.get(name, False))

    return _has_owner


class TestSmsDeferral:
    def test_txtt_owner_stays_quiet(self, monkeypatch):
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            _owners({modem_monitor.TXXT_BUS_NAME: True,
                                     modem_monitor.CHATTY_BUS_NAME: True}))
        win = _win()
        CallWiring(win)._on_sms_received('+15550001111', 'hello')
        assert win._posted == []
        assert win._txtt_owner_warned is True
        assert win._chatty_owner_warned is False

    def test_chatty_owner_stays_quiet(self, monkeypatch):
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            _owners({modem_monitor.CHATTY_BUS_NAME: True}))
        win = _win()
        CallWiring(win)._on_sms_received('+15550001111', 'hello')
        assert win._posted == []
        assert win._chatty_owner_warned is True
        assert win._txtt_owner_warned is False

    def test_txtt_checked_before_chatty(self, monkeypatch):
        """Both present: TxxT wins (it owns SMS on XX-WM devices)."""
        seen: list[str] = []

        def _has_owner(name: str, timeout_ms: int = 500) -> bool:
            seen.append(name)
            return True

        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner', _has_owner)
        win = _win()
        CallWiring(win)._on_sms_received('+15550001111', 'hello')
        assert seen and seen[0] == modem_monitor.TXXT_BUS_NAME
        assert win._posted == []

    def test_no_owner_posts_shell_notification(self, monkeypatch):
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            _owners({}))
        win = _win()
        CallWiring(win)._on_sms_received('+15550001111', 'hello')
        assert len(win._posted) == 1
        _notif_id, app, _summary, body = win._posted[0]
        assert app == 'Messages'
        assert body == 'hello'
        assert win._txtt_owner_warned is False
        assert win._chatty_owner_warned is False

    def test_txtt_bus_name(self):
        assert modem_monitor.TXXT_BUS_NAME == 'io.piercingxx.TxxT.Daemon'


class TestActivatableOwner:
    """TxxT or Chatty installed but not running yet still own the SMS:
    the shell starts the app (it reads the modem store on startup) and
    posts nothing, instead of notifying once itself and once more when
    the app comes up."""

    @staticmethod
    def _activatable(monkeypatch, names):
        started: list[str] = []
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner', _owners({}))
        monkeypatch.setattr(modem_monitor, 'bus_name_activatable',
                            lambda name: name in names)
        monkeypatch.setattr(modem_monitor, 'start_bus_name', started.append)
        return started

    def test_activatable_txxt_is_started_not_duplicated(self, monkeypatch):
        started = self._activatable(monkeypatch, {modem_monitor.TXXT_BUS_NAME})
        win = _win()
        handled = CallWiring(win)._on_sms_received('+15550001111', 'hello')
        assert win._posted == []
        assert started == [modem_monitor.TXXT_BUS_NAME]
        assert win._txtt_owner_warned is True
        assert handled is False

    def test_activatable_chatty_when_no_txxt(self, monkeypatch):
        started = self._activatable(monkeypatch,
                                    {modem_monitor.CHATTY_BUS_NAME})
        win = _win()
        CallWiring(win)._on_sms_received('+15550001111', 'hello')
        assert win._posted == []
        assert started == [modem_monitor.CHATTY_BUS_NAME]

    def test_running_owner_is_not_restarted(self, monkeypatch):
        started = self._activatable(monkeypatch, {modem_monitor.TXXT_BUS_NAME})
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            _owners({modem_monitor.TXXT_BUS_NAME: True}))
        win = _win()
        CallWiring(win)._on_sms_received('+15550001111', 'hello')
        assert started == [] and win._posted == []

    def test_shell_post_reports_handled(self, monkeypatch):
        started = self._activatable(monkeypatch, set())
        win = _win()
        handled = CallWiring(win)._on_sms_received('+15550001111', 'hello')
        assert len(win._posted) == 1 and started == []
        assert handled is True


class TestSmsNumberDisplay:
    """The Messages banner summary reads through xx_phone; saved names win."""

    def test_unknown_number_summary_uses_xx_phone(self, monkeypatch):
        import contacts
        import xx_phone
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            _owners({}))
        monkeypatch.setattr(
            contacts, 'shared_book',
            lambda config=None: types.SimpleNamespace(lookup_number=lambda n: None))
        win = _win()
        number = '+16145550177'
        CallWiring(win)._on_sms_received(number, 'hello')
        assert len(win._posted) == 1
        assert win._posted[0][2] == xx_phone.display(
            number, xx_phone.user_country("", ""))

    def test_saved_name_wins_in_summary(self, monkeypatch):
        import contacts
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            _owners({}))
        monkeypatch.setattr(
            contacts, 'shared_book',
            lambda config=None: types.SimpleNamespace(lookup_number=lambda n: 'Mom'))
        win = _win()
        CallWiring(win)._on_sms_received('+16145550177', 'hello')
        assert win._posted[0][2] == 'Mom'
