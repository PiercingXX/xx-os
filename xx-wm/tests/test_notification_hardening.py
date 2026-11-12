"""Notification pipeline hardening: real GLib Variants where the fakes
used to be more forgiving than the library.

Modules are loaded fresh from launcher/src under private names, so a
sibling file that imported them against fake gi seams cannot leak in.
"""
import importlib.util
import sys
from pathlib import Path

import pytest


def _glib():
    # Resolved at test time: sibling files stub gi during collection and
    # conftest puts the real modules back before the first test runs.
    from gi.repository import GLib
    return GLib


def Variant(*args):
    return _glib().Variant(*args)

SRC = Path(__file__).parent.parent / 'launcher' / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f'_hardening_{name}', SRC / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _QueueGLib:
    """Module-level GLib seam: idle/timeout callbacks queue until drained."""

    Error = Exception

    def __init__(self) -> None:
        self.idle: list = []
        self.timeouts: dict = {}
        self._next = 100

    def idle_add(self, callback, *args):
        self.idle.append((callback, args))
        return len(self.idle)

    def run_idle(self) -> None:
        queued, self.idle = self.idle, []
        for callback, args in queued:
            callback(*args)

    def timeout_add(self, ms, callback, *args):
        self._next += 1
        self.timeouts[self._next] = (ms, callback, args)
        return self._next

    def source_remove(self, tid):
        self.timeouts.pop(tid, None)
        return True

    @staticmethod
    def Variant(*args):
        return _glib().Variant(*args)


class _Bus:
    def __init__(self) -> None:
        self.signals: list = []

    def emit_signal(self, _dest, _path, _iface, name, params):
        self.signals.append((name, params.unpack()))


def _daemon(on_notify=None, on_close=None):
    mod = _load('notif_daemon')
    glib = _QueueGLib()
    mod.GLib = glib
    daemon = object.__new__(mod.NotificationDaemon)
    daemon._on_notify = on_notify or (lambda *a: None)
    daemon._on_close = on_close or (lambda _i: None)
    daemon._bus = _Bus()
    daemon._reg_id = 0
    daemon._name_id = 0
    daemon._next_id = 1
    daemon._owns_name = True
    daemon._expire_timers = {}
    daemon._notif_hints = {}
    daemon._issued_ids = {}
    daemon._takes_actions = None
    return mod, daemon, glib


def _notify(summary='S', body='B', hints=None, expire=-1, replaces=0,
            actions=None):
    return Variant('(susssasa{sv}i)', (
        'App', replaces, '', summary, body, actions or [],
        hints or {}, expire))


class TestImageDataStaysPacked:
    def test_image_data_never_unpacked(self, monkeypatch):
        seen: list = []
        cls = _glib().Variant
        real_unpack = cls.unpack

        def recording_unpack(self):
            seen.append(self.get_type_string())
            return real_unpack(self)

        received: list = []
        _mod, daemon, glib = _daemon(
            on_notify=lambda *args: received.append(args))
        pixels = bytes(256 * 256 * 4)
        params = _notify(hints={
            'image-data': Variant(
                '(iiibiiay)', (256, 256, 1024, True, 8, 4, pixels)),
            'icon_data': Variant(
                '(iiibiiay)', (1, 1, 4, True, 8, 4, bytes(4))),
            'category': Variant('s', 'im'),
            'urgency': Variant('y', 1),
        })
        monkeypatch.setattr(cls, 'unpack', recording_unpack)
        daemon._handle_notify(params)
        monkeypatch.setattr(cls, 'unpack', real_unpack)
        glib.run_idle()
        assert '(iiibiiay)' not in seen
        assert 'ay' not in seen
        hints = received[0][5]
        assert hints == {'category': 'im', 'urgency': 1}
        assert received[0][2:4] == ('S', 'B')


class _Invocation:
    def __init__(self) -> None:
        self.value = 'unset'
        self.error = None

    def return_value(self, value):
        self.value = value

    def return_error_literal(self, _domain, _code, message):
        self.error = message


def _call(daemon, sender, method, params):
    inv = _Invocation()
    daemon._on_method_call(daemon._bus, sender, '/org/freedesktop/'
                           'Notifications', 'org.freedesktop.Notifications',
                           method, params, inv)
    return inv


def _issued(daemon, glib, sender):
    inv = _call(daemon, sender, 'Notify', _notify())
    glib.run_idle()
    return inv.value.unpack()[0]


class TestCloseOnlyOwnIds:
    def test_client_closes_its_own_id(self):
        closed: list = []
        _mod, daemon, glib = _daemon(on_close=closed.append)
        nid = _issued(daemon, glib, ':1.10')
        inv = _call(daemon, ':1.10', 'CloseNotification',
                    Variant('(u)', (nid,)))
        glib.run_idle()
        assert inv.error is None
        assert closed == [nid]
        assert daemon._bus.signals[-1] == ('NotificationClosed', (nid, 3))

    def test_other_client_cannot_close(self):
        closed: list = []
        _mod, daemon, glib = _daemon(on_close=closed.append)
        nid = _issued(daemon, glib, ':1.10')
        inv = _call(daemon, ':1.99', 'CloseNotification',
                    Variant('(u)', (nid,)))
        glib.run_idle()
        assert inv.error is None
        assert closed == []
        assert daemon._bus.signals == []

    def test_never_issued_and_shell_rows_cannot_be_closed(self):
        closed: list = []
        _mod, daemon, glib = _daemon(on_close=closed.append)
        for nid in (4242, 999904, 1_900_000_001):
            _call(daemon, ':1.10', 'CloseNotification',
                  Variant('(u)', (nid,)))
        glib.run_idle()
        assert closed == []
        assert daemon._bus.signals == []

    def test_other_client_cannot_replace(self):
        _mod, daemon, glib = _daemon()
        nid = _issued(daemon, glib, ':1.10')
        inv = _call(daemon, ':1.99', 'Notify', _notify(replaces=nid))
        assert inv.value.unpack()[0] != nid


class TestExpiryRemovesRow:
    def test_expiry_closes_like_the_app_heard(self):
        closed: list = []
        _mod, daemon, glib = _daemon(on_close=closed.append)
        nid = daemon._handle_notify(_notify(expire=5000), ':1.10')
        glib.run_idle()
        assert daemon._expire_timers
        daemon._expire_notification(nid)
        glib.run_idle()
        assert closed == [nid]
        assert daemon._bus.signals == [('NotificationClosed', (nid, 1))]
        assert nid not in daemon._issued_ids
        assert nid not in daemon._notif_hints


# -- main.py routing ---------------------------------------------------------

def _app():
    import main
    app = main.XXWMApplication()
    app._maybe_banner = lambda *args, **kwargs: None
    app._usage_tracker = None
    return app


def _shell(**overrides):
    import types
    config = types.SimpleNamespace(
        is_app_muted=lambda app_id: False, sound_notifications=False)
    shown: list = []
    shade = types.SimpleNamespace(
        add_notification=lambda *args: shown.append(args),
        dismiss=lambda *args, **kwargs: None)
    ns = dict(config=config, focus_state=None, dnd_state=None,
              nope_state=None, _ensure_shade=lambda: shade, _shade=shade,
              shown=shown)
    ns.update(overrides)
    return types.SimpleNamespace(**ns)


class _Records:
    def __init__(self) -> None:
        import logging
        self.records: list = []
        self.handler = logging.Handler(logging.DEBUG)
        self.handler.emit = self.records.append

    def __enter__(self):
        import logging
        self.logger = logging.getLogger('piercing.main')
        self.level = self.logger.level
        self.logger.setLevel(logging.DEBUG)
        self.logger.addHandler(self.handler)
        return self

    def __exit__(self, *exc):
        self.logger.removeHandler(self.handler)
        self.logger.setLevel(self.level)


class TestNotificationLogging:
    def test_log_carries_id_and_app_only(self):
        app = _app()
        app._shell = _shell()
        with _Records() as rec:
            app._on_notification(
                7, 'Messages', 'Your code is 482913', 'from the bank',
                'org.example.Messages', {})
        text = '\n'.join(r.getMessage() for r in rec.records)
        assert 'notification 7 from Messages' in text
        assert '482913' not in text
        assert 'from the bank' not in text


class _Dnd:
    def __init__(self, active=True):
        self.active = active

    def is_active(self):
        return self.active


def _focus(apps=(), enabled=True):
    import types
    from focus_mode import FocusState
    config = types.SimpleNamespace(
        data={'focus_enabled': enabled, 'focus_apps': list(apps)},
        save=lambda: None)
    return FocusState(config)


class TestOnlyAlarmsPassDndAndFocus:
    def _route(self, hints, entry='org.example.Chat', focus=None):
        app = _app()
        banners: list = []
        app._maybe_banner = lambda *a, **k: banners.append(k)
        app._shell = _shell(dnd_state=_Dnd(), focus_state=focus)
        app._on_notification(3, 'Chat', 'S', 'B', entry, hints)
        return app, banners

    def test_critical_urgency_alone_is_not_an_alarm(self):
        _app_, banners = self._route({'urgency': 2})
        assert banners[0]['is_alarm'] is False

    def test_alarm_category_is_an_alarm(self):
        for category in ('alarm', 'alarm.ringing'):
            _app_, banners = self._route(
                {'category': category, 'urgency': 2})
            assert banners[0]['is_alarm'] is True

    def test_lookalike_categories_are_not(self):
        for category in ('alarmist', 'im.alarm', 'x-alarm'):
            _app_, banners = self._route({'category': category})
            assert banners[0]['is_alarm'] is False

    def test_critical_from_a_paused_app_is_held(self):
        focus = _focus(apps=['org.example.Chat'])
        app, banners = self._route({'urgency': 2}, focus=focus)
        assert banners == []
        assert len(app._held_notifications) == 1


class TestFocusHolds:
    def _app(self, focus, entries=()):
        import types
        app = _app()
        app._shell = _shell(focus_state=focus)
        app._shell.config.label_for = lambda _app_id, name: name
        app._shell.app_index = types.SimpleNamespace(entries=list(entries))
        return app

    def test_withdrawn_while_held_is_never_delivered(self):
        focus = _focus(apps=['org.example.Chat'])
        app = self._app(focus)
        app._on_notification(5, 'Chat', 'S', 'B', 'org.example.Chat', {})
        assert len(app._held_notifications) == 1
        app._on_notification_closed(5)
        focus.set_enabled(False)
        app._tick_focus_release()
        assert app._shell.shown == []

    def test_held_replacement_keeps_one_entry(self):
        focus = _focus(apps=['org.example.Chat'])
        app = self._app(focus)
        app._on_notification(5, 'Chat', 'old', 'B', 'org.example.Chat', {})
        app._on_notification(5, 'Chat', 'new', 'B', 'org.example.Chat', {})
        focus.set_enabled(False)
        app._tick_focus_release()
        assert [args[2] for args in app._shell.shown] == ['new']

    def test_delivered_replacement_drops_the_held_copy(self):
        focus = _focus(apps=['org.example.Chat'])
        app = self._app(focus)
        app._on_notification(5, 'Chat', 'old', 'B', 'org.example.Chat', {})
        focus.set_enabled(False)
        app._on_notification(5, 'Chat', 'new', 'B', 'org.example.Chat', {})
        app._tick_focus_release()
        assert [args[2] for args in app._shell.shown] == ['new']

    def test_sender_without_desktop_entry_is_held_by_id_tail(self):
        app = self._app(_focus(apps=['org.signal.Signal']))
        app._on_notification(6, 'Signal', 'S', 'B', '', {})
        assert len(app._held_notifications) == 1
        assert app._shell.shown == []

    def test_sender_without_desktop_entry_is_held_by_display_name(self):
        import types
        entry = types.SimpleNamespace(app_id='signal-desktop.desktop',
                                      name='Signal Messenger')
        app = self._app(_focus(apps=['signal-desktop']), entries=[entry])
        app._on_notification(7, 'Signal Messenger', 'S', 'B', '', {})
        assert len(app._held_notifications) == 1

    def test_unrelated_sender_without_entry_is_delivered(self):
        app = self._app(_focus(apps=['org.signal.Signal']))
        app._on_notification(8, 'Weather', 'S', 'B', '', {})
        assert len(app._held_notifications) == 0
        assert len(app._shell.shown) == 1


class _NotifBox:
    def __init__(self) -> None:
        self.children: list = []

    def get_first_child(self):
        return self.children[0] if self.children else None

    def remove(self, child):
        self.children.remove(child)

    def append(self, child):
        child.parent = self
        self.children.append(child)


class _LineButton:
    def __init__(self, label=''):
        self.label = label
        self.parent = None

    def add_css_class(self, _css):
        pass

    def connect(self, *_args):
        return 1

    def get_next_sibling(self):
        siblings = self.parent.children
        index = siblings.index(self)
        return siblings[index + 1] if index + 1 < len(siblings) else None


class _Lock:
    """The lock screen's notification list, run through the real
    LockScreen methods against a recording box."""

    def __init__(self, notifications, mode='summary', dnd=False):
        import types
        self._notif_box = _NotifBox()
        self._get_notifications = notifications
        self._config = types.SimpleNamespace(lock_screen_notifications=mode)
        self._dnd = dnd
        self.visible = True

    def _dnd_active(self):
        return self._dnd

    def get_visible(self):
        return self.visible

    def refresh_notifications(self):
        import lock_screen
        lock_screen.LockScreen.refresh_notifications(self)

    def _refresh_notifications(self):
        import lock_screen
        lock_screen.LockScreen._refresh_notifications(self)

    def _on_notification_tapped(self):
        pass

    def lines(self):
        return [c.label for c in self._notif_box.children]


class TestLockListWhileLocked:
    @pytest.fixture(autouse=True)
    def _fake_button(self, monkeypatch):
        import lock_screen
        monkeypatch.setattr(lock_screen.Gtk, 'Button', _LineButton,
                            raising=False)

    def _app(self, lock, locked=True):
        import types
        app = _app()
        rows: list = []

        def add(*args):
            rows.insert(0, (args[1], args[2], args[0]))

        def dismiss(notif_id, notify_daemon=True):
            rows[:] = [r for r in rows if r[2] != notif_id]

        shade = types.SimpleNamespace(add_notification=add, dismiss=dismiss)
        lock._get_notifications = lambda: [(a, s) for a, s, _ in rows]
        app._shell = _shell(_ensure_shade=lambda: shade, _shade=shade,
                            _lock_screen=lock, is_locked=lambda: locked)
        return app

    def test_arrival_and_withdrawal_show_while_locked(self):
        lock = _Lock(None)
        app = self._app(lock)
        app._on_notification(4, 'Chat', 'Lunch?', 'B', 'org.x.Chat', {})
        assert lock.lines() == ['Chat \u2014 Lunch?']
        app._on_notification_closed(4)
        assert lock.lines() == []

    def test_privacy_setting_still_applies(self):
        lock = _Lock(None, mode='count')
        app = self._app(lock)
        app._on_notification(4, 'Chat', 'Lunch?', 'B', 'org.x.Chat', {})
        app._on_notification(5, 'Mail', 'Invoice', 'B', 'org.x.Mail', {})
        assert lock.lines() == ['2 notifications']
        off = _Lock(None, mode='off')
        app = self._app(off)
        app._on_notification(4, 'Chat', 'Lunch?', 'B', 'org.x.Chat', {})
        assert off.lines() == []

    def test_unlocked_leaves_the_lock_list_alone(self):
        lock = _Lock(None)
        app = self._app(lock, locked=False)
        app._on_notification(4, 'Chat', 'Lunch?', 'B', 'org.x.Chat', {})
        assert lock.lines() == []


# -- DnD / Focus tiles against a schedule -------------------------------------

def _cfg(**data):
    import types
    return types.SimpleNamespace(data=dict(data), save=lambda: None)


def _mon(hour, minute=0, week=0):
    from datetime import datetime, timedelta
    return datetime(2026, 11, 9, hour, minute) + timedelta(weeks=week)


_WORKDAY = [{'days': [0, 1, 2, 3, 4], 'start': '09:00', 'end': '17:00'}]
_ALL_DAY = [{'days': list(range(7)), 'start': '00:00', 'end': '12:00'},
            {'days': list(range(7)), 'start': '12:00', 'end': '00:00'}]


class TestScheduledSessionEnds:
    def test_dnd_off_ends_the_current_session_only(self):
        from dnd import DndState
        dnd = DndState(_cfg(dnd_schedules=_WORKDAY))
        assert dnd.is_active(_mon(12))
        dnd.set_enabled(False, now=_mon(12))
        assert not dnd.is_active(_mon(12))
        assert not dnd.is_active(_mon(16, 59))
        assert dnd.is_active(_mon(9, 30).replace(day=10))
        dnd.set_enabled(True, now=_mon(12))
        assert dnd._config.data['dnd_schedule_off_until'] == 0.0

    def test_focus_off_ends_touching_windows_as_one_session(self):
        from focus_mode import FocusState
        windows = [{'days': [0], 'start': '09:00', 'end': '12:00'},
                   {'days': [0], 'start': '12:00', 'end': '17:00'}]
        focus = FocusState(_cfg(focus_schedules=windows))
        assert focus.is_active(_mon(10), now_ts=0.0)
        focus.set_enabled(False, now=_mon(10))
        assert not focus.is_active(_mon(13), now_ts=0.0)
        assert focus.is_active(_mon(10, week=1), now_ts=0.0)

    def test_off_outside_a_schedule_skips_nothing(self):
        from dnd import DndState
        dnd = DndState(_cfg(dnd_schedules=_WORKDAY, dnd_enabled=True))
        dnd.set_enabled(False, now=_mon(18))
        assert dnd._config.data['dnd_schedule_off_until'] == 0.0
        assert dnd.is_active(_mon(9, 30, week=1))


class _TileBtn:
    def __init__(self, active):
        self.active = active
        self.css: list = []

    def set_active(self, value):
        self.active = value

    def get_active(self):
        return self.active

    def add_css_class(self, css):
        if css not in self.css:
            self.css.append(css)

    def remove_css_class(self, css):
        if css in self.css:
            self.css.remove(css)


class _TileLabel:
    text = ''

    def set_text(self, text):
        self.text = text


class _NoHardware(dict):
    def __contains__(self, _key):
        return True

    def __getitem__(self, _key):
        return False


class _PanelSelf:
    """Runs the real QuickActionsPanel methods on a plain object: the
    panel is a Gtk widget, which needs a display to construct."""

    def __getattr__(self, name):
        import quick_actions
        attr = getattr(quick_actions.QuickActionsPanel, name)
        return attr.__get__(self) if callable(attr) else attr


def _panel(key, dnd=None, focus=None, nope=None):
    from types import SimpleNamespace
    panel = _PanelSelf()
    panel._dnd = dnd
    panel._focus = focus
    panel._nope = nope
    panel._on_nope_changed = None
    panel._hw_gate_cache = _NoHardware()
    off = SimpleNamespace(available=lambda: False, is_active=lambda: False)
    panel._als = off
    panel._location = off
    panel._tile_buttons = {key: _TileBtn(True)}
    panel._state_labels = {key: _TileLabel()}
    panel._tile_state = {key: True}
    panel._tile_details = {}
    panel._updating = False
    panel.refresh_states = lambda *a, **k: None
    tile = next(t for t in panel._tiles(include_slow_gates=False)
                if t.key == key)
    return panel, tile


class TestTilesEndScheduledSessions:
    def _tap_off(self, panel, tile):
        btn = panel._tile_buttons[tile.key]
        btn.set_active(False)
        panel._on_tile_toggled(btn, tile)
        return btn

    def test_dnd_tile_turns_a_scheduled_session_off(self):
        from dnd import DndState
        dnd = DndState(_cfg(dnd_schedules=_ALL_DAY))
        assert dnd.is_active()
        panel, tile = _panel('dnd', dnd=dnd)
        btn = self._tap_off(panel, tile)
        assert not dnd.is_active()
        assert btn.active is False and 'active' not in btn.css

    def test_focus_tile_turns_a_scheduled_session_off(self):
        from focus_mode import FocusState
        focus = FocusState(_cfg(focus_schedules=_ALL_DAY))
        assert focus.is_active()
        panel, tile = _panel('focus', focus=focus)
        btn = self._tap_off(panel, tile)
        assert not focus.is_active()
        assert btn.active is False


class TestNopeTileKeepsFriction:
    def _nope(self, break_log=()):
        from nope_mode import NopeState
        return NopeState(_cfg(nope_enabled=True, nope_schedules=_ALL_DAY,
                              nope_break_log=list(break_log)))

    def _tap_off(self, nope):
        panel, tile = _panel('nope', nope=nope)
        btn = panel._tile_buttons['nope']
        btn.set_active(False)
        panel._on_tile_toggled(btn, tile)
        return btn

    def test_tap_off_in_a_schedule_is_a_bounded_break(self):
        nope = self._nope()
        assert nope.is_active()
        btn = self._tap_off(nope)
        assert nope.enabled
        assert nope.override == 'break'
        assert not nope.is_active()
        assert btn.active is False

    def test_refused_break_leaves_nope_on(self):
        import time
        now = time.time()
        nope = self._nope(break_log=[now - 60, now - 120, now - 180])
        btn = self._tap_off(nope)
        assert nope.enabled
        assert nope.is_active()
        assert btn.active is True and 'active' in btn.css


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
