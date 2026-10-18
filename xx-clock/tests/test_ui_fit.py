"""Phone-fit rules the UI pass fixed: thumb-sized targets, taps on the
alarm switch, and the copy that must stay whole."""
import re
from types import SimpleNamespace

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
    # The shared switch is a 48 px target (xx_ui style.css): a tap inside
    # it is the switch's, anywhere else on the row opens the editor.
    hit = SimpleNamespace(x=272, y=8, width=48, height=48)
    assert alarms_page.tap_zone(300, 30, hit) == "switch"
    assert alarms_page.tap_zone(274, 10, hit) == "switch"
    assert alarms_page.tap_zone(260, 30, hit) == "row"
    assert alarms_page.tap_zone(20, 30, hit) == "row"


def test_shared_switch_is_a_48_px_target():
    from pathlib import Path
    css = (Path(__file__).resolve().parents[2] / "common" / "xx_ui"
           / "style.css").read_text()
    rule = re.search(r"\nswitch \{(.*?)\n\}", css, re.S).group(1)
    border = re.search(r"border: (\d+)px solid transparent", rule)
    side = re.search(r"border-left-width: (\d+)px", rule)
    pad = re.search(r"padding: (\d+)px", rule)
    # 20 px pill + 2 x (3 px padding + 11 px border) = 48 tall.
    assert 20 + 2 * (int(pad.group(1)) + int(border.group(1))) >= 48
    assert 40 + 2 * (int(pad.group(1)) + int(side.group(1))) >= 48


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
    from pathlib import Path
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
