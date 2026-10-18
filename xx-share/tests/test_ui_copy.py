"""Share-window copy and states, as pure functions (no display needed).

Pins the minimal rules: empty lists show no caption, Send needs a device
and files, send failures read as one short line without HTTP codes.
"""
import time

import pytest

pytest.importorskip("gi")

import ui  # noqa: E402
from client import FingerprintMismatch, PinRequired, TransferError  # noqa: E402
from service import PIN_NEEDED, send_error_text  # noqa: E402


def test_count_caption_hides_when_empty():
    assert ui.count_caption(0, "file") == ""
    assert ui.count_caption(1, "file") == "1 FILE"
    assert ui.count_caption(6, "file") == "6 FILES"


def test_visibility_caption():
    assert ui.visibility_caption(False, True) == "Hidden"
    assert ui.visibility_caption(True, True) == "Visible"
    assert ui.visibility_caption(True, False) == "Visible · 10 min"


def test_keep_visible_subtitle_follows_switch():
    assert ui.keep_visible_sub(False) == "Hides after 10 min"
    assert ui.keep_visible_sub(True) == "Until turned off"


def test_send_needs_device_and_files():
    assert ui.send_ready(False, 2) is False
    assert ui.send_ready(True, 0) is False
    assert ui.send_ready(True, 1) is True


def test_incoming_summary():
    assert ui.incoming_summary(1, 1024) == "Wants to send 1 file · 1.0 KB"
    assert ui.incoming_summary(4, 50725158).startswith("Wants to send 4 files")


def test_history_date_today_is_time_older_is_day():
    now = time.mktime((2026, 10, 17, 15, 0, 0, 0, 0, -1))
    today = time.mktime((2026, 10, 17, 9, 5, 0, 0, 0, -1))
    older = time.mktime((2026, 10, 3, 9, 5, 0, 0, 0, -1))
    assert ui._history_date({"received_at": today}, now) == "09:05"
    assert ui._history_date({"received_at": older}, now) == "Oct 3"
    assert ui._history_date({"received_at": "junk"}, now) == ""


def test_tabs_are_share_history_settings():
    assert [name for name, _title in ui.TABS] == ["share", "history",
                                                  "settings"]


def test_send_errors_are_short_and_plain():
    assert send_error_text("Mara", PinRequired(401, "PIN required")) \
        == PIN_NEEDED
    assert send_error_text(
        "Mara", TransferError(403, "receiver declined the transfer")) \
        == "Mara declined"
    assert send_error_text("Mara", TransferError(0, "cancelled")) \
        == "Cancelled"
    assert send_error_text(
        "Mara", TransferError(409, "receiver is busy with another session")) \
        == "Receiver is busy with another session"
    assert send_error_text("Mara", OSError("refused")) == "Couldn't reach Mara"
    assert "changed identity" in send_error_text(
        "Mara", FingerprintMismatch("x"))
    for text in (send_error_text("M", TransferError(413, "too big")),
                 send_error_text("M", TransferError(500, "request failed"))):
        assert "HTTP" not in text
