"""Phone-fit rules the UI pass fixed: thumb-sized targets, taps on the
alarm switch, and the copy that must stay whole."""
import re
from pathlib import Path

from xx_clock.ui import alarm_dialog, alarms_page, setup_page, shell, timers_page


def _min_heights(css: str, selector: str) -> list[int]:
    rules = re.findall(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return [int(v) for rule in rules for v in re.findall(r"min-height:\s*(\d+)px", rule)]


def test_tap_targets_are_48_px():
    css = shell.APP_CSS
    for selector in (".xx-clock-act", ".xx-clock-presets .xx-chip",
                     ".xx-clock-days .xx-chip", ".xx-clock-spin button",
                     ".xx-clock-snooze button"):
        heights = _min_heights(css, selector)
        assert heights and min(heights) >= 48, selector


def test_switch_taps_stay_on_the_switch():
    # The shared switch answers to a 48 px target (xx_ui make_switch): a
    # tap inside it is the switch's, anywhere else on the row edits.
    assert alarms_page.tap_zone(True) == "switch"
    assert alarms_page.tap_zone(False) == "row"
    src = (Path(__file__).resolve().parents[1] / "src" / "xx_clock" / "ui"
           / "alarms_page.py").read_text()
    assert "switch.contains(" in src
    assert "SWITCH_SLOP" not in src


def test_limits_line_is_unchanged():
    assert setup_page.LIMIT_TEXT == "Silent when suspended or off"


def test_custom_minutes_range():
    assert timers_page.parse_minutes("5") == 5
    assert timers_page.parse_minutes(" 999 ") == 999
    for bad in ("0", "1000", "", "x", "-3"):
        assert timers_page.parse_minutes(bad) is None


def test_rejected_minutes_are_marked():
    # The error class must be visible, not a bare Adwaita class no theme
    # draws: the shared stylesheet outlines entry.xx-entry.error.
    css = (Path(__file__).resolve().parents[2] / "common" / "xx_ui"
           / "style.css").read_text()
    assert "entry.xx-entry.error" in css
    assert "xx-clock-minutes" not in timers_page.APP_CSS_EXTRA


def test_next_alarm_wraps_between_parts_only():
    from xx_clock.ui import clock_page
    text = clock_page.next_alarm_text(
        {"fire_text": "07:00", "label": "Work", "until_text": "8 h 0 m"})
    assert text.replace(clock_page.NBSP, " ") == "07:00 · Work · in 8 h 0 m"
    # Breakable spaces sit only after a separator dot.
    assert [text[i - 1] for i, ch in enumerate(text) if ch == " "] == ["·", "·"]
    assert clock_page.next_alarm_text({"fire_text": "07:00"}) == "07:00"


def test_sound_row_shows_the_file_name():
    assert alarm_dialog.sound_name(None) == "Default"
    assert alarm_dialog.sound_name("file:///home/me/Morning%20Bell.ogg") == "Morning Bell.ogg"
