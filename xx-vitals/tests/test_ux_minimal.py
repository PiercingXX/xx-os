"""UX pass contract: minimal copy, one way to each thing, honest units.

Pure helpers behind the pages (no display needed) plus source guards
for clutter the owner removed: empty-state lines, decorative arrows,
explanatory paragraphs and duplicate menu entries.
"""

from pathlib import Path
from types import SimpleNamespace

from xx_vitals.core import profile_edit
from xx_vitals.ui import browse, home, journal, profile, shell

UI = Path(__file__).parent.parent / "src" / "xx_vitals" / "ui"


def _nav():
    return SimpleNamespace(add_weight=lambda: None, add_activity=lambda: None,
                           show=lambda _name: None)


def test_settings_entry_only_while_profile_tab_hidden():
    shown = SimpleNamespace(show_profile_tab=True)
    hidden = SimpleNamespace(show_profile_tab=False)
    assert shell.settings_items(shown, _nav()) == []
    assert [t for t, _ in shell.settings_items(hidden, _nav())] == ["Settings"]


def test_home_menu_has_no_duplicate_entries():
    shown = SimpleNamespace(show_profile_tab=True)
    hidden = SimpleNamespace(show_profile_tab=False)
    # The FAB logs an activity and Import lives on Profile: only weight.
    assert [t for t, _ in home.home_menu_items(shown, _nav())] == ["Log weight"]
    assert [t for t, _ in home.home_menu_items(hidden, _nav())] == [
        "Log weight", "Settings"]


def test_profile_stays_reachable_with_its_tab_hidden():
    src = (UI / "shell.py").read_text(encoding="utf-8")
    assert 'name != "profile"' in src


def test_step_subtitle_is_short_and_honest():
    off = {"enabled": False, "has_source": False, "source": ""}
    hw = {"enabled": True, "has_source": True, "source": "hardware"}
    awake = {"enabled": True, "has_source": True, "source": "resident"}
    none = {"enabled": True, "has_source": False, "source": ""}
    assert profile.step_subtitle(off) == ""  # the switch says off
    assert profile.step_subtitle(hw) == "Hardware step counter"
    assert profile.step_subtitle(awake) == "While the phone is awake"
    assert profile.step_subtitle(none) == "No step sensor found"
    assert profile.step_subtitle(awake, systemd=False) == "While the app is open"


def test_goal_errors_name_the_field():
    text = profile.goal_error_text({"steps": "Enter 1–100000",
                                    "weekly": "Enter 1–100000"})
    assert text.splitlines() == ["Daily steps goal: enter 1–100000",
                                 "Weekly effort goal: enter 1–100000"]


def test_imperial_height_entry_round_trips():
    # The entry shows the height in the current units, so Save (which
    # parses in those units) keeps it instead of rejecting "1.78".
    shown = profile_edit.height_input_text(1.78, "imperial")
    assert shown == "5'10"
    assert abs(profile_edit.parse_height_metres(shown, "imperial") - 1.78) < 0.02
    assert profile.height_placeholder("imperial") == "e.g. 5'10"
    assert profile.height_placeholder("metric") == "e.g. 1.78"


def test_journal_titles_and_empty_caption():
    def activity(kind):
        return SimpleNamespace(type=kind, started_at=1785574800000)

    assert journal.activity_title(activity("run")).endswith(" run")
    assert journal.activity_title(activity("cycle")).endswith(" ride")
    assert journal.journal_caption(0, 0) is None
    assert journal.journal_caption(1, 1) == "1 day · 1 step"
    assert journal.journal_caption(3, 1200) == "3 days · 1,200 steps"


def test_browse_range_caption_is_short():
    assert browse.range_caption("2026-09-18", "2026-10-17") == "Sep 18 – Oct 17"
    assert browse.range_caption("2025-12-30", "2026-01-02") == (
        "Dec 30 2025 – Jan 2 2026")


def test_no_clutter_in_page_sources():
    home_src = (UI / "home.py").read_text(encoding="utf-8")
    journal_src = (UI / "journal.py").read_text(encoding="utf-8")
    browse_src = (UI / "browse.py").read_text(encoding="utf-8")
    profile_src = (UI / "profile.py").read_text(encoding="utf-8")
    assert "go-next-symbolic" not in home_src  # no decorative arrows
    assert "Heart Points a week" not in home_src  # no explainer paragraph
    for src in (journal_src, browse_src):
        assert "empty_state(" not in src  # an empty list shows nothing
    assert "log lines copied" not in profile_src  # no success notice
    assert "Import Takeout\"" not in home_src + journal_src + browse_src
