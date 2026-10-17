"""W3 call UI + call audio (snapshot surface, ringer, proximity, dialer).

Headless: real widgets are never constructed. call_ui/modem_monitor/dialer/
window/display_manager are imported against a minimal fake gi stack (the
test_call_ui_flow idiom) and driven as unbound methods on hand-built
namespaces or __new__ instances with fake labels/stacks/buses.
"""
import inspect
import logging
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))


def _install_fake_gi() -> None:
    glib = types.ModuleType('gi.repository.GLib')
    glib.SOURCE_REMOVE = False
    glib.SOURCE_CONTINUE = True
    glib.Error = type('Error', (Exception,), {})
    glib.timeouts = []
    glib.removed = []
    glib.idle_add = lambda *a, **k: 1
    glib.timeout_add = lambda ms, cb: glib.timeouts.append(cb) or 101
    glib.timeout_add_seconds = lambda secs, cb: glib.timeouts.append(cb) or 102
    glib.source_remove = lambda tid: glib.removed.append(tid)
    glib.Variant = lambda *a, **k: None

    gdk = types.ModuleType('gi.repository.Gdk')
    gdk.Display = types.SimpleNamespace(get_default=lambda: None)

    gio = types.ModuleType('gi.repository.Gio')
    gio.BusType = types.SimpleNamespace(SYSTEM=1, SESSION=2)
    gio.DBusSignalFlags = types.SimpleNamespace(NONE=0)
    gio.DBusCallFlags = types.SimpleNamespace(NONE=0)
    gio.bus_get_sync = lambda *a, **k: None

    gtk = types.ModuleType('gi.repository.Gtk')
    gtk.Window = type('Window', (), {})

    adw = types.ModuleType('gi.repository.Adw')
    adw.Application = type('Application', (), {})
    adw.ApplicationWindow = type('ApplicationWindow', (), {})

    pango = types.ModuleType('gi.repository.Pango')
    pango.EllipsizeMode = types.SimpleNamespace(END=3)

    gi_mod = types.ModuleType('gi')
    gi_rep = types.ModuleType('gi.repository')

    def _require_version(namespace, version):
        if namespace == 'Gtk4LayerShell':
            raise ValueError(f'{namespace} not available')
    gi_mod.require_version = _require_version
    sys.modules['gi'] = gi_mod
    sys.modules['gi.repository'] = gi_rep
    sys.modules['gi.repository.GLib'] = glib
    sys.modules['gi.repository.Gdk'] = gdk
    sys.modules['gi.repository.Gio'] = gio
    sys.modules['gi.repository.Gtk'] = gtk
    sys.modules['gi.repository.Adw'] = adw
    sys.modules['gi.repository.Pango'] = pango


_install_fake_gi()

import call_ui  # noqa: E402  (needs fake gi modules installed first)
import modem_monitor  # noqa: E402
import dialer  # noqa: E402
import display_manager  # noqa: E402

# window is imported with the test_w2_lock save/restore idiom: sibling
# modules evict it from sys.modules during collection, so a plain import
# here would re-execute it under these stubs and leave that copy cached
# for every later test (stale Gtk bindings). Import under the previous
# entry, then leave sys.modules exactly as found; the local reference is
# only used for Gtk-free wiring methods.
_saved_window = sys.modules.get('window')
sys.modules.pop('window', None)
import window  # noqa: E402
if _saved_window is not None:
    sys.modules['window'] = _saved_window
else:
    sys.modules.pop('window', None)
del _saved_window

CALL = '/org/freedesktop/ModemManager1/Call/3'
CALL2 = '/org/freedesktop/ModemManager1/Call/4'
MODEM = '/org/freedesktop/ModemManager1/Modem/0'


def _call(path=CALL, number='+15550001111', direction=1, state=3,
          started_at=None):
    return modem_monitor.CallInfo(
        path=path, number=number, direction=direction, state=state,
        started_at=started_at)


class FakeLabel:
    def __init__(self, text=''):
        self.text = text

    def set_text(self, text):
        self.text = text

    def get_text(self):
        return self.text


class FakeStack:
    def __init__(self):
        self.visible = None

    def set_visible_child_name(self, name):
        self.visible = name


class FakeToggle:
    def __init__(self):
        self.active = False

    def set_active(self, value):
        self.active = bool(value)

    def get_active(self):
        return self.active


class FakeBox:
    def __init__(self):
        self.visible = False
        self.children = []

    def set_visible(self, value):
        self.visible = bool(value)

    def append(self, child):
        self.children.append(child)


class FakeBar:
    def __init__(self):
        self.shown = False
        self.text = ''
        self.updates = []

    def show_bar(self, text):
        self.shown = True
        self.text = text

    def hide_bar(self):
        self.shown = False

    def update(self, caller, timer_text):
        self.updates.append((caller, timer_text))
        self.text = f'{caller}  ·  {timer_text}'


class FakeRinger:
    def __init__(self):
        self.calls = []

    def start(self, tone='ringtone.ogg'):
        self.calls.append(('start', tone))

    def stop(self):
        self.calls.append('stop')

    def silence(self):
        self.calls.append('silence')


class FakeAudio:
    def __init__(self):
        self.calls = []

    def enter_call(self):
        self.calls.append('enter')

    def exit_call(self):
        self.calls.append('exit')

    def set_speaker(self, enabled):
        self.calls.append(('speaker', enabled))

    def set_mic_mute(self, muted):
        self.calls.append(('mute', muted))


class FakeProximity:
    def __init__(self):
        self.calls = []

    def start(self, display_mgr, surface):
        self.calls.append('start')

    def stop(self):
        self.calls.append('stop')


def _make_ui(accept_fn=None, hangup_fn=None, hold_accept_fn=None,
               dtmf_fn=None, **overrides):
    ui = call_ui.CallUI.__new__(call_ui.CallUI)
    ui._events = []
    ui._on_accept = lambda: ui._events.append('accept')
    ui._on_decline = lambda: ui._events.append('decline')
    ui._on_hangup = lambda: ui._events.append('hangup')
    ui._accept_fn = accept_fn or (lambda path, done: done(True, None))
    ui._hangup_fn = hangup_fn or (lambda path, done: done(True, None))
    ui._hold_accept_fn = hold_accept_fn or (lambda modem, done: done(True, None))
    ui._dtmf_fn = dtmf_fn or (lambda path, digit, done: done(True, None))
    ui._incoming_call_path = CALL
    ui._incoming_path = CALL
    ui._primary_path = None
    ui._waiting_path = None
    ui._action_path = None
    ui._pending_action = None
    ui._surface_live = False
    ui._minimized = False
    ui._presented_page = None
    ui._presented_path = None
    ui._live_snapshot = []
    ui._current_caller = ''
    ui._current_number = ''
    ui._muted = False
    ui._speakerphone = False
    ui._timed_path = None
    ui._call_start_mono = None
    ui._close_src = None
    ui._close_btns = [FakeBox(), FakeBox(), FakeBox()]
    ui._bar = FakeBar()
    ui._display_mgr = None
    ui._ringer = FakeRinger()
    ui._audio = FakeAudio()
    ui._proximity = FakeProximity()
    ui._inc_caller = FakeLabel()
    ui._inc_number = FakeLabel()
    ui._out_caller = FakeLabel()
    ui._out_number = FakeLabel()
    ui._act_caller = FakeLabel()
    ui._act_number = FakeLabel()
    ui._act_timer = FakeLabel('0:00')
    ui._mute_btn = FakeToggle()
    ui._speaker_btn = FakeToggle()
    ui._keypad_btn = FakeToggle()
    ui._keypad_grid = FakeBox()
    ui._waiting_box = FakeBox()
    ui._waiting_label = FakeLabel()
    ui._stack = FakeStack()
    ui._timer_id = None
    ui.presented = 0
    ui.hidden = 0
    ui.sensitive = True
    ui.present = lambda: setattr(ui, 'presented', ui.presented + 1)
    ui.hide = lambda: setattr(ui, 'hidden', ui.hidden + 1)
    ui.set_sensitive = lambda value: setattr(ui, 'sensitive', bool(value))
    for key, value in overrides.items():
        setattr(ui, key, value)
    return ui


class FakeReply:
    # Mirrors Gio: every reply is a tuple of out-args.
    def __init__(self, value):
        self._value = value

    def unpack(self):
        if isinstance(self._value, dict):
            return (self._value,)
        return self._value


class FakeParams:
    def __init__(self, value):
        self._value = value

    def unpack(self):
        return self._value


class FakeVariant:
    def __init__(self, fmt, value=None):
        self.fmt = fmt
        self.value = value

    def unpack(self):
        return self.value


class FakeBus:
    """Async-only bus: calls never take user_data (XXWM_RULES trap)."""

    def __init__(self, session=True):
        self.session = session
        self.calls = []  # (bus, path, iface, method, params)
        self.pending = []
        self.replies = []
        self.subs = {}
        self.unsubs = []
        self._next = 1

    def call(self, bus_name, path, iface, method, params=None,
             reply_type=None, flags=None, timeout=-1, cancellable=None,
             callback=None):
        self.calls.append((bus_name, path, iface, method, params))
        self.pending.append(callback)

    def call_finish(self, _result):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def complete_next(self, reply) -> None:
        self.replies.append(reply)
        cb = self.pending.pop(0)
        cb(self, object())

    def signal_subscribe(self, sender, iface, signal, path, arg0,
                         flags, callback, user_data):
        assert user_data is None, 'no user_data on async calls'
        sid = self._next
        self._next += 1
        self.subs[sid] = (sender, iface, signal, path, callback)
        return sid

    def signal_unsubscribe(self, sid):
        self.unsubs.append(sid)
        self.subs.pop(sid, None)


class FakeTime:
    def __init__(self, now=1_100.0):
        self.now = now

    def monotonic(self):
        return self.now


def _names(monkeypatch):
    """Deterministic contact names without touching the real ContactBook."""
    monkeypatch.setattr(
        call_ui, 'display_name',
        lambda n: {'+15550001111': 'Mom',
                   '+15550002222': 'Second'}.get(n, n))


def _unknown_contact(monkeypatch):
    import contacts
    monkeypatch.setattr(
        contacts, 'shared_book',
        lambda config=None: types.SimpleNamespace(lookup_number=lambda n: None))


class TestDisplayName:
    """Unknown numbers read through xx_phone; saved names win."""

    def test_unknown_number_formatted_through_xx_phone(self, monkeypatch):
        import xx_phone
        _unknown_contact(monkeypatch)
        number = '+16145550177'
        assert call_ui.display_name(number) == xx_phone.display(
            number, xx_phone.user_country("", ""))

    def test_saved_name_wins_over_number(self, monkeypatch):
        import contacts
        monkeypatch.setattr(
            contacts, 'shared_book',
            lambda config=None: types.SimpleNamespace(lookup_number=lambda n: 'Mom'))
        assert call_ui.display_name('+16145550177') == 'Mom'

    def test_empty_number_stays_empty(self):
        assert call_ui.display_name('') == ''

    def test_number_display_never_blocks_on_dbus(self, monkeypatch):
        import contacts
        import xx_phone

        def _boom():
            raise AssertionError(
                'D-Bus autosource must not run on the main thread')

        monkeypatch.setattr(xx_phone, '_auto_sources', _boom)
        number = '+16145550177'
        assert contacts.display_number(number) == xx_phone.display(
            number, xx_phone.user_country("", ""))

    def test_number_display_falls_back_to_raw(self, monkeypatch):
        import contacts
        import xx_phone

        def _boom(raw, country=None):
            raise RuntimeError('no engine')

        monkeypatch.setattr(xx_phone, 'display', _boom)
        assert contacts.display_number('+16145550177') == '+16145550177'


class TestSelectPage:
    """Pure snapshot→page mapping (no widgets)."""

    def test_empty_and_terminated_only_close(self):
        assert call_ui.select_page([]) is None
        assert call_ui.select_page(
            [_call(state=modem_monitor._STATE_TERMINATED)]) is None

    def test_incoming_ringing(self):
        assert call_ui.select_page([_call(state=3)]) == 'incoming'

    def test_outgoing_dialing_and_ringing_out(self):
        assert call_ui.select_page(
            [_call(direction=2, state=1)]) == 'outgoing'
        assert call_ui.select_page(
            [_call(direction=2, state=2)]) == 'outgoing'

    def test_active_and_held(self):
        assert call_ui.select_page([_call(state=4)]) == 'active'
        assert call_ui.select_page([_call(state=5)]) == 'active'

    def test_incoming_beats_active_beats_outgoing(self):
        mixed = [_call(path=CALL2, direction=2, state=1),
                 _call(state=4),
                 _call(path='/c/9', state=3)]
        assert call_ui.select_page(mixed) == 'incoming'
        assert call_ui.primary_call(mixed).path == '/c/9'
        assert call_ui.select_page(mixed[:2]) == 'active'

    def test_lone_waiting_shows_incoming_page(self):
        assert call_ui.select_page([_call(state=6)]) == 'incoming'

    def test_waiting_pick_skips_primary(self):
        calls = [_call(state=4),
                 _call(path=CALL2, number='+15550002222', state=6)]
        primary = call_ui.primary_call(calls)
        assert primary.path == CALL
        waiting = call_ui.waiting_call(calls, primary)
        assert waiting is not None and waiting.path == CALL2

    def test_no_waiting_without_state_6(self):
        assert call_ui.waiting_call([_call(state=4)]) is None

    def test_format_elapsed(self):
        assert call_ui.format_elapsed(1_000.0, 1_065.0) == '1:05'
        assert call_ui.format_elapsed(1_000.0, 999.0) == '0:00'


class TestTimerStability:
    """The timer pins to the ACTIVE transition: re-renders and HELD→ACTIVE
    never reset it; only a new call path does."""

    def test_rerender_and_held_active_keep_base(self, monkeypatch):
        _names(monkeypatch)
        clock = FakeTime(now=1_100.0)
        monkeypatch.setattr(call_ui, 'time', clock)
        ui = _make_ui()
        ui.render_snapshot([_call(state=4, started_at=1_000.0)],
                           display_mgr=None)
        assert ui._call_start_mono == 1_000.0
        assert ui._act_timer.get_text() == '1:40'
        clock.now = 1_130.0
        ui.render_snapshot([_call(state=4, started_at=1_000.0)],
                           display_mgr=None)
        assert ui._call_start_mono == 1_000.0
        assert ui._act_timer.get_text() == '2:10'
        ui.render_snapshot([_call(state=5, started_at=1_000.0)],
                           display_mgr=None)
        ui.render_snapshot([_call(state=4, started_at=1_000.0)],
                           display_mgr=None)
        assert ui._call_start_mono == 1_000.0
        assert ui._act_timer.get_text() == '2:10'

    def test_new_path_resets_base(self, monkeypatch):
        _names(monkeypatch)
        clock = FakeTime(now=2_000.0)
        monkeypatch.setattr(call_ui, 'time', clock)
        ui = _make_ui()
        ui.render_snapshot([_call(state=4, started_at=1_000.0)],
                           display_mgr=None)
        ui.render_snapshot([_call(path=CALL2, number='+15550002222',
                                  state=4, started_at=1_900.0)],
                           display_mgr=None)
        assert ui._timed_path == CALL2
        assert ui._act_timer.get_text() == '1:40'


class TestRenderPages:
    def test_outgoing_page_with_hangup(self, monkeypatch):
        _names(monkeypatch)
        hung = []
        ui = _make_ui(
            _hangup_fn=lambda p, done: hung.append(p) or done(True, None))
        ui.render_snapshot([_call(direction=2, state=1)], display_mgr=None)
        assert ui._stack.visible == 'outgoing'
        assert ui._out_caller.get_text() == 'Mom'
        assert ui.presented == 0  # dialing stays on the bar, no steal
        assert ui._bar.text == 'Mom'
        ui._on_hangup_clicked(None)
        assert hung == [CALL]
        assert ui._events == ['hangup']

    def test_active_page_enters_call_audio(self, monkeypatch):
        _names(monkeypatch)
        monkeypatch.setattr(call_ui, 'time', FakeTime(now=1_100.0))
        ui = _make_ui()
        ui.render_snapshot([_call(state=4, started_at=1_000.0)],
                           display_mgr=None)
        assert ui._stack.visible == 'active'
        assert ui._audio.calls == ['enter']
        assert ui._act_timer.get_text() == '1:40'

    def test_empty_snapshot_closes_everything(self, monkeypatch):
        _names(monkeypatch)
        ui = _make_ui()
        ui.render_snapshot([_call(state=4, started_at=1_000.0)],
                           display_mgr=object())
        ui.render_snapshot([], display_mgr=None)
        assert ui.hidden == 1
        assert ui._bar.shown is False
        assert 'stop' in ui._ringer.calls
        assert 'exit' in ui._audio.calls
        assert 'stop' in ui._proximity.calls
        assert ui._live_snapshot == []

    def test_ringing_keeps_ringer_but_active_stops_it(self, monkeypatch):
        _names(monkeypatch)
        ui = _make_ui()
        ui.render_snapshot([_call(state=3)], display_mgr=None)
        assert ui._ringer.calls == []  # window starts it after DnD gating
        ui._ringer.calls.append('start')
        ui.render_snapshot([_call(state=4, started_at=1.0)], display_mgr=None)
        assert ui._ringer.calls.count('stop') == 1


class TestWaitingCall:
    def test_banner_and_hold_answer(self, monkeypatch):
        _names(monkeypatch)
        held = []
        ui = _make_ui(
            _hold_accept_fn=lambda m, done: held.append(m) or done(True, None))
        monkeypatch.setattr(call_ui, '_resolve_modem_path', lambda: MODEM)
        ui.render_snapshot([_call(state=4, started_at=1.0),
                            _call(path=CALL2, number='+15550002222',
                                  direction=1, state=6)],
                           display_mgr=None)
        assert ui._stack.visible == 'active'
        assert ui._waiting_box.visible is True
        assert 'Second' in ui._waiting_label.get_text()
        ui._on_hold_answer_clicked(None)
        assert held == [MODEM]

    def test_waiting_decline_hangs_up_waiting_path(self, monkeypatch):
        _names(monkeypatch)
        hung = []
        ui = _make_ui(
            _hangup_fn=lambda p, done: hung.append(p) or done(True, None))
        ui.render_snapshot([_call(state=4, started_at=1.0),
                            _call(path=CALL2, number='+15550002222',
                                  direction=1, state=6)],
                           display_mgr=None)
        ui._on_waiting_decline_clicked(None)
        assert hung == [CALL2]
        assert ui._waiting_path is None  # no double-decline while in flight
        # The banner itself is snapshot-owned: it hides when the modem
        # confirms the hangup.
        assert ui._waiting_box.visible is True
        ui.render_snapshot([_call(state=4, started_at=1.0)], display_mgr=None)
        assert ui._waiting_box.visible is False

    def test_no_banner_for_single_call(self, monkeypatch):
        _names(monkeypatch)
        ui = _make_ui()
        ui.render_snapshot([_call(state=4, started_at=1.0)], display_mgr=None)
        assert ui._waiting_box.visible is False


class TestRecoverability:
    def test_failed_hangup_closes_but_keeps_bar(self, monkeypatch, caplog):
        _names(monkeypatch)
        ui = _make_ui(
            _hangup_fn=lambda p, done: done(False, 'stuck'))
        live = [_call(state=4, started_at=1.0)]
        ui.render_snapshot(live, display_mgr=None)
        ui._live_snapshot = list(live)
        with caplog.at_level(logging.WARNING):
            ui._on_hangup_clicked(None)
        assert ui._events == []
        assert ui.hidden == 1
        assert ui._bar.shown is True
        assert ui._bar.text == 'Mom'
        assert ui._pending_action is None

    def test_modem_silence_offers_close(self):
        ui = _make_ui(accept_fn=lambda p, done: None)  # never answers
        ui.show_incoming('Mom', '+15550001111', CALL)
        ui._on_accept_clicked(None)
        assert ui._pending_action == 'accept'
        ui._on_action_timeout()
        assert all(b.visible for b in ui._close_btns)
        ui._on_close_clicked(None)
        assert ui._pending_action is None
        assert ui.hidden == 1
        assert all(not b.visible for b in ui._close_btns)

    def test_answered_action_needs_no_close(self):
        seen = []
        ui = _make_ui(accept_fn=lambda p, done: seen.append(done))
        ui.show_incoming('Mom', '+15550001111', CALL)
        ui._on_accept_clicked(None)
        seen.pop(0)(True, None)
        assert all(not b.visible for b in ui._close_btns)


class TestTickAndMinimize:
    def test_bar_updates_every_tick(self, monkeypatch):
        _names(monkeypatch)
        clock = FakeTime(now=1_100.0)
        monkeypatch.setattr(call_ui, 'time', clock)
        ui = _make_ui()
        ui.render_snapshot([_call(state=4, started_at=1_000.0)],
                           display_mgr=None)
        ui._bar.updates.clear()
        assert ui._tick_timer() is True
        assert ui._bar.updates[-1] == ('Mom', '1:40')
        clock.now = 1_101.0
        assert ui._tick_timer() is True
        assert ui._bar.updates[-1] == ('Mom', '1:41')

    def test_tick_stops_without_live_calls(self):
        ui = _make_ui()
        ui._timer_id = 7
        assert ui._tick_timer() is False
        assert ui._timer_id is None

    def test_minimize_collapses_and_rerender_respects_it(self, monkeypatch):
        _names(monkeypatch)
        ui = _make_ui()
        ui.render_snapshot([_call(state=4, started_at=1.0)], display_mgr=None)
        presented = ui.presented
        ui.minimize()
        assert ui.hidden == 1
        assert ui._bar.shown is True
        ui.render_snapshot([_call(state=4, started_at=1.0)], display_mgr=None)
        assert ui.presented == presented  # stays down until expanded
        ui.expand()
        assert ui.presented == presented + 1

    def test_new_call_unminimizes(self, monkeypatch):
        _names(monkeypatch)
        ui = _make_ui()
        ui.render_snapshot([_call(state=4, started_at=1.0)], display_mgr=None)
        ui.minimize()
        ui.render_snapshot([_call(path=CALL2, number='+15550002222',
                                  state=3)], display_mgr=None)
        assert ui._stack.visible == 'incoming'
        assert ui.presented >= 1


class TestAudioRouter:
    def _router(self, bus):
        pactl = []
        mute = []
        router = call_ui.CallAudioRouter(
            session_bus=lambda: bus,
            run_pactl=pactl.append,
            run_mute=mute.append)
        return router, pactl, mute

    def test_callaudiod_selected_when_present(self):
        bus = FakeBus()
        router, pactl, _mute = self._router(bus)
        router.enter_call()
        assert bus.calls[-1][:4] == (
            'org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'GetNameOwner')
        bus.complete_next(FakeReply(('owner',)))
        assert bus.calls[-1][:4] == (
            'org.mobian_project.CallAudio', '/org/mobian_project/CallAudio',
            'org.mobian_project.CallAudio', 'SelectMode')
        assert pactl == []
        router.exit_call()
        assert bus.calls[-1][3] == 'SelectMode'
        assert pactl == []  # exit also rides callaudiod while present

    def test_enter_is_idempotent(self):
        bus = FakeBus()
        router, pactl, _mute = self._router(bus)
        router.enter_call()
        bus.complete_next(FakeReply(('owner',)))
        selects = [c for c in bus.calls if c[3] == 'SelectMode']
        router.enter_call()
        assert [c for c in bus.calls if c[3] == 'SelectMode'] == selects

    def test_pactl_fallback_when_absent(self):
        bus = FakeBus()
        router, pactl, _mute = self._router(bus)
        router.enter_call()
        bus.complete_next(Exception('no owner'))
        assert pactl == [True]

    def test_pactl_fallback_without_bus(self):
        router, pactl, _mute = self._router(None)
        router.enter_call()
        assert pactl == [True]
        router.exit_call()
        assert pactl == [True, False]

    def test_speaker_and_mute_routing(self):
        bus = FakeBus()
        router, pactl, mute = self._router(bus)
        router.set_speaker(True)
        bus.complete_next(FakeReply(('owner',)))
        assert bus.calls[-1][3] == 'EnableSpeaker'
        router.set_mic_mute(True)
        assert bus.calls[-1][3] == 'MuteMic'
        assert pactl == [] and mute == []

    def test_speaker_and_mute_pactl_fallback(self):
        router, pactl, mute = self._router(None)
        router.set_speaker(True)
        router.set_mic_mute(True)
        assert pactl == [False]  # speaker on routes away from the earpiece
        assert mute == [True]

    def test_async_calls_carry_no_user_data(self):
        seen = {}

        class StrictBus(FakeBus):
            def call(self, *args, **kwargs):
                seen['nargs'] = len(args)
                super().call(*args, **kwargs)

        bus = StrictBus()
        router, _p, _m = self._router(bus)
        router.enter_call()
        # (bus, path, iface, method, params, reply, flags, timeout,
        # cancellable, callback): an 11th arg would be user_data, which
        # shifts the callback arity on a real bus (self is already bound).
        assert seen['nargs'] == 10


class TestPactlFallback:
    def test_card_names_parsed(self, monkeypatch):
        import subprocess

        out = ('Card #45\n\tName: alsa_card.platform-sound\n'
               'Card #46\n\tName: bluez_card.XX_YY\n')
        monkeypatch.setattr(
            subprocess, 'run',
            lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=out))
        assert call_ui._pactl_card_names() == [
            'alsa_card.platform-sound', 'bluez_card.XX_YY']

    def test_card_names_empty_on_failure(self, monkeypatch):
        import subprocess
        monkeypatch.setattr(
            subprocess, 'run',
            lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
        assert call_ui._pactl_card_names() == []

    def test_hunts_sequential_first_success_wins(self, monkeypatch):
        import subprocess
        ran = []

        def _run(cmd, **kwargs):
            ran.append(cmd)
            rc = 0 if cmd[-1] in ('voice-call', 'output-speaker') else 1
            return types.SimpleNamespace(returncode=rc, stdout='')

        monkeypatch.setattr(subprocess, 'run', _run)
        call_ui._run_pactl_hunts([
            [['pactl', 'set-card-profile', 'cardA', 'Voice Call'],
             ['pactl', 'set-card-profile', 'cardA', 'voice-call']],
            [['pactl', 'set-sink-port', '@DEFAULT_SINK@', 'nope'],
             ['pactl', 'set-sink-port', '@DEFAULT_SINK@', 'output-speaker']],
        ])
        assert ran == [
            ['pactl', 'set-card-profile', 'cardA', 'Voice Call'],
            ['pactl', 'set-card-profile', 'cardA', 'voice-call'],
            ['pactl', 'set-sink-port', '@DEFAULT_SINK@', 'nope'],
            ['pactl', 'set-sink-port', '@DEFAULT_SINK@', 'output-speaker'],
        ]

    def test_route_addresses_cards_by_name(self, monkeypatch):
        import subprocess
        ran = []

        def _run(cmd, **kwargs):
            ran.append(cmd)
            if cmd[:2] == ['pactl', 'list']:
                return types.SimpleNamespace(
                    returncode=0,
                    stdout='Card #7\n\tName: alsa_card.main\n')
            return types.SimpleNamespace(returncode=0, stdout='')

        monkeypatch.setattr(subprocess, 'run', _run)

        class SyncThread:
            def __init__(self, target=None, args=(), daemon=False):
                self._target = target

            def start(self):
                self._target()

        monkeypatch.setattr(call_ui.threading, 'Thread', SyncThread)
        call_ui._set_audio_route(earpiece=True)
        profile_cmds = [c for c in ran if c[1:2] == ['set-card-profile']]
        assert profile_cmds, 'no profile command ran'
        for cmd in profile_cmds:
            assert cmd[2] == 'alsa_card.main', cmd
            assert cmd[2] != '0'


class TestRinger:
    def _ringer(self, bus):
        played = []
        stopped = []
        ringer = call_ui.Ringer(
            session_bus=lambda: bus,
            play=lambda: played.append(True),
            stop=lambda: stopped.append(True))
        return ringer, played, stopped

    def test_feedbackd_preferred_with_system_event(self, monkeypatch):
        monkeypatch.setattr(call_ui.GLib, 'Variant', FakeVariant)
        bus = FakeBus()
        ringer, played, stopped = self._ringer(bus)
        ringer.start()
        _bus, path, iface, method, params = bus.calls[-1]
        assert (_bus, path, iface, method) == (
            'org.sigxcpu.Feedback', '/org/sigxcpu/Feedback',
            'org.sigxcpu.Feedback', 'TriggerFeedback')
        assert params.fmt == '(ssa{sv}i)'
        # Timeout 0 loops until EndFeedback; -1 would play the event's
        # default duration once and then go silent mid-ring.
        assert (params.value[0], params.value[1], params.value[3]) == (
            'io.piercingxx.XXWM', 'phone-incoming-call', 0)
        assert played == []
        bus.complete_next(FakeReply((7,)))
        assert ringer._feedback_id == 7
        ringer.stop()
        assert bus.calls[-1][3] == 'EndFeedback'
        assert stopped == [True]

    def test_sound_fallback_when_feedbackd_absent(self):
        bus = FakeBus()
        ringer, played, stopped = self._ringer(bus)
        ringer.start()
        bus.complete_next(Exception('no feedbackd'))
        assert played == [True]
        ringer.stop()
        assert stopped == [True]
        assert [c[3] for c in bus.calls] == ['TriggerFeedback']

    def test_sound_fallback_without_bus(self):
        ringer, played, _stopped = self._ringer(None)
        ringer.start()
        assert played == [True]

    def test_start_idempotent_and_stop_safe_twice(self):
        bus = FakeBus()
        ringer, _played, stopped = self._ringer(bus)
        ringer.start()
        ringer.start()
        assert len(bus.calls) == 1
        ringer.stop()
        ringer.stop()
        assert stopped == [True, True]

    def test_terminal_paths_stop_ringer(self, monkeypatch):
        _names(monkeypatch)
        ui = _make_ui()
        ui.render_snapshot([_call(state=3)], display_mgr=None)
        ui._ringer.calls.append('start')
        ui._finish_accept(CALL, True, None)
        assert 'stop' in ui._ringer.calls
        ui._ringer.calls.clear()
        ui._ringer.calls.append('start')
        ui._finish_decline(CALL, True, None)
        assert 'stop' in ui._ringer.calls
        ui._ringer.calls.clear()
        ui.render_snapshot([], display_mgr=None)
        assert 'stop' in ui._ringer.calls

    def test_silence_keeps_call_state(self):
        bus = FakeBus()
        ringer, _played, stopped = self._ringer(bus)
        ringer.start()
        ringer.silence()
        assert ringer.ringing is False
        assert stopped == [True]


class FakeDisplay:
    def __init__(self):
        self.calls = []

    def _blank(self):
        self.calls.append('blank')

    def wake(self):
        self.calls.append('wake')


class TestProximity:
    def _claimed_bus(self):
        bus = FakeBus(session=False)
        monitor = call_ui.ProximityMonitor(system_bus=lambda: bus)
        display = FakeDisplay()
        surface = _make_ui()
        monitor.start(display, surface)
        assert bus.calls[-1][3] == 'ClaimProximity'
        bus.complete_next(FakeReply(()))
        assert monitor.claimed is True
        return bus, monitor, display, surface

    def test_near_blanks_and_insensitizes_far_restores(self):
        bus, monitor, display, surface = self._claimed_bus()
        assert bus.calls[-1][3] == 'Get'  # initial ProximityNear read
        bus.complete_next(FakeReply((True,)))
        assert surface.sensitive is False
        assert display.calls == ['blank']
        monitor._on_properties_changed(
            None, None, None, None, None,
            FakeParams(('net.hadess.SensorProxy',
                        {'ProximityNear': False}, [])),
            None)
        assert surface.sensitive is True
        assert display.calls == ['blank', 'wake']

    def test_unrelated_properties_ignored(self):
        bus, monitor, display, surface = self._claimed_bus()
        monitor._on_properties_changed(
            None, None, None, None, None,
            FakeParams(('net.hadess.SensorProxy', {'HasProximity': True}, [])),
            None)
        assert display.calls == []
        monitor._on_properties_changed(
            None, None, None, None, None,
            FakeParams(('other.Iface', {'ProximityNear': True}, [])),
            None)
        assert display.calls == []

    def test_absent_sensor_is_noop(self):
        bus = FakeBus(session=False)
        monitor = call_ui.ProximityMonitor(system_bus=lambda: bus)
        monitor.start(FakeDisplay(), _make_ui())
        bus.complete_next(Exception('no sensor'))
        assert monitor.claimed is False
        assert [c[3] for c in bus.calls] == ['ClaimProximity']

    def test_no_display_no_claim(self):
        bus = FakeBus(session=False)
        monitor = call_ui.ProximityMonitor(system_bus=lambda: bus)
        monitor.start(None, _make_ui())
        assert bus.calls == []

    def test_stop_releases_and_restores(self):
        bus, monitor, display, surface = self._claimed_bus()
        surface.sensitive = False
        monitor._apply_near(True)
        monitor.stop()
        assert monitor.claimed is False
        assert bus.calls[-1][3] == 'ReleaseProximity'
        assert surface.sensitive is True
        assert display.calls[-1] == 'wake'  # blanked by us: wake on release
        monitor.stop()  # idempotent

    def test_speaker_on_releases_proximity(self, monkeypatch):
        _names(monkeypatch)
        bus = FakeBus(session=False)
        ui = _make_ui()
        display = FakeDisplay()
        ui._proximity = call_ui.ProximityMonitor(system_bus=lambda: bus)
        ui.render_snapshot([_call(state=4, started_at=1.0)],
                           display_mgr=display)
        bus.complete_next(FakeReply(()))
        assert ui._proximity.claimed is True
        ui._speakerphone = True
        ui._update_proximity(ui._live_snapshot)
        assert ui._proximity.claimed is False
        assert bus.calls[-1][3] == 'ReleaseProximity'

    def test_dtmf_sends_digit_on_primary_path(self):
        sent = []
        ui = _make_ui(
            _dtmf_fn=lambda p, d, done: sent.append((p, d)) or done(True, None))
        ui._primary_path = CALL
        ui._on_dtmf_clicked(None, '5')
        assert sent == [(CALL, '5')]

    def test_dtmf_failure_logs(self, caplog):
        ui = _make_ui(
            _dtmf_fn=lambda p, d, done: done(False, 'no tone'))
        ui._primary_path = CALL
        with caplog.at_level(logging.WARNING):
            ui._on_dtmf_clicked(None, '5')
        assert any('DTMF' in r.getMessage() for r in caplog.records)


def _make_dialer():
    d = dialer.Dialer.__new__(dialer.Dialer)
    d._digits = ''
    d._suppress_zero_click = False
    d._display = FakeLabel()
    d._status = FakeLabel()
    d._contact_book = types.SimpleNamespace(search=lambda *a, **k: [])
    d._suggestions = types.SimpleNamespace(
        set_visible=lambda v: None, get_first_child=lambda: None)
    return d


class TestDialerZeroLongpress:
    def test_empty_longpress_enters_plus(self):
        d = _make_dialer()
        dialer.Dialer._on_zero_longpress(d, None, 0.0, 0.0)
        assert d._digits == '+'
        assert d._suppress_zero_click is True

    def test_release_click_after_hold_suppressed_once(self):
        d = _make_dialer()
        dialer.Dialer._on_zero_longpress(d, None, 0.0, 0.0)
        dialer.Dialer._on_digit(d, None, '0')
        assert d._digits == '+'
        dialer.Dialer._on_digit(d, None, '0')
        assert d._digits == '+0'

    def test_nonempty_longpress_appends_plus(self):
        d = _make_dialer()
        d._digits = '011'
        dialer.Dialer._on_zero_longpress(d, None, 0.0, 0.0)
        assert d._digits == '011+'

    def test_plus_survives_display_format(self, monkeypatch):
        import xx_phone
        monkeypatch.setattr(xx_phone, 'user_country', lambda *a: 'US')
        d = _make_dialer()
        shown = dialer.Dialer._format_number(d, '+15550001111')
        assert shown == xx_phone.as_you_type('+15550001111', 'US')
        assert shown.startswith('+')


class TestDialerNumberDisplay:
    """Shell keypad entry reads through xx_phone; saved names win."""

    def test_keypad_groups_through_xx_phone(self, monkeypatch):
        import xx_phone
        monkeypatch.setattr(xx_phone, 'user_country', lambda *a: 'US')
        d = _make_dialer()
        assert (dialer.Dialer._format_number(d, '6145550142')
                == xx_phone.as_you_type('6145550142', 'US') == '(614) 555-0142')
        assert (dialer.Dialer._format_number(d, '5550142')
                == xx_phone.as_you_type('5550142', 'US') == '555-0142')
        assert dialer.Dialer._format_number(d, '911') == '911'
        assert dialer.Dialer._format_number(d, '') == ''

    def test_country_lookup_stays_off_dbus(self, monkeypatch):
        import xx_phone
        seen: list = []

        def _capture(*args):
            seen.append(args)
            return 'US'

        monkeypatch.setattr(xx_phone, 'user_country', _capture)
        monkeypatch.setattr(xx_phone, '_auto_sources',
                            lambda: (_ for _ in ()).throw(
                                AssertionError('must not hit D-Bus')))
        d = _make_dialer()
        dialer.Dialer._format_number(d, '6145550142')
        assert seen == [("", "")]

    def test_format_falls_back_to_raw(self, monkeypatch):
        import xx_phone

        def _boom(digits, country=None):
            raise RuntimeError('no engine')

        monkeypatch.setattr(xx_phone, 'as_you_type', _boom)
        d = _make_dialer()
        assert dialer.Dialer._format_number(d, '6145550142') == '6145550142'

    def test_swipe_to_close_lives_on_header_only(self):
        src = inspect.getsource(dialer.Dialer._build)
        assert 'header.add_controller(swipe)' in src
        assert 'root.add_controller(swipe)' not in src


class TestWindowWiring:
    def _shell(self, **overrides):
        shell = object.__new__(window.ShellWindow)
        shell._display_mgr = None
        shell._call_ui = None
        shell._call_bar = None
        shell._calls_owner_warned = False
        shell._call_was_live = False
        shell._blanked = False
        shell._lock_src = None
        for key, value in overrides.items():
            setattr(shell, key, value)
        return shell

    def test_calls_changed_delegates_snapshot(self, monkeypatch):
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda name: False)
        rendered = []
        bar = FakeBar()

        class FakeUI:
            def render_snapshot(self, calls, display_mgr=None):
                rendered.append((list(calls), display_mgr))

        shell = self._shell()
        shell._ensure_call_surfaces = lambda: (FakeUI(), bar)
        calls = [_call(state=4, started_at=1.0)]
        window.ShellWindow._on_calls_changed(shell, calls)
        assert len(rendered) == 1
        assert rendered[0][0] == calls

    def test_calls_changed_empty_still_renders(self, monkeypatch):
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda name: False)
        rendered = []
        bar = FakeBar()

        class FakeUI:
            def render_snapshot(self, calls, display_mgr=None):
                rendered.append(list(calls))

        shell = self._shell()
        shell._ensure_call_surfaces = lambda: (FakeUI(), bar)
        window.ShellWindow._on_calls_changed(shell, [])
        assert rendered == [[]]  # surface closes itself on empty

    def test_calls_defers_to_gnome_calls(self, monkeypatch):
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda name: True)
        shell = self._shell()
        shell._ensure_call_surfaces = lambda: (_ for _ in ()).throw(
            AssertionError('must stay quiet'))
        window.ShellWindow._on_calls_changed(shell, [_call(state=3)])
        assert shell._calls_owner_warned is True

    def test_silence_ringer_stops_surface_ringer(self):
        stopped = []

        class FakeUI:
            _ringer = types.SimpleNamespace(stop=lambda: stopped.append(True))

        shell = self._shell(_call_ui=FakeUI())
        window.ShellWindow.silence_ringer(shell)
        assert stopped == [True]

    def test_silence_ringer_without_surface_falls_back(self, monkeypatch):
        stopped = []
        monkeypatch.setattr('sound.stop', lambda: stopped.append(True))
        shell = self._shell(_call_ui=None)
        window.ShellWindow.silence_ringer(shell)
        assert stopped == [True]

    def test_main_wires_power_press_to_silence(self):
        src = Path(__file__).parent.parent.joinpath(
            'launcher', 'src', 'main.py').read_text()
        assert 'on_power_press=self._shell.silence_ringer' in src


class FakePresentUI:
    """CallUI stand-in that records presents like the real surface."""

    def __init__(self, minimized=False):
        self.rendered = []
        self.presented = 0
        self._minimized = minimized

    def render_snapshot(self, calls, display_mgr=None):
        self.rendered.append(list(calls))

    def present(self):
        self.presented += 1


class TestLockedOutgoing:
    """Outgoing dialing while locked must map the full CallUI above the
    OVERLAY lock (last mapped wins); the CallBar alone sits under it."""

    def _locked_shell(self, base, ui):
        base._lock_screen = types.SimpleNamespace(get_visible=lambda: True)
        base._ensure_call_surfaces = lambda: (ui, FakeBar())
        return base

    def test_locked_outgoing_presents_full_surface(self, monkeypatch):
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda name: False)
        ui = FakePresentUI()
        shell = TestWindowWiring()._shell()
        self._locked_shell(shell, ui)
        window.ShellWindow._on_calls_changed(
            shell, [_call(direction=2, state=1)])
        assert len(ui.rendered) == 1
        assert ui.presented == 1

    def test_unlocked_outgoing_stays_on_bar(self, monkeypatch):
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda name: False)
        ui = FakePresentUI()
        shell = TestWindowWiring()._shell()
        shell._ensure_call_surfaces = lambda: (ui, FakeBar())
        window.ShellWindow._on_calls_changed(
            shell, [_call(direction=2, state=1)])
        assert len(ui.rendered) == 1
        assert ui.presented == 0

    def test_locked_active_needs_no_force_present(self, monkeypatch):
        # Incoming/active pages present themselves in _maybe_present; the
        # window only forces the outgoing page it deliberately skips.
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda name: False)
        ui = FakePresentUI()
        shell = TestWindowWiring()._shell()
        self._locked_shell(shell, ui)
        window.ShellWindow._on_calls_changed(shell, [_call(state=4)])
        assert len(ui.rendered) == 1
        assert ui.presented == 0

    def test_locked_minimized_outgoing_stays_down(self, monkeypatch):
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda name: False)
        ui = FakePresentUI(minimized=True)
        shell = TestWindowWiring()._shell()
        self._locked_shell(shell, ui)
        window.ShellWindow._on_calls_changed(
            shell, [_call(direction=2, state=1)])
        assert len(ui.rendered) == 1
        assert ui.presented == 0

    def test_lock_shown_after_call_represents_surface(self):
        # The other direction: the lock mapping after a live call must
        # leave the call UI on top (both OVERLAY, last mapped wins).
        presented = []
        shell = TestWindowWiring()._shell(
            _call_ui=types.SimpleNamespace(
                present=lambda: presented.append(True)),
            _modem_monitor=types.SimpleNamespace(
                calls=lambda: [_call(state=4)]),
        )
        window.ShellWindow._reassert_call_ui_above_lock(shell)
        assert presented == [True]

    def test_lock_shown_without_live_call_presents_nothing(self):
        presented = []
        shell = TestWindowWiring()._shell(
            _call_ui=types.SimpleNamespace(
                present=lambda: presented.append(True)),
            _modem_monitor=types.SimpleNamespace(calls=lambda: []),
        )
        window.ShellWindow._reassert_call_ui_above_lock(shell)
        assert presented == []


class TestPowerPressHook:
    def _manager(self, monkeypatch, **kwargs):
        monkeypatch.setattr(display_manager, '_all_event_devices',
                            lambda: ['/dev/input/eventX'])
        monkeypatch.setattr(display_manager, '_watch_evdev',
                            lambda *a, **k: None)
        monkeypatch.setattr(display_manager, '_detect_fp_node',
                            lambda: '/dev/input/event9')
        blanks, wakes = [], []
        monkeypatch.setattr(display_manager.DisplayManager, '_blank',
                            lambda self: blanks.append(True))
        monkeypatch.setattr(display_manager.DisplayManager, '_wake',
                            lambda self: wakes.append(True))
        mgr = display_manager.DisplayManager(**kwargs)
        return mgr, blanks, wakes

    def _power(self, value):
        return ('/dev/input/eventX', display_manager.EV_KEY,
                display_manager.KEY_POWER, value)

    def test_power_press_fires_hook_before_blank_toggle(self, monkeypatch):
        pressed = []
        mgr, blanks, wakes = self._manager(
            monkeypatch, on_power_press=lambda: pressed.append(True))
        mgr._on_event(*self._power(1))
        assert pressed == [True]
        mgr._on_event(*self._power(0))
        assert blanks == [True]  # short press still blanks on key-up

    def test_hook_failure_never_breaks_power(self, monkeypatch):
        def _boom():
            raise RuntimeError('ringer gone')

        mgr, blanks, _wakes = self._manager(
            monkeypatch, on_power_press=_boom)
        mgr._on_event(*self._power(1))
        mgr._on_event(*self._power(0))
        assert blanks == [True]

    def test_no_hook_keeps_legacy_behavior(self, monkeypatch):
        mgr, blanks, _wakes = self._manager(monkeypatch)
        mgr._on_event(*self._power(1))
        mgr._on_event(*self._power(0))
        assert blanks == [True]


class TestCallTiles:
    """The in-call tile grid: live toggles, HOLD truth, momentary ADD
    CALL, and the Bluetooth snap-back (no BT audio route exists)."""

    def test_initials_from_name(self):
        assert call_ui.caller_initials('Mara Voss') == 'MV'
        assert call_ui.caller_initials('Ridgeline Dental') == 'RD'
        assert call_ui.caller_initials('Mom') == 'M'
        assert call_ui.caller_initials('') == ''
        assert call_ui.caller_initials('+15550001111') == ''
        assert call_ui.caller_initials(None) == ''

    def test_hold_answers_waiting_call(self, monkeypatch):
        _names(monkeypatch)
        held = []
        ui = _make_ui(
            _hold_accept_fn=lambda m, done: held.append(m) or done(True, None))
        monkeypatch.setattr(call_ui, '_resolve_modem_path', lambda: MODEM)
        ui._live_snapshot = [_call(state=4, started_at=1.0),
                             _call(path=CALL2, number='+15550002222',
                                   direction=1, state=6)]
        ui._waiting_path = CALL2
        btn = FakeToggle()
        btn.set_active(True)
        ui._on_hold_toggled(btn)
        assert held == [MODEM]

    def test_hold_snaps_back_without_waiting(self):
        ui = _make_ui()
        ui._live_snapshot = [_call(state=4, started_at=1.0)]
        btn = FakeToggle()
        btn.set_active(True)
        ui._on_hold_toggled(btn)
        assert btn.get_active() is False

    def test_hold_tracks_held_primary(self):
        ui = _make_ui()
        ui._live_snapshot = [_call(state=5, started_at=1.0)]
        assert ui._primary_held() is True
        ui._live_snapshot = [_call(state=4, started_at=1.0)]
        assert ui._primary_held() is False

    def test_add_call_minimizes_and_snaps_back(self):
        ui = _make_ui()
        btn = FakeToggle()
        btn.set_active(True)
        ui._on_add_call_toggled(btn)
        assert btn.get_active() is False
        assert ui._minimized is True
        assert ui.hidden == 1

    def test_bluetooth_snaps_back(self):
        ui = _make_ui()
        btn = FakeToggle()
        btn.set_active(True)
        ui._on_bt_toggled(btn)
        assert btn.get_active() is False
