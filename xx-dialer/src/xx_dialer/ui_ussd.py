"""USSD/MMI routing for the app's dial paths (keypad, people, recents).

``*100#`` must never become a voice call (``policy.normalize_number``
would even strip it to ``100``). Every place that dials asks
:func:`route_special` first: the shared :mod:`xx_phone.mmi` detector (the
one the XX-WM shell dialer uses) decides, and USSD codes and ``*#06#``
open the shared :mod:`xx_phone.ussd_dialog` sheet instead of a call.
"""
from __future__ import annotations


def special_kind(raw: str | None) -> str:
    """``'imei'``, ``'ussd'`` or ``'call'``; ``'call'`` when the shared
    detector is not importable (the old behaviour)."""
    try:
        from xx_phone import mmi

        return mmi.classify(raw)
    except Exception:
        return "call"


def _parent_of(widget):
    try:
        return widget.get_root() if widget is not None else None
    except Exception:
        return None


def route_special(raw: str | None, voice=None, widget=None,
                  notice=None, opener=None) -> bool:
    """Open the USSD/IMEI sheet for a special code and return True;
    False means "dial it as a call". ``voice`` lends its system-bus
    connection when it has one; ``opener(kind, code, parent, bus)``
    replaces the GTK sheet in tests."""
    kind = special_kind(raw)
    if kind == "call":
        return False
    bus = getattr(voice, "_bus", None)
    parent = _parent_of(widget)
    try:
        if opener is not None:
            opener(kind, raw, parent, bus)
        else:
            from xx_phone import ussd_dialog

            if kind == "imei":
                ussd_dialog.open_imei_dialog(parent=parent, bus=bus)
            else:
                ussd_dialog.open_ussd_dialog(raw, parent=parent, bus=bus)
    except Exception as err:
        if notice is not None:
            try:
                notice("Could not send the code: %s" % (err,))
            except Exception:
                pass
    return True
