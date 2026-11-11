"""CallUI button flow + CallBar show/hide contract (P2-D, W1-A async).

window.py already calls ``CallBar.show_bar(number)`` / ``hide_bar()`` and
constructs ``CallUI(on_accept=..., on_decline=...)``, so those signatures
are pinned here. Accept/Decline/Hangup run through the async transport
seam: UI transitions happen only on the success callback, a pending-action
flag guards double-fire, failures keep the surface consistent and log.
Widget construction never runs — instances are built with ``object.__new__``
and fake labels/stacks, matching the test_quick_sliders idiom.
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
    glib.idle_add = lambda *a, **k: 1
    glib.timeout_add = lambda *a, **k: 1
    glib.timeout_add_seconds = lambda *a, **k: 1
    glib.source_remove = lambda *a, **k: None
    glib.Variant = lambda *a, **k: None

    gdk = types.ModuleType('gi.repository.Gdk')
    gdk.Display = types.SimpleNamespace(get_default=lambda: None)

    gio = types.ModuleType('gi.repository.Gio')
    gio.BusType = types.SimpleNamespace(SYSTEM=1)
    gio.DBusSignalFlags = types.SimpleNamespace(NONE=0)
    gio.DBusCallFlags = types.SimpleNamespace(NONE=0)
    gio.bus_get_sync = lambda *a, **k: None

    gtk = types.ModuleType('gi.repository.Gtk')
    gtk.Window = type('Window', (), {})

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


_install_fake_gi()

import call_ui  # noqa: E402  (needs fake gi modules installed first)
import modem_monitor  # noqa: E402


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

    def set_visible(self, value):
        self.visible = bool(value)


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


def _make_call_ui(accept_fn=None, hangup_fn=None):
    ui = call_ui.CallUI.__new__(call_ui.CallUI)
    ui._events = []
    ui._on_accept = lambda: ui._events.append('accept')
    ui._on_decline = lambda: ui._events.append('decline')
    ui._on_hangup = lambda: ui._events.append('hangup')
    ui._accept_fn = accept_fn or (lambda path, done: done(True, None))
    ui._hangup_fn = hangup_fn or (lambda path, done: done(True, None))
    ui._hold_accept_fn = lambda modem, done: done(True, None)
    ui._dtmf_fn = lambda path, digit, done: done(True, None)
    ui._incoming_call_path = '/org/freedesktop/ModemManager1/Call/3'
    ui._incoming_path = '/org/freedesktop/ModemManager1/Call/3'
    ui._primary_path = '/org/freedesktop/ModemManager1/Call/3'
    ui._waiting_path = None
    ui._action_path = None
    ui._pending_action = None
    ui._surface_live = True
    ui._minimized = False
    ui._presented_page = None
    ui._presented_path = None
    ui._live_snapshot = []
    ui._current_caller = 'Incoming call'
    ui._current_number = '+15550001111'
    ui._muted = False
    ui._speakerphone = False
    ui._timed_path = None
    ui._call_start_mono = None
    ui._close_src = None
    ui._close_btns = []
    ui._bar = None
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
    ui.present = lambda: setattr(ui, 'presented', ui.presented + 1)
    ui.hide = lambda: setattr(ui, 'hidden', ui.hidden + 1)
    return ui


def _make_call_bar():
    bar = call_ui.CallBar.__new__(call_ui.CallBar)
    bar._label = FakeLabel('In call')
    bar._shown = False
    bar.presented = 0
    bar.hidden = 0
    bar.present = lambda: setattr(bar, 'presented', bar.presented + 1)
    bar.hide = lambda: setattr(bar, 'hidden', bar.hidden + 1)
    return bar


class TestWindowContract:
    def test_call_bar_has_show_hide_bar_used_by_window(self):
        sig = inspect.signature(call_ui.CallBar.show_bar)
        assert list(sig.parameters) == ['self', 'number']
        assert list(inspect.signature(call_ui.CallBar.hide_bar).parameters) == ['self']

    def test_show_incoming_still_takes_caller_and_number(self):
        sig = inspect.signature(call_ui.CallUI.show_incoming)
        assert list(sig.parameters)[:3] == ['self', 'caller', 'number']

    def test_default_fns_delegate_to_modem_monitor(self, monkeypatch):
        seen = []
        monkeypatch.setattr(modem_monitor, 'accept_call',
                            lambda p, done: seen.append(('accept', p)) or done(True, None))
        monkeypatch.setattr(modem_monitor, 'hangup_call',
                            lambda p, done: seen.append(('hangup', p)) or done(False, 'boom'))
        got = []
        call_ui._default_accept('/mm1/Call/1', lambda ok, err: got.append(('a', ok, err)))
        call_ui._default_hangup('/mm1/Call/2', lambda ok, err: got.append(('h', ok, err)))
        assert seen == [('accept', '/mm1/Call/1'), ('hangup', '/mm1/Call/2')]
        assert got == [('a', True, None), ('h', False, 'boom')]

    def test_resolve_call_path_reads_monitor(self, monkeypatch):
        monkeypatch.setattr(modem_monitor, '_last_monitor', None)
        assert call_ui._resolve_call_path() is None


class TestAcceptFlow:
    def test_accept_success_transitions_exactly_once(self, monkeypatch):
        monkeypatch.setattr(call_ui, '_set_audio_route', lambda earpiece: None)
        dispatches = []
        ui = _make_call_ui(
            accept_fn=lambda p, done: dispatches.append(p) or done(True, None))
        ui.show_incoming('Mom', '+15550001111', '/org/freedesktop/ModemManager1/Call/3')
        ui._on_accept_clicked(None)
        assert dispatches == ['/org/freedesktop/ModemManager1/Call/3']
        assert ui._events == ['accept']
        assert ui._stack.visible == 'active'
        assert ui.hidden == 0

    def test_transition_only_on_success_callback(self):
        pending_cb = []
        ui = _make_call_ui(accept_fn=lambda p, done: pending_cb.append(done))
        ui.show_incoming('Mom', '+15550001111', '/org/freedesktop/ModemManager1/Call/3')
        ui._on_accept_clicked(None)
        assert ui._events == []
        assert ui._stack.visible == 'incoming'
        pending_cb.pop(0)(True, None)
        assert ui._events == ['accept']
        assert ui._stack.visible == 'active'

    def test_show_incoming_resolves_path_from_monitor(self, monkeypatch):
        class StubMonitor:
            _active_call_path = '/org/freedesktop/ModemManager1/Call/9'
        monkeypatch.setattr(modem_monitor, '_last_monitor', StubMonitor())
        ui = _make_call_ui()
        ui.show_incoming('', '+15550002222')
        assert ui._incoming_call_path == '/org/freedesktop/ModemManager1/Call/9'
        assert ui._inc_caller.get_text() == '+15550002222'

    def test_accept_failure_closes_surface_and_logs(self, caplog):
        ui = _make_call_ui(accept_fn=lambda p, done: done(False, 'mm1 timed out'))
        ui.show_incoming('Mom', '+15550001111', '/org/freedesktop/ModemManager1/Call/3')
        with caplog.at_level(logging.WARNING):
            ui._on_accept_clicked(None)
        assert 'accept' not in ui._events
        assert ui.hidden == 1
        assert ui._pending_action is None  # buttons responsive again
        assert any('accept' in r.getMessage() and 'mm1 timed out' in r.getMessage()
                   for r in caplog.records)

    def test_double_tap_dispatches_once(self):
        pending_cb = []
        ui = _make_call_ui(accept_fn=lambda p, done: pending_cb.append(done))
        ui._on_accept_clicked(None)
        ui._on_accept_clicked(None)
        assert len(pending_cb) == 1
        pending_cb.pop(0)(True, None)
        assert ui._events == ['accept']
        assert ui._pending_action is None  # guard released after completion

    def test_pending_accept_blocks_decline_too(self):
        pending_cb = []
        ui = _make_call_ui(accept_fn=lambda p, done: pending_cb.append(done),
                           hangup_fn=lambda p, done: pending_cb.append(done))
        ui._on_accept_clicked(None)
        ui._on_decline_clicked(None)
        assert len(pending_cb) == 1
        assert ui._events == []

    def test_accept_without_path_never_calls_transport(self, caplog):
        dispatched = []
        ui = _make_call_ui(accept_fn=lambda p, done: dispatched.append(p))
        ui._incoming_call_path = None
        ui._incoming_path = None
        with caplog.at_level(logging.WARNING):
            ui._on_accept_clicked(None)
        assert dispatched == []
        assert ui._events == []

    def test_stale_accept_after_remote_end_is_ignored(self, monkeypatch):
        monkeypatch.setattr(call_ui, '_set_audio_route', lambda earpiece: None)
        pending_cb = []
        ui = _make_call_ui(accept_fn=lambda p, done: pending_cb.append(done))
        ui._on_accept_clicked(None)
        ui.end_call()  # remote hung up while the accept was in flight
        pending_cb.pop(0)(True, None)
        assert ui._events == []
        assert ui.hidden == 1  # stayed ended; no resurrection of the call


class TestDeclineFlow:
    def test_decline_success_hides_on_callback(self):
        pending_cb = []
        ui = _make_call_ui(hangup_fn=lambda p, done: pending_cb.append(done))
        ui._on_decline_clicked(None)
        assert ui._events == []
        assert ui.hidden == 0
        pending_cb.pop(0)(True, None)
        assert ui._events == ['decline']
        assert ui.hidden == 1
        assert ui._incoming_call_path is None

    def test_decline_failure_closes_surface_and_logs(self, caplog):
        ui = _make_call_ui(hangup_fn=lambda p, done: done(False, 'No such object'))
        with caplog.at_level(logging.WARNING):
            ui._on_decline_clicked(None)
        assert ui._events == []
        assert ui.hidden == 1
        assert ui._pending_action is None  # buttons responsive again
        assert any('decline' in r.getMessage() and 'No such object' in r.getMessage()
                   for r in caplog.records)


class TestHangupFlow:
    def test_hangup_success_ends_call_on_callback(self, monkeypatch):
        monkeypatch.setattr(call_ui, '_set_audio_route', lambda earpiece: None)
        pending_cb = []
        ui = _make_call_ui(hangup_fn=lambda p, done: pending_cb.append(done))
        ui._on_hangup_clicked(None)
        assert ui._events == []
        assert ui.hidden == 0
        pending_cb.pop(0)(True, None)
        assert ui._events == ['hangup']
        assert ui.hidden == 1
        assert ui._incoming_call_path is None

    def test_hangup_failure_closes_surface_and_logs(self, caplog):
        ui = _make_call_ui(hangup_fn=lambda p, done: done(False, 'call stuck'))
        with caplog.at_level(logging.WARNING):
            ui._on_hangup_clicked(None)
        assert ui._events == []
        assert ui.hidden == 1
        assert ui._pending_action is None  # buttons responsive again
        assert any('hang up' in r.getMessage() and 'call stuck' in r.getMessage()
                   for r in caplog.records)

    def test_stale_hangup_after_remote_end_is_ignored(self, monkeypatch):
        monkeypatch.setattr(call_ui, '_set_audio_route', lambda earpiece: None)
        pending_cb = []
        ui = _make_call_ui(hangup_fn=lambda p, done: pending_cb.append(done))
        ui._on_hangup_clicked(None)
        ui.end_call()  # remote beat us to it
        pending_cb.pop(0)(True, None)
        assert ui._events == []
        assert ui.hidden == 1


class TestCallBar:
    def test_show_bar_renders_number_once(self):
        bar = _make_call_bar()
        bar.show_bar('+15551230000')
        assert bar._label.get_text() == '+15551230000'
        assert bar.presented == 1
        bar.show_bar('+15551230000')
        assert bar.presented == 1

    def test_show_bar_updates_text_while_shown(self):
        bar = _make_call_bar()
        bar.show_bar('111')
        bar.show_bar('222')
        assert bar._label.get_text() == '222'
        assert bar.presented == 1

    def test_hide_bar_idempotent(self):
        bar = _make_call_bar()
        bar.hide_bar()
        assert bar.hidden == 0
        bar.show_bar('555')
        bar.hide_bar()
        bar.hide_bar()
        assert bar.hidden == 1
        assert bar._shown is False

    def test_show_after_hide_presents_again(self):
        bar = _make_call_bar()
        bar.show_bar('555')
        bar.hide_bar()
        bar.show_bar('556')
        assert bar.presented == 2
        assert bar._label.get_text() == '556'

    def test_empty_number_falls_back_to_label(self):
        bar = _make_call_bar()
        bar.show_bar('')
        assert bar._label.get_text() == 'In call'


class _DtmfBus:
    def __init__(self):
        self.sent = []

    def call(self, bus_name, path, iface, method, params, *rest):
        self.sent.append((path, method, params))


def _dtmf_seams(monkeypatch):
    bus = _DtmfBus()
    monkeypatch.setattr(call_ui.Gio, 'bus_get_sync', lambda *a, **k: bus)
    monkeypatch.setattr(call_ui.GLib, 'Variant',
                        lambda fmt, value: (fmt, value))
    return bus


def test_dtmf_tone_is_the_shared_filter():
    for tone in '0123456789*#ABCD':
        assert call_ui.dtmf_tone(tone) == tone
    assert call_ui.dtmf_tone('b') == 'B'
    for bad in ('+', ',', 'p', 'x', '12', '', None):
        assert call_ui.dtmf_tone(bad) is None


def test_default_dtmf_never_sends_an_invalid_digit(monkeypatch):
    bus = _dtmf_seams(monkeypatch)
    results = []
    for bad in ('+', 'p', '55', ''):
        call_ui._default_dtmf('/Call/1', bad,
                              lambda ok, err: results.append((ok, err)))
    assert bus.sent == []
    assert [ok for ok, _err in results] == [False] * 4
    assert results[0][1] == "invalid DTMF digit: '+'"


def test_default_dtmf_sends_the_normalized_tone(monkeypatch):
    bus = _dtmf_seams(monkeypatch)
    call_ui._default_dtmf('/Call/1', ' d ', lambda ok, err: None)
    assert bus.sent == [('/Call/1', 'SendDtmf', ('(s)', ('D',)))]
