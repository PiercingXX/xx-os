"""W5 call screening + Settings fixes (battery names, charging probe, log).

Headless: D-Bus peers are recording fakes mirroring the real Gio traps
(tuple replies, two-arg callbacks, no trailing user_data); the
dbus-run-session test proves the real dialer service answers the real
shell consult through activation.
"""
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'launcher' / 'src'))

import call_screen  # noqa: E402
import call_wiring  # noqa: E402


# ----------------------------------------------------------------------
# Fake D-Bus bank (mirrors the real Gio traps)


class FakeVariant:
    def __init__(self, fmt, value):
        self.fmt = fmt
        self.value = value

    def unpack(self):
        return self.value


class FakeGlib:
    Error = type('Error', (Exception,), {})

    def Variant(self, fmt, value):
        return FakeVariant(fmt, value)

    class VariantType:
        @staticmethod
        def new(fmt):
            return ('VariantType', fmt)


class FakeGio:
    BusType = types.SimpleNamespace(SESSION='session', SYSTEM='system')
    DBusCallFlags = types.SimpleNamespace(NONE=0, NO_AUTO_START=1)

    def bus_get_sync(self, *_args):
        raise AssertionError('tests inject the bus')


class FakeBus:
    """Recording session bus: consult replies are programmable."""

    def __init__(self, verdict=('ring', 'ok')):
        self.verdict = verdict
        self.calls = []
        self.pending = []
        self.sync_probes = []
        self.fail_call_finish = False

    def call_sync(self, *args, **kwargs):
        self.sync_probes.append((args, kwargs))
        raise FakeGlib.Error('no owner')

    def call(self, bus_name, path, iface, method, params=None,
             reply_type=None, flags=None, timeout=-1, cancellable=None,
             callback=None):
        self.calls.append({
            'bus': bus_name, 'path': path, 'iface': iface,
            'method': method, 'timeout': timeout, 'flags': flags,
            'reply_type': reply_type,
            'params': params.unpack() if params is not None else None,
        })
        self.pending.append(callback)

    def call_finish(self, _result):
        if self.fail_call_finish:
            raise FakeGlib.Error('timed out')
        return FakeVariant('(ss)', self.verdict)

    def drain(self):
        while self.pending:
            self.pending.pop(0)(self, object())


class TestConsultReached:
    """The consult reports whether the service answered, so the shell can
    tell a service verdict (service owns the silenced-call card) apart
    from a fail-open fallback (shell posts its own card)."""

    def _consult(self, bus, **kwargs):
        got, reached = [], []
        call_screen.consult_check_number(
            '+15550001111', lambda v, r: got.append((v, r)),
            bus=bus, gio=FakeGio(), glib=FakeGlib(),
            on_reached=reached.append, **kwargs)
        return got, reached, bus

    def test_answered_verdict_reports_reached(self):
        bus = FakeBus(verdict=('silence', 'unknown caller'))
        got, reached, _ = self._consult(bus)
        assert got == []
        bus.drain()
        assert got == [('silence', 'unknown caller')]
        assert reached == [True]

    def test_bad_verdict_still_reports_reached(self):
        bus = FakeBus(verdict=('voicemail', 'send to voicemail'))
        got, reached, _ = self._consult(bus)
        bus.drain()
        assert got == [('ring', 'fail-open')]
        assert reached == [True]

    def test_error_reports_not_reached(self):
        bus = FakeBus()
        bus.fail_call_finish = True
        got, reached, _ = self._consult(bus)
        bus.drain()
        assert got == [('ring', 'fail-open')]
        assert reached == [False]

    def test_dispatch_failure_reports_not_reached(self):
        class _BoomBus(FakeBus):
            def call(self, *args, **kwargs):
                raise FakeGlib.Error('no bus')

        bus = _BoomBus()
        got, reached, _ = self._consult(bus)
        assert got == [('ring', 'fail-open')]
        assert reached == [False]
        assert bus.calls == []

    def test_without_callback_keeps_working(self):
        bus = FakeBus(verdict=('ring', 'saved contact'))
        got = []
        call_screen.consult_check_number(
            '+15550001111', lambda v, r: got.append((v, r)),
            bus=bus, gio=FakeGio(), glib=FakeGlib())
        bus.drain()
        assert got == [('ring', 'saved contact')]


class TestConsultShape:
    def test_ring_verdict_delivered(self):
        bus = FakeBus(verdict=('ring', 'saved contact'))
        got = []
        call_screen.consult_check_number(
            '+15550001111', lambda v, r: got.append((v, r)),
            bus=bus, gio=FakeGio(), glib=FakeGlib())
        assert got == []
        call = bus.calls[0]
        assert (call['bus'], call['path'], call['iface'],
                call['method']) == (
            call_screen.BUS_NAME, call_screen.OBJECT_PATH,
            call_screen.INTERFACE, 'CheckNumber')
        assert call['timeout'] == 2000
        assert call['flags'] == 0
        assert call['params'] == ('+15550001111',)
        assert bus.sync_probes == []
        bus.drain()
        assert got == [('ring', 'saved contact')]

    def test_silence_and_block_verdicts(self):
        for verdict in (('silence', 'unknown caller'), ('block', 'blocked')):
            bus = FakeBus(verdict=verdict)
            got = []
            call_screen.consult_check_number(
                '+15550001111', lambda v, r, got=got: got.append((v, r)),
                bus=bus, gio=FakeGio(), glib=FakeGlib())
            bus.drain()
            assert got == [verdict]

    def test_unknown_verdict_fails_open(self):
        bus = FakeBus(verdict=('voicemail', 'send to voicemail'))
        got = []
        call_screen.consult_check_number(
            '+15550001111', lambda v, r: got.append((v, r)),
            bus=bus, gio=FakeGio(), glib=FakeGlib())
        bus.drain()
        assert got == [('ring', 'fail-open')]

    def test_error_fails_open(self):
        bus = FakeBus()
        bus.fail_call_finish = True
        got = []
        call_screen.consult_check_number(
            '+15550001111', lambda v, r: got.append((v, r)),
            bus=bus, gio=FakeGio(), glib=FakeGlib())
        bus.drain()
        assert got == [('ring', 'fail-open')]

    def test_timeout_fails_open(self):
        bus = FakeBus()
        got = []
        call_screen.consult_check_number(
            '+15550001111', lambda v, r: got.append((v, r)),
            bus=bus, gio=FakeGio(), glib=FakeGlib())
        assert got == []
        assert bus.calls[0]['timeout'] == 2000
        bus.fail_call_finish = True
        bus.drain()
        assert got == [('ring', 'fail-open')]

    def test_service_not_running_still_calls(self):
        bus = FakeBus(verdict=('ring', 'starred'))
        got = []
        call_screen.consult_check_number(
            '+15550001111', lambda v, r: got.append((v, r)),
            bus=bus, gio=FakeGio(), glib=FakeGlib())
        assert len(bus.calls) == 1
        assert bus.sync_probes == []
        assert bus.calls[0]['flags'] == 0
        bus.drain()
        assert got == [('ring', 'starred')]

    def test_withheld_passes_empty_string(self):
        bus = FakeBus(verdict=('silence', 'hidden caller'))
        got = []
        call_screen.consult_check_number(
            '', lambda v, r: got.append((v, r)),
            bus=bus, gio=FakeGio(), glib=FakeGlib())
        assert bus.calls[0]['params'] == ('',)
        bus.drain()
        assert got == [('silence', 'hidden caller')]

    def test_reply_type_is_variant_type(self):
        gi = pytest.importorskip('gi')
        gi.require_version('GLib', '2.0')
        from gi.repository import GLib as RealGLib
        bus = FakeBus()
        got = []
        call_screen.consult_check_number(
            '+15550001111', lambda v, r: got.append((v, r)), bus=bus)
        reply_type = bus.calls[0]['reply_type']
        assert isinstance(reply_type, RealGLib.VariantType)
        assert reply_type.dup_string() == '(ss)'
        bus.drain()
        assert got == [('ring', 'ok')]

    def test_no_owner_probe_and_no_auto_start_flag(self):
        pytest.importorskip('gi')
        from gi.repository import Gio as RealGio
        bus = FakeBus()
        call_screen.consult_check_number(
            '+15550001111', lambda _v, _r: None, bus=bus)
        assert bus.sync_probes == []
        assert bus.calls[0]['flags'] == RealGio.DBusCallFlags.NONE
        assert (bus.calls[0]['flags']
                & RealGio.DBusCallFlags.NO_AUTO_START) == 0


class TestNoteCall:
    def test_note_call_shape(self):
        bus = FakeBus()
        call_screen.note_call('+15550001111', 'incoming', 1790000000000,
                              30000, 'answered',
                              bus=bus, gio=FakeGio(), glib=FakeGlib())
        assert len(bus.calls) == 1
        call = bus.calls[0]
        assert (call['bus'], call['path'], call['iface'],
                call['method']) == (
            call_screen.BUS_NAME, call_screen.OBJECT_PATH,
            call_screen.INTERFACE, 'NoteCall')
        assert call['params'] == ('+15550001111', 'incoming',
                                  1790000000000, 30000, 'answered')

    def test_note_call_error_only_logs(self, caplog):
        bus = FakeBus()
        bus.fail_call_finish = True
        import logging
        with caplog.at_level(logging.DEBUG, logger='piercing.call_screen'):
            call_screen.note_call('+15550001111', 'outgoing', 1000, 5000,
                                  'missed',
                                  bus=bus, gio=FakeGio(), glib=FakeGlib())
            bus.drain()


def _wiring_win(events, consult=None, hangups=None, notes=None,
               notifs=None, dnd_rings=True):
    shown = events
    consult_box = {}

    def _fake_consult(number, on_verdict, **_kwargs):
        consult_box['number'] = number
        verdict, reason = consult if consult is not None else ('ring', 'ok')
        if verdict is not None:
            on_verdict(verdict, reason)
        return None

    call_ui = SimpleNamespace(
        show_incoming=lambda c, n: shown.append(('incoming', c, n)),
        start_ringing=lambda tone='ringtone.ogg': shown.append(
            ('ring', tone)),
        stop_ringing=lambda: shown.append('stop-ring'),
        end_call=lambda: shown.append('end-call'),
        show_active=lambda c, n: shown.append(('active', c, n)),
        present=lambda: shown.append('present'),
        render_snapshot=lambda *a, **k: shown.append('render'),
        _minimized=False,
    )
    bar = SimpleNamespace(
        show_bar=lambda t: shown.append(('bar', t)),
        hide_bar=lambda: shown.append('hide-bar'),
    )
    dnd = SimpleNamespace(
        is_active=lambda: not dnd_rings,
        is_exception=lambda _n: False,
        note_call=lambda _n: shown.append('dnd-noted'),
    )
    config = SimpleNamespace(sound_ringtone=True,
                             label_for=lambda _a, name: name)
    app = SimpleNamespace(
        _on_notification=(lambda i, a, s, b, d, h: notifs.append(
            (i, a, s, b)) if notifs is not None else None))
    win = SimpleNamespace(
        dnd_state=dnd, config=config, _call_ui=call_ui, _call_bar=bar,
        _calls_owner_warned=False, _screen_pending=None,
        _screen_tracked={}, _screen_done=set(), _call_notif_seq=0,
        is_locked=lambda: False,
        _sync_call_inhibit=lambda _calls: None,
        _ensure_call_surfaces=lambda: (call_ui, bar),
        get_application=lambda: app,
        _ensure_shade=lambda: SimpleNamespace(
            add_notification=lambda i, a, s, b: notifs.append((i, a, s, b))
            if notifs is not None else None),
    )
    return win, call_ui, _fake_consult, consult_box


def _ensure_settings_page():
    import settings_page  # noqa: F401  (import first: breaks the cycle)
    return settings_page


class TestRingerTone:
    def test_unknown_rings_use_the_unknown_tone(self):
        for reason in ("unknown caller", "you called them Tue",
                       "repeat caller", "rang · expecting a call",
                       "Unknown, outside 09–17", "silenced by pattern"):
            assert call_wiring.ringer_tone_for("ring", reason) == \
                "unknown.ogg", reason

    def test_known_and_fallback_rings_use_the_default_tone(self):
        for reason in ("saved contact", "starred", "business",
                       "emergency callback", "fail-open", "ok",
                       "Unknown, outside business window"):
            assert call_wiring.ringer_tone_for("ring", reason) == \
                "ringtone.ogg", reason

    def test_non_ring_verdicts_use_the_default_tone(self):
        assert call_wiring.ringer_tone_for("silence",
                                           "unknown caller") == \
            "ringtone.ogg"
        assert call_wiring.ringer_tone_for("block", "blocked") == \
            "ringtone.ogg"

    def test_unknown_verdict_rings_unknown_tone(self, monkeypatch):
        import modem_monitor
        events = []
        win, _ui, fake_consult, _box = _wiring_win(
            events, consult=('ring', 'unknown caller'))
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda _n: False)
        monkeypatch.setattr(call_screen, 'consult_check_number',
                            fake_consult)
        call_wiring.CallWiring(win)._on_call_incoming('Mom', '+15550001111')
        assert ('ring', 'unknown.ogg') in events


class TestWiringVerdicts:
    def test_ring_starts_ringer(self, monkeypatch):
        import modem_monitor
        events = []
        win, _ui, fake_consult, box = _wiring_win(events)
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda _n: False)
        monkeypatch.setattr(call_screen, 'consult_check_number',
                            fake_consult)
        call_wiring.CallWiring(win)._on_call_incoming('Mom', '+15550001111')
        assert ('incoming', 'Mom', '+15550001111') in events
        assert ('ring', 'ringtone.ogg') in events
        assert box['number'] == '+15550001111'

    def test_silence_never_rings(self, monkeypatch):
        import modem_monitor
        events = []
        win, _ui, fake_consult, _box = _wiring_win(
            events, consult=('silence', 'unknown caller'))
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda _n: False)
        monkeypatch.setattr(call_screen, 'consult_check_number',
                            fake_consult)
        call_wiring.CallWiring(win)._on_call_incoming('Mom', '+15550001111')
        assert ('incoming', 'Mom', '+15550001111') in events
        assert 'ring' not in events

    def test_ended_missed_reports_without_posting_a_card(self, monkeypatch):
        import modem_monitor
        events, notifs, notes = [], [], []
        win, _ui, fake_consult, _box = _wiring_win(
            events, consult=('silence', 'unknown caller'), notifs=notifs)
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda _n: False)
        monkeypatch.setattr(call_screen, 'consult_check_number',
                            fake_consult)
        monkeypatch.setattr(call_screen, 'note_call',
                            lambda *a, **_k: notes.append(a))
        wiring = call_wiring.CallWiring(win)
        wiring._on_call_incoming('Mom', '+15550001111')
        wiring._on_call_ended()
        assert notes and notes[0][1] == 'incoming'
        assert notes[0][4] == 'missed'
        assert notifs == []

    def test_answered_reports_answered(self, monkeypatch):
        import modem_monitor
        events, notes = [], []
        win, _ui, fake_consult, _box = _wiring_win(events)
        win._call_ui = SimpleNamespace(
            stop_ringing=lambda: None,
            show_active=lambda c, n: events.append(('active', c, n)),
            end_call=lambda: events.append('end-call'),
        )
        win._call_bar = SimpleNamespace(show_bar=lambda _t: None,
                                        hide_bar=lambda: None)
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda _n: False)
        monkeypatch.setattr(call_screen, 'consult_check_number',
                            fake_consult)
        monkeypatch.setattr(call_screen, 'note_call',
                            lambda *a, **_k: notes.append(a))
        wiring = call_wiring.CallWiring(win)
        wiring._on_call_incoming('Mom', '+15550001111')
        wiring._on_call_answered('Mom', '+15550001111')
        wiring._on_call_ended()
        assert notes and notes[0][4] == 'answered'
        assert notes[0][1] == 'incoming'

    def test_block_hangs_up_no_surface(self, monkeypatch, caplog):
        import logging
        import modem_monitor
        events, notes, hangups = [], [], []
        win, _ui, fake_consult, _box = _wiring_win(
            events, consult=('block', 'blocked'))
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda _n: False)
        monkeypatch.setattr(modem_monitor, 'hangup_call',
                            lambda p, cb: (hangups.append(p), cb(True, None)))
        monkeypatch.setattr(modem_monitor, 'active_call_path',
                            lambda: '/mm/Call/7')
        monkeypatch.setattr(call_screen, 'consult_check_number',
                            fake_consult)
        monkeypatch.setattr(call_screen, 'note_call',
                            lambda *a, **_k: notes.append(a))
        with caplog.at_level(logging.INFO, logger='piercing.window'):
            call_wiring.CallWiring(win)._on_call_incoming(
                'Spam', '+15550009999')
        assert hangups == ['/mm/Call/7']
        assert 'ring' not in events
        assert 'end-call' in events
        assert any('blocked call from +15550009999' in r.message
                   for r in caplog.records
                   if r.name == 'piercing.window')

    def test_withheld_passes_empty_string(self, monkeypatch):
        import modem_monitor
        events = []
        win, _ui, fake_consult, box = _wiring_win(events)
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda _n: False)
        monkeypatch.setattr(call_screen, 'consult_check_number',
                            fake_consult)
        call_wiring.CallWiring(win)._on_call_incoming('', '')
        assert box['number'] == ''

    def test_outgoing_end_reports_outgoing(self, monkeypatch):
        import modem_monitor
        events, notes = [], []
        win, _ui, _fake, _box = _wiring_win(events)
        monkeypatch.setattr(call_screen, 'note_call',
                            lambda *a, **_k: notes.append(a))
        wiring = call_wiring.CallWiring(win)
        out = SimpleNamespace(path='/mm/Call/9', number='+15550002222',
                              direction=modem_monitor._DIR_OUTGOING,
                              state=modem_monitor._STATE_DIALING)
        wiring._on_calls_changed([out])
        wiring._on_calls_changed([])
        assert notes and notes[0][1] == 'outgoing'
        assert notes[0][0] == '+15550002222'
        assert notes[0][4] == 'missed'

    def test_outgoing_answered_reports_answered(self, monkeypatch):
        import modem_monitor
        notes = []
        win, _ui, _fake, _box = _wiring_win([])
        monkeypatch.setattr(call_screen, 'note_call',
                            lambda *a, **_k: notes.append(a))
        wiring = call_wiring.CallWiring(win)
        out = SimpleNamespace(path='/mm/Call/9', number='+15550002222',
                              direction=modem_monitor._DIR_OUTGOING,
                              state=modem_monitor._STATE_ACTIVE)
        wiring._on_calls_changed([out])
        wiring._on_calls_changed([])
        assert notes and notes[0][4] == 'answered'


class TestLateVerdicts:
    """A slow consult (cold activation, up to the 2 s timeout) lands after
    the user already answered, declined or hung up: the verdict is recorded
    for NoteCall/logging but never acted on (no late ring during an active
    call, no hangup of an answered call)."""

    def _deferred_win(self, events, monkeypatch, hangups=None):
        import modem_monitor
        win, _ui, _fake, _box = _wiring_win(events)
        box = {}

        def _deferred(number, on_verdict, **_kwargs):
            box['callback'] = on_verdict
            box['number'] = number

        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda _n: False)
        monkeypatch.setattr(call_screen, 'consult_check_number', _deferred)
        if hangups is not None:
            monkeypatch.setattr(
                modem_monitor, 'hangup_call',
                lambda p, cb: (hangups.append(p), cb(True, None)))
            monkeypatch.setattr(modem_monitor, 'active_call_path',
                                lambda: '/mm/Call/7')
        monkeypatch.setattr(modem_monitor, '_last_monitor', None)
        call_wiring.CallWiring(win)._on_call_incoming('Mom', '+15550001111')
        assert box['number'] == '+15550001111'
        assert ('incoming', 'Mom', '+15550001111') in events
        return win, box

    def _assert_record_only(self, win, events, box, verdict, reason,
                            hangups):
        before = list(events)
        box['callback'](verdict, reason)
        pending = win._screen_pending
        assert pending['verdict'] == verdict
        assert pending['reason'] == reason
        assert events == before
        assert hangups == []
        assert pending.get('blocked') is None
        assert pending.get('silenced') is None

    @pytest.mark.parametrize(('verdict', 'reason'), [
        ('ring', 'saved contact'),
        ('silence', 'unknown caller'),
        ('block', 'blocked'),
    ])
    def test_verdict_after_answer_records_without_acting(
            self, monkeypatch, verdict, reason):
        import sound
        events, hangups = [], []
        win, box = self._deferred_win(events, monkeypatch, hangups)
        monkeypatch.setattr(sound, 'stop', lambda: None)
        call_wiring.CallWiring(win)._on_call_answered('Mom', '+15550001111')
        assert win._screen_pending['answered'] is True
        self._assert_record_only(win, events, box, verdict, reason, hangups)

    @pytest.mark.parametrize(('verdict', 'reason'), [
        ('ring', 'saved contact'),
        ('silence', 'unknown caller'),
        ('block', 'blocked'),
    ])
    def test_verdict_after_reject_records_without_acting(
            self, monkeypatch, verdict, reason):
        import modem_monitor
        events, hangups = [], []
        win, box = self._deferred_win(events, monkeypatch, hangups)
        win._screen_pending['rejected'] = True
        self._assert_record_only(win, events, box, verdict, reason, hangups)
        assert modem_monitor._last_monitor is None

    @pytest.mark.parametrize(('verdict', 'reason'), [
        ('ring', 'saved contact'),
        ('silence', 'unknown caller'),
        ('block', 'blocked'),
    ])
    def test_verdict_after_mm_left_ringing_records_without_acting(
            self, monkeypatch, verdict, reason):
        import modem_monitor
        events, hangups = [], []
        win, box = self._deferred_win(events, monkeypatch, hangups)
        live = SimpleNamespace(path='/mm/Call/7', number='+15550001111',
                               direction=modem_monitor._DIR_INCOMING,
                               state=modem_monitor._STATE_ACTIVE)
        monkeypatch.setattr(modem_monitor, '_last_monitor',
                            SimpleNamespace(calls=lambda: [live]))
        self._assert_record_only(win, events, box, verdict, reason, hangups)

    @pytest.mark.parametrize(('verdict', 'reason'), [
        ('ring', 'saved contact'),
        ('silence', 'unknown caller'),
        ('block', 'blocked'),
    ])
    def test_verdict_after_end_does_nothing(
            self, monkeypatch, verdict, reason):
        import sound
        events, hangups, notes = [], [], []
        win, box = self._deferred_win(events, monkeypatch, hangups)
        monkeypatch.setattr(sound, 'stop', lambda: None)
        monkeypatch.setattr(call_screen, 'note_call',
                            lambda *a, **_k: notes.append(a))
        call_wiring.CallWiring(win)._on_call_ended()
        assert win._screen_pending is None
        box['callback'](verdict, reason)
        assert 'ring' not in events
        assert hangups == []

    def test_verdict_while_still_ringing_still_acts(self, monkeypatch):
        import modem_monitor
        events, hangups = [], []
        win, box = self._deferred_win(events, monkeypatch, hangups)
        ringing = SimpleNamespace(path='/mm/Call/7', number='+15550001111',
                                   direction=modem_monitor._DIR_INCOMING,
                                   state=modem_monitor._STATE_RINGING_IN)
        monkeypatch.setattr(modem_monitor, '_last_monitor',
                            SimpleNamespace(calls=lambda: [ringing]))
        box['callback']('ring', 'saved contact')
        assert ('ring', 'ringtone.ogg') in events


class TestDisplayNames:
    def _win_with_entries(self, entries):
        return SimpleNamespace(
            app_index=SimpleNamespace(entries=entries),
            config=SimpleNamespace(
                label_for=lambda _a, name: name))

    def test_battery_use_prefers_desktop_name(self):
        _ensure_settings_page()
        import settings_battery_use as battery_use
        entries = [SimpleNamespace(app_id='io.piercingxx.XXContacts',
                                   name='Contacts')]
        win = self._win_with_entries(entries)
        section = battery_use.BatteryUseSection()
        assert section._display_name(
            win, 'io.piercingxx.XXContacts') == 'Contacts'

    def test_battery_use_falls_back_to_last_component(self):
        _ensure_settings_page()
        import settings_battery_use as battery_use
        win = self._win_with_entries([])
        section = battery_use.BatteryUseSection()
        assert section._display_name(
            win, 'io.piercingxx.XXContacts') == 'XXContacts'
        assert section._display_name(
            win, 'org.example.App.desktop') == 'App'

    def test_screen_time_matches_battery_use(self):
        _ensure_settings_page()
        import settings_battery_use as battery_use
        import settings_screentime as screen_time
        entries = [SimpleNamespace(app_id='org.example.App.desktop',
                                   name='Example')]
        win = self._win_with_entries(entries)
        win.config = SimpleNamespace(
            label_for=lambda _a, name: name,
            app_time_limits={}, app_limit_ignored={})
        assert battery_use.friendly_app_name(
            win, 'org.example.App.desktop') == 'Example'
        section = screen_time.ScreenTimeSection()
        assert section._display_name(
            win, 'org.example.App.desktop') == 'Example'
        assert section._display_name(win, 'io.piercingxx.XXEmail') == 'XXEmail'


class TestChargingProbe:
    def _sysfs(self, tmp_path, supplies):
        base = tmp_path / 'class' / 'power_supply'
        for name, supply_type, knobs in supplies:
            knob_dir = base / name
            knob_dir.mkdir(parents=True, exist_ok=True)
            (knob_dir / 'type').write_text(supply_type + '\n')
            for knob in knobs:
                (knob_dir / knob).write_text('100\n')
        return str(tmp_path)

    def test_battery_knob_counts(self, tmp_path):
        _ensure_settings_page()
        import settings_charging as charging
        root = self._sysfs(tmp_path, [
            ('BAT0', 'Battery', ['charge_control_end_threshold'])])
        assert charging.knob_supported(root) is True

    def test_non_battery_knob_ignored(self, tmp_path):
        _ensure_settings_page()
        import settings_charging as charging
        root = self._sysfs(tmp_path, [
            ('AC', 'Mains', ['charge_control_end_threshold']),
            ('USB', 'USB', ['charge_stop_threshold'])])
        assert charging.knob_supported(root) is False
        assert charging.find_charge_knobs(root) == []

    def test_battery_without_knob_is_unsupported(self, tmp_path):
        _ensure_settings_page()
        import settings_charging as charging
        root = self._sysfs(tmp_path, [('BAT0', 'Battery', [])])
        assert charging.knob_supported(root) is False

    def test_helper_unreachable_falls_back_to_sysfs(self, tmp_path):
        _ensure_settings_page()
        import settings_charging as charging
        section = charging.ChargingSection()
        state = {'local': (True, None), 'caps': (False, 'helper not installed')}
        seen = []
        section._on_state = lambda _w, res: seen.append(res)
        win = SimpleNamespace(config=SimpleNamespace(charge_limit=None))
        local_supported, battery = state['local']
        ok, caps = state['caps']
        supported = (bool(caps.get('charge_limit_supported'))
                     if ok else bool(local_supported))
        section._on_state(win, (supported, battery))
        assert seen == [(True, None)]

    def test_helper_reachable_wins_over_sysfs(self):
        ok, caps, local_supported = True, {'charge_limit_supported': False}, True
        if ok:
            supported = bool(isinstance(caps, dict)
                             and caps.get('charge_limit_supported'))
        else:
            supported = bool(local_supported)
        assert supported is False


class TestUsageLogCoalescing:
    def test_hour_call_writes_bounded(self, tmp_path):
        import privacy_monitor
        clock = SimpleNamespace(now=1000.0)
        log = privacy_monitor.UsageLog(
            path=tmp_path / 'usage.json', clock=lambda: clock.now)
        saves = []
        orig_save = log.save
        log.save = lambda: (saves.append(clock.now), orig_save())
        assert log.note_snapshot({'mic': {'Calls'}}, now=1000.0) is True
        first_saves = len(saves)
        assert first_saves == 1
        for tick in range(1, 1200):
            clock.now = 1000.0 + tick * 3.0
            log.note_snapshot({'mic': {'Calls'}})
        assert len(saves) <= 62
        assert log.recent(now=clock.now)[0]['at'] == clock.now
        assert (tmp_path / 'usage.json').exists()

    def test_new_key_saves_immediately(self, tmp_path):
        import privacy_monitor
        log = privacy_monitor.UsageLog(path=tmp_path / 'usage.json')
        assert log.note_snapshot({'mic': {'Calls'}}, now=1000.0) is True
        assert log.note_snapshot({'mic': {'Calls'}}, now=1003.0) is False
        assert log.note_snapshot({'camera': {'Camera'}}, now=1006.0) is True

    def test_shutdown_flushes_pending(self, tmp_path):
        import privacy_monitor
        log = privacy_monitor.UsageLog(path=tmp_path / 'usage.json')
        assert log.note_snapshot({'mic': {'Calls'}}, now=1000.0) is True
        assert log.note_snapshot({'mic': {'Calls'}}, now=1003.0) is False
        assert log._dirty is True
        log.shutdown()
        assert log._dirty is False


def _dialer_src():
    here = Path(__file__).parent.parent
    for candidate in (here / 'ref' / 'xx-dialer' / 'src',
                      here.parent / 'xx-dialer' / 'src'):
        if (candidate / 'xx_dialer' / 'service.py').exists():
            return str(candidate)
    return None


def _dialer_service_info(dialer_src):
    """(bus_name, object_path, interface) the dialer policy service serves.

    The bus name comes from the dialer's installed ``.service`` file
    (``data/*.service.in``), not a hard-coded string, so this follows the
    ``io.piercingxx.XXDialer`` -> ``io.piercingxx.XXDialer.Policy`` rename
    whichever side has landed; the object path and interface come from
    the dialer's ``service.py``. Falls back to parsing ``service.py`` for
    the bus name when no service file is present.
    """
    import re
    repo = Path(dialer_src).parent.parent
    names = []
    for pattern in ('*.service.in', '*.service'):
        for candidate in sorted((repo / 'data').glob(pattern)):
            try:
                text = candidate.read_text(encoding='utf-8')
            except OSError:
                continue
            for line in text.splitlines():
                if line.startswith('Name='):
                    names.append(line.split('=', 1)[1].strip())
    bus_name = next((n for n in names if 'Policy' in n), None)
    if bus_name is None:
        bus_name = names[0] if names else None
    object_path, interface = None, None
    try:
        service_py = (Path(dialer_src) / 'xx_dialer'
                      / 'service.py').read_text(encoding='utf-8')
    except OSError:
        service_py = ''
    for key in ('BUS_NAME', 'OBJECT_PATH', 'INTERFACE'):
        match = re.search(r'^%s\s*=\s*["\']([^"\']+)["\']' % key,
                          service_py, re.MULTILINE)
        if match:
            if key == 'BUS_NAME' and bus_name is not None:
                continue
            if key == 'BUS_NAME':
                bus_name = match.group(1)
            elif key == 'OBJECT_PATH':
                object_path = match.group(1)
            else:
                interface = match.group(1)
    return bus_name, object_path, interface


class TestPolicyBusNames:
    def test_policy_service_names(self):
        assert call_screen.BUS_NAME == 'io.piercingxx.XXDialer.Policy'
        assert call_screen.OBJECT_PATH == '/io/piercingxx/XXDialer/Policy'
        assert call_screen.INTERFACE == 'io.piercingxx.XXDialer.Policy1'


_ACTIVATION_DRIVER = r'''
import sys
sys.path.insert(0, {launcher_src!r})
sys.path.insert(0, {dialer_src!r})
from gi.repository import Gio, GLib
import call_screen
call_screen.BUS_NAME = {dialer_bus_name!r}
call_screen.OBJECT_PATH = {dialer_object_path!r}
loop = GLib.MainLoop()
result = {{}}
def _on_verdict(verdict, reason):
    result["verdict"] = (verdict, reason)
    loop.quit()
def _call():
    call_screen.consult_check_number("+14155550200", _on_verdict)
    return False
GLib.idle_add(_call)
GLib.timeout_add_seconds(30, loop.quit)
loop.run()
assert result.get("verdict") == ("block", "blocked"), result
print("SHELL-CONSULT-OK %s %s" % result["verdict"])
'''

_ACTIVATION_WRAPPER = """\
#!/bin/sh
export HOME={home!r}
export PYTHONPATH={src!r}:{common!r}
exec {exe!r} -m xx_dialer --service
"""


def test_shell_consult_activates_real_service(tmp_path):
    if shutil.which('dbus-run-session') is None:
        pytest.skip('dbus-run-session not installed')
    pytest.importorskip('gi.repository.Gio')
    dialer_src = _dialer_src()
    if dialer_src is None:
        pytest.skip('xx-dialer source not present')
    sys.path.insert(0, dialer_src)
    try:
        from xx_dialer import config as app_config
        from xx_dialer.store import Store
    finally:
        sys.path.pop(0)
    home = tmp_path / 'home'
    (home / '.local' / 'share' / 'xx-dialer').mkdir(parents=True)
    (home / '.config' / 'xx-dialer').mkdir(parents=True)
    settings = app_config.load(home)
    settings['enforcement_mode'] = 'enforcing'
    app_config.save(settings, home)
    store = Store(home / '.local' / 'share' / 'xx-dialer' / 'calls.db')
    store.add_blocked('+14155550200')
    store.close()
    dialer_bus_name, dialer_object_path, dialer_iface = _dialer_service_info(
        dialer_src)
    if not dialer_bus_name or not dialer_object_path:
        pytest.skip('dialer service bus name/object path not discoverable')
    assert dialer_iface == call_screen.INTERFACE
    data_dir = tmp_path / 'data'
    services_dir = data_dir / 'dbus-1' / 'services'
    services_dir.mkdir(parents=True)
    (services_dir / ('%s.service' % dialer_bus_name)).write_text(
        '[D-BUS Service]\nName=%s\n'
        'Exec=%s\n' % (dialer_bus_name, tmp_path / 'run-service.sh'))
    (tmp_path / 'run-service.sh').write_text(
        _ACTIVATION_WRAPPER.format(
            home=str(home), src=dialer_src, exe=sys.executable,
            common=str(Path(dialer_src).parent.parent / 'common')))
    (tmp_path / 'run-service.sh').chmod(0o755)
    launcher_src = str(Path(__file__).parent.parent / 'launcher' / 'src')
    driver = tmp_path / 'shell_consult_driver.py'
    # The driver consults the names the dialer actually serves (read from
    # its .service file above): identical to the shell constants once the
    # dialer's .Policy rename lands, and the pre-rename dialer still proves
    # the activation mechanism until then. The shell constants themselves
    # are pinned to the new names by TestPolicyBusNames.
    driver.write_text(_ACTIVATION_DRIVER.format(
        launcher_src=launcher_src, dialer_src=dialer_src,
        dialer_bus_name=dialer_bus_name,
        dialer_object_path=dialer_object_path))
    env = dict(os.environ)
    env['HOME'] = str(home)
    env['XDG_DATA_DIRS'] = str(data_dir) + os.pathsep + env.get(
        'XDG_DATA_DIRS', '/usr/local/share:/usr/share')
    proc = subprocess.run(
        ['dbus-run-session', '--', sys.executable, str(driver)],
        capture_output=True, text=True, timeout=90, env=env, check=False)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert 'SHELL-CONSULT-OK block blocked' in proc.stdout


def _verdict_consult(verdict, reason):
    """Fake consult answering with one verdict, like the real one."""

    def _fake(number, on_verdict, **kwargs):
        on_verdict(verdict, reason)

    return _fake


class TestMissedCardOwnership:
    """The shell never posts a missed-call card: the dialer policy
    service owns every one, and NoteCall wakes it over D-Bus activation
    when it is not running. Both terminal paths (the legacy end hook
    and the snapshot tracker) only report the call."""

    def _incoming(self, monkeypatch, events, verdict, reason, notifs):
        import modem_monitor
        win, _ui, _fake, _box = _wiring_win(events, notifs=notifs)
        monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                            lambda _n: False)
        monkeypatch.setattr(call_screen, 'consult_check_number',
                            _verdict_consult(verdict, reason))
        return win

    def test_ended_silence_posts_no_shell_card(self, monkeypatch):
        events, notifs, notes = [], [], []
        win = self._incoming(monkeypatch, events, 'silence',
                             'unknown caller', notifs)
        monkeypatch.setattr(call_screen, 'note_call',
                            lambda *a, **_k: notes.append(a))
        wiring = call_wiring.CallWiring(win)
        wiring._on_call_incoming('Mom', '+15550001111')
        wiring._on_call_ended()
        assert notes and notes[0][4] == 'missed'
        assert notifs == []

    def test_ended_ring_posts_no_shell_card(self, monkeypatch):
        events, notifs, notes = [], [], []
        win = self._incoming(monkeypatch, events, 'ring',
                             'saved contact', notifs)
        monkeypatch.setattr(call_screen, 'note_call',
                            lambda *a, **_k: notes.append(a))
        wiring = call_wiring.CallWiring(win)
        wiring._on_call_incoming('Mom', '+15550001111')
        wiring._on_call_ended()
        assert notes and notes[0][4] == 'missed'
        assert notifs == []

    def test_snapshot_path_posts_no_shell_card(self, monkeypatch):
        import modem_monitor
        events, notifs, notes = [], [], []
        win = self._incoming(monkeypatch, events, 'silence',
                             'unknown caller', notifs)
        monkeypatch.setattr(call_screen, 'note_call',
                            lambda *a, **_k: notes.append(a))
        wiring = call_wiring.CallWiring(win)
        wiring._on_call_incoming('Mom', '+15550001111')
        ringing = SimpleNamespace(
            path='/mm/Call/7', number='+15550001111',
            direction=modem_monitor._DIR_INCOMING,
            state=modem_monitor._STATE_RINGING_IN)
        wiring._on_calls_changed([ringing])
        wiring._on_calls_changed([])
        assert notes and notes[0][4] == 'missed'
        assert notifs == []

    def test_snapshot_path_posts_no_shell_card_for_ring(self, monkeypatch):
        import modem_monitor
        events, notifs, notes = [], [], []
        win = self._incoming(monkeypatch, events, 'ring',
                             'saved contact', notifs)
        monkeypatch.setattr(call_screen, 'note_call',
                            lambda *a, **_k: notes.append(a))
        wiring = call_wiring.CallWiring(win)
        wiring._on_call_incoming('Mom', '+15550001111')
        ringing = SimpleNamespace(
            path='/mm/Call/7', number='+15550001111',
            direction=modem_monitor._DIR_INCOMING,
            state=modem_monitor._STATE_RINGING_IN)
        wiring._on_calls_changed([ringing])
        wiring._on_calls_changed([])
        assert notes and notes[0][4] == 'missed'
        assert notifs == []
