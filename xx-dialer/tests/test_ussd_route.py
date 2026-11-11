"""USSD/MMI codes never become voice calls in the app.

The keypad (and people/recents dial paths) ask ui_ussd.route_special
first, which uses the shared xx_phone.mmi detector — the same one the
XX-WM shell dialer uses. ``*100#`` used to be normalized to ``100`` and
dialed as a call.
"""
from types import SimpleNamespace

from xx_dialer import ui_ussd


def _route(raw, voice=None):
    opened = []
    handled = ui_ussd.route_special(
        raw, voice, None, None,
        opener=lambda kind, code, parent, bus: opened.append((kind, code, bus)))
    return handled, opened


def test_ussd_and_imei_open_the_sheet():
    voice = SimpleNamespace(_bus="BUS")
    assert _route("*100#", voice) == (True, [("ussd", "*100#", "BUS")])
    assert _route("*#06#") == (True, [("imei", "*#06#", None)])
    assert _route("**21*5550100#")[0] is True


def test_numbers_still_dial():
    for raw in ("+15550100", "555-0100", "#31#5550100", ""):
        assert _route(raw) == (False, [])


def test_opener_failure_is_a_notice_not_a_call():
    notes = []

    def _boom(*_a):
        raise RuntimeError("no display")

    assert ui_ussd.route_special("*100#", None, None, notes.append,
                                 opener=_boom) is True
    assert notes and "no display" in notes[0]


def test_keypad_call_button_routes_codes(monkeypatch):
    from xx_dialer import ui_keypad

    routed, placed = [], []
    monkeypatch.setattr(
        ui_ussd, "route_special",
        lambda raw, voice, widget, notice: routed.append(raw)
        or ui_ussd.special_kind(raw) != "call")
    view = ui_keypad.KeypadView.__new__(ui_keypad.KeypadView)
    view.deps = SimpleNamespace(voice=None)
    view._root = None
    view._notice = lambda _t: None
    view.place = placed.append
    for digits in ("*100#", "+15550100"):
        view.buffer = ui_keypad.KeypadBuffer(digits)
        view._on_call_click()
    assert routed == ["*100#", "+15550100"]
    assert placed == [ui_keypad.dial_target("+15550100")]


def test_dial_target_keeps_service_codes():
    from xx_dialer.ui_keypad import dial_target

    assert dial_target("#31# 555 0100") == "#31#5550100"
    assert dial_target("*100#") == "*100#"
