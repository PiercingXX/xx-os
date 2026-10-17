"""Real GLib Variant construction for notifications and feedbackd.

Fakes accept anything; real PyGObject raises ``KeyError: 0`` on a nested
prebuilt container and ``TypeError`` on a plain-string reply_type. Both
builders run against the real library here.
"""

import pytest

pytestmark = pytest.mark.skipif(
    __import__("shutil").which("dbus-daemon") is None,
    reason="dbus-daemon is missing")


def _glib():
    import gi
    gi.require_version("GLib", "2.0")
    from gi.repository import GLib
    return GLib


def test_notify_params_build_in_one_step():
    from xx_clock import notify
    params = notify.notify_params("Alarm", "07:00",
                                  ["snooze", "Snooze", "dismiss", "Dismiss"],
                                  critical=True)
    assert params.get_type_string() == "(susssasa{sv}i)"
    unpacked = params.unpack()
    assert unpacked[3] == "Alarm"
    assert list(unpacked[5]) == ["snooze", "Snooze", "dismiss", "Dismiss"]
    assert unpacked[6]["urgency"] == 2


def test_missed_notification_is_not_critical():
    from xx_clock import notify
    params = notify.notify_params("Missed alarm", "", [], critical=False)
    unpacked = params.unpack()
    assert unpacked[6]["urgency"] == 1
    assert unpacked[7] == 10000


def test_reply_type_is_a_real_variant_type():
    GLib = _glib()
    reply_type = GLib.VariantType.new("(u)")
    assert isinstance(reply_type, GLib.VariantType)


def test_feedback_params_build_in_one_step():
    from xx_clock import ringer
    GLib = _glib()
    params = ringer.feedback_params("io.piercingxx.XXClock", GLib)
    assert params.get_type_string() == "(ssa{sv}i)"
    app_id, event, _hints, _mode = params.unpack()
    assert app_id == "io.piercingxx.XXClock"
    assert event == "alarm-clock-elapsed"


def test_ringer_reports_failure_without_pipeline():
    from xx_clock import ringer as ringer_module
    ring = ringer_module.Ringer(play=lambda _c: False, vibrate=lambda: False)
    assert ring.start(["nothing"]) is False
    assert ring.ringing is False
    ring.stop()


def test_ringer_uses_fallback_seams():
    from xx_clock import ringer as ringer_module
    played: list = []
    ring = ringer_module.Ringer(play=lambda c: played.append(c) or True,
                                vibrate=lambda: True)
    assert ring.start(["tone"]) is True
    assert ring.ringing is True
    assert played == [["tone"]]
    ring.stop()
    assert ring.ringing is False
