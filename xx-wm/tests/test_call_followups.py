"""Call follow-ups: end-reason notice, call-waiting DnD logging, USSD dial.

- A call that ends abnormally ('Busy', 'Network error', 'Audio setup
  failed') shows its reason briefly: on the dialer status line when the
  dialer is open, else the shell toast. Normal hang-ups show nothing;
  'Busy' only for calls we placed.
- WAITING calls (screened in modem_monitor._screen_waiting) are noted
  into the DnD repeat-caller window once; a waiting call promoted to
  ringing is not noted twice.
- The DnD exception is decided before the ringing call is noted (it
  used to match itself, so every call rang through DnD).
- The shell dialer sends USSD/MMI codes to the USSD sheet and *#06# to
  the IMEI sheet instead of placing a voice call.
"""
from types import SimpleNamespace

import pytest

import call_wiring
import modem_monitor
from call_wiring import CallWiring, end_reason_notice


def _info(reason, direction=2, ended_at=100.0, text=None, path='/c/1'):
    return modem_monitor.CallInfo(
        path=path, direction=direction, state=7, state_reason=reason,
        end_reason=text if text is not None
        else modem_monitor.end_reason_text(reason),
        ended_at=ended_at)


@pytest.mark.parametrize('reason,direction,expected', [
    (0, 2, None), (1, 2, None), (3, 1, None), (4, 2, None), (4, 1, None),
    (5, 2, 'Busy'),
    (5, 1, None),                    # incoming: caller gave up / declined
    (6, 1, 'Network error'),
    (6, 2, 'Network error'),
    (7, 2, 'Audio setup failed'),
    (8, 2, 'Call transferred'),
    (9, 1, 'Call deflected'),
])
def test_end_reason_table(reason, direction, expected):
    assert end_reason_notice(_info(reason, direction), now=101.0) == expected


def test_end_reason_stale_or_missing():
    assert end_reason_notice(None) is None
    assert end_reason_notice(_info(6), now=200.0) is None
    assert end_reason_notice(_info(6, ended_at=None)) == 'Network error'
    assert end_reason_notice(_info(6, text=''), now=101.0) == 'Network error'


def _win(dialer_visible=None):
    shown = {'toast': [], 'dialer': [], 'noted': []}
    dialer = None
    if dialer_visible is not None:
        dialer = SimpleNamespace(
            get_visible=lambda: dialer_visible,
            _set_status=lambda t: shown['dialer'].append(t))
    win = SimpleNamespace(
        _dialer=dialer, _show_status=lambda t: shown['toast'].append(t),
        _calls_owner_warned=False, _call_ui=None, _call_bar=None,
        _screen_pending=None,
        dnd_state=SimpleNamespace(
            note_call=lambda n: shown['noted'].append(n),
            is_active=lambda: True, is_exception=lambda _n: False))
    return win, shown


def _last_ended(monkeypatch, info):
    monkeypatch.setattr(modem_monitor, 'last_ended_call', lambda: info)
    monkeypatch.setattr(call_wiring, 'end_reason_notice',
                        lambda i: end_reason_notice(i, now=101.0))


def test_on_call_ended_toasts_reason(monkeypatch):
    import sound
    monkeypatch.setattr(sound, 'stop', lambda: None)
    _last_ended(monkeypatch, _info(5))
    win, shown = _win()
    wiring = CallWiring(win)
    wiring._on_call_ended()
    assert shown['toast'] == ['Busy']
    wiring._on_call_ended()  # same call: once
    assert shown['toast'] == ['Busy']


def test_reason_goes_to_open_dialer(monkeypatch):
    _last_ended(monkeypatch, _info(7))
    win, shown = _win(dialer_visible=True)
    CallWiring(win)._show_end_reason()
    assert shown['dialer'] == ['Audio setup failed'] and shown['toast'] == []


def test_normal_hangup_and_calls_owner_show_nothing(monkeypatch):
    _last_ended(monkeypatch, _info(4))
    win, shown = _win(dialer_visible=False)
    CallWiring(win)._show_end_reason()
    _last_ended(monkeypatch, _info(6, path='/c/2'))
    win._calls_owner_warned = True
    CallWiring(win)._show_end_reason()
    assert shown['toast'] == [] and shown['dialer'] == []


# ------------------------------------------------------ waiting DnD log

def _call(path, number, state, direction=1):
    return modem_monitor.CallInfo(path=path, number=number, state=state,
                                  direction=direction)


ACTIVE = modem_monitor._STATE_ACTIVE
WAITING = modem_monitor._STATE_WAITING


def test_waiting_call_is_noted_once():
    win, shown = _win()
    wiring = CallWiring(win)
    first = _call('/c/1', '+15550001111', ACTIVE)
    wait = _call('/c/2', '+15550002222', WAITING)
    wiring._note_waiting_calls([first, wait])
    wiring._note_waiting_calls([first, wait])
    assert shown['noted'] == ['+15550002222']
    # Gone and back (a new call on the same path later) counts again.
    wiring._note_waiting_calls([first])
    wiring._note_waiting_calls([first, wait])
    assert shown['noted'] == ['+15550002222', '+15550002222']


def test_withheld_waiting_call_noted_again_when_number_arrives():
    win, shown = _win()
    wiring = CallWiring(win)
    wiring._note_waiting_calls([_call('/c/2', '', WAITING)])
    wiring._note_waiting_calls([_call('/c/2', '+15550002222', WAITING)])
    wiring._note_waiting_calls([_call('/c/2', '+15550002222', WAITING)])
    assert shown['noted'] == ['', '+15550002222']


def test_outgoing_or_active_calls_are_not_noted_as_waiting():
    win, shown = _win()
    CallWiring(win)._note_waiting_calls([
        _call('/c/1', '+1555', WAITING, direction=2),
        _call('/c/2', '+1666', ACTIVE)])
    assert shown['noted'] == []


def test_calls_changed_notes_waiting(monkeypatch):
    monkeypatch.setattr(modem_monitor, 'bus_name_has_owner', lambda _n: False)
    win, shown = _win()
    ui = SimpleNamespace(render_snapshot=lambda *a, **k: None, _minimized=False)
    win._sync_call_inhibit = lambda _c: None
    win._ensure_call_surfaces = lambda: (ui, None)
    win.is_locked = lambda: False
    win._screen_tracked = {}
    CallWiring(win)._on_calls_changed([_call('/c/2', '+15550002222', WAITING)])
    assert shown['noted'] == ['+15550002222']


def _incoming_win(monkeypatch, dnd):
    events = []
    monkeypatch.setattr(modem_monitor, 'bus_name_has_owner', lambda _n: False)
    import call_screen
    verdicts = []
    monkeypatch.setattr(call_screen, 'consult_check_number',
                        lambda n, cb: verdicts.append(cb))
    ui = SimpleNamespace(
        show_incoming=lambda c, n: None, present=lambda: None,
        start_ringing=lambda tone='ringtone.ogg': events.append('ring'),
        stop_ringing=lambda: None, end_call=lambda: None)
    win = SimpleNamespace(
        _calls_owner_warned=False, dnd_state=dnd,
        _ensure_call_surfaces=lambda: (ui, None), is_locked=lambda: False,
        config=SimpleNamespace(sound_ringtone=True), _screen_pending=None,
        _screen_tracked={})
    return win, events, verdicts


def test_dnd_exception_checked_before_noting_this_call(monkeypatch):
    import dnd as dnd_mod
    state = dnd_mod.DndState.__new__(dnd_mod.DndState)
    state._recent_calls = []
    state.is_starred = lambda _n: False
    state.is_active = lambda: True
    win, events, verdicts = _incoming_win(monkeypatch, state)
    wiring = CallWiring(win)
    monkeypatch.setattr(wiring, '_verdict_is_stale', lambda _n: False)
    wiring._on_call_incoming('Unknown', '+15550003333')
    verdicts[-1]('ring', 'unknown caller')
    assert events == []  # first call under DnD stays silent
    wiring._on_call_incoming('Unknown', '+15550003333')
    verdicts[-1]('ring', 'repeat caller')
    assert events == ['ring']  # second call within 15 min rings


def test_promoted_waiting_call_not_noted_twice(monkeypatch):
    noted = []
    dnd = SimpleNamespace(note_call=noted.append, is_active=lambda: False,
                          is_exception=lambda _n: False)
    win, _events, _verdicts = _incoming_win(monkeypatch, dnd)
    wiring = CallWiring(win)
    wiring._note_waiting_calls([_call('/c/2', '+15550002222', WAITING)])
    wiring._on_call_incoming('Bob', '+15550002222')
    assert noted == ['+15550002222']
    wiring._on_call_incoming('Ann', '+15550004444')
    assert noted == ['+15550002222', '+15550004444']


# ------------------------------------------------------ shell dialer USSD

def test_shell_dialer_routes_codes(monkeypatch):
    import dialer
    routed = []
    bare = dialer.Dialer.__new__(dialer.Dialer)
    bare._show_imei = lambda: routed.append(('imei',))
    bare._start_ussd = lambda code: routed.append(('ussd', code))
    bare._initiate_call = lambda number: routed.append(('call', number))
    for digits in ('*#06#', '*100#', '**21*5550100#', '+15550100', '#31#555'):
        bare._digits = digits
        bare._on_call(None)
    assert routed == [('imei',), ('ussd', '*100#'),
                      ('ussd', '**21*5550100#'), ('call', '+15550100'),
                      ('call', '#31#555')]


def test_classify_dial_without_xx_phone_stays_a_call(monkeypatch):
    import builtins
    import dialer
    real_import = builtins.__import__

    def _no_xx_phone(name, *args, **kwargs):
        if name.startswith('xx_phone'):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', _no_xx_phone)
    assert dialer.classify_dial('*100#') == 'call'


# ------------------------------------------- GNOME Calls leaving the bus

def test_end_notices_return_after_gnome_calls_leaves(monkeypatch):
    owner = {'present': True}
    monkeypatch.setattr(modem_monitor, 'bus_name_has_owner',
                        lambda _n: owner['present'])
    win, shown = _win()
    ui = SimpleNamespace(render_snapshot=lambda *a, **k: None,
                         _minimized=False)
    win._sync_call_inhibit = lambda _c: None
    win._ensure_call_surfaces = lambda: (ui, None)
    win.is_locked = lambda: False
    win._screen_tracked = {}
    wiring = CallWiring(win)
    wiring._on_calls_changed([_call('/c/1', '+15550001111', 4)])
    assert win._calls_owner_warned is True
    _last_ended(monkeypatch, _info(5, path='/c/1'))
    wiring._show_end_reason()
    assert shown['toast'] == []              # Calls shows its own reason
    owner['present'] = False                 # GNOME Calls quit
    wiring._on_calls_changed([_call('/c/2', '+15550002222', 4)])
    assert win._calls_owner_warned is False
    _last_ended(monkeypatch, _info(5, path='/c/2'))
    wiring._show_end_reason()
    assert shown['toast'] == ['Busy']


def test_incoming_probe_resets_owner_flag(monkeypatch):
    dnd = SimpleNamespace(note_call=lambda _n: None, is_active=lambda: False,
                          is_exception=lambda _n: False)
    win, _events, _verdicts = _incoming_win(monkeypatch, dnd)
    win._calls_owner_warned = True           # seen earlier this session
    wiring = CallWiring(win)
    monkeypatch.setattr(wiring, '_verdict_is_stale', lambda _n: False)
    wiring._on_call_incoming('Unknown', '+15550004444')
    assert win._calls_owner_warned is False
