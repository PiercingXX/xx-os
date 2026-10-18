"""Headless UI-logic tests: paste sanitizer, error strings, grouping hook."""
from xx_calculator.engine import DIVIDE, MINUS, TIMES, ErrorKind
from xx_calculator.main import (
    BACK_TARGETS,
    _mix,
    error_text,
    format_number,
    pick_fit,
    sanitize_paste,
    shrink_to_fit,
)


def test_pick_fit_takes_the_largest_step_that_fits():
    assert pick_fit([400, 320, 280, 240], 300) == 2
    assert pick_fit([200, 180], 300) == 0


def test_pick_fit_falls_back_to_the_smallest_step():
    assert pick_fit([900, 800, 700], 300) == 2
    assert pick_fit([], 300) == 0


def test_shrink_to_fit_leaves_a_fitting_line_alone():
    assert shrink_to_fit(24, 200, 300, 12) is None
    assert shrink_to_fit(24, 200, 0, 12) is None


def test_shrink_to_fit_scales_a_long_number_down_whole():
    assert shrink_to_fit(36, 410, 272, 12) == 23
    assert shrink_to_fit(36, 4000, 272, 12) == 12


def test_back_closes_each_sub_page_to_its_parent():
    assert BACK_TARGETS == {"history": "calc", "licenses": "calc",
                            "license": "licenses"}
    assert "calc" not in BACK_TARGETS


def test_license_entries_list_what_the_calculator_ships():
    import pytest

    try:
        from xx_calculator.main import license_entries

        entries = license_entries()
    except Exception:
        pytest.skip("GTK is missing")
    names = [entry.name for entry in entries]
    assert names[0] == "xx-calculator"
    assert entries[0].spdx == "GPL-3.0-only"
    assert "phonenumbers" not in names
    assert "GTK 4" in names


def test_error_strings_match_the_play_edition():
    assert error_text(ErrorKind.DIVIDE_BY_ZERO) == "Can't divide by 0"
    assert error_text(ErrorKind.SYNTAX) == "Invalid expression"
    assert error_text(ErrorKind.DOMAIN) == "Invalid input"


def test_format_number_groups_plain_results():
    assert format_number("1234567") == "1,234,567"
    assert format_number("2.5") == "2.5"


def test_format_number_leaves_e_form_untouched():
    assert format_number("2.0922789888E13") == "2.0922789888E13"


def test_paste_maps_ascii_operators_to_display_glyphs():
    assert sanitize_paste("2+3*4") == f"2+3{TIMES}4"
    assert sanitize_paste("9/4") == f"9{DIVIDE}4"
    assert sanitize_paste("7-10") == f"7{MINUS}10"


def test_paste_keeps_functions_constants_and_e_form():
    assert sanitize_paste("sin(30)") == "sin(30)"
    assert sanitize_paste("2.0922789888E13") == "2.0922789888E13"
    assert sanitize_paste("√(2)") == "√(2)"


def test_paste_drops_garbage_and_whitespace():
    assert sanitize_paste(" 12 + 34 ") == "12+34"
    assert sanitize_paste("?@#") == ""
    assert sanitize_paste("!") == "!"
    assert "?" not in sanitize_paste("2?+2")
    assert sanitize_paste("2?+2") == "2+2"


def test_paste_empty_stays_empty():
    assert sanitize_paste("") == ""


def test_key_mix_raises_digits_off_an_ink_ground():
    assert _mix("#f4f4f4", "#000000", 0.16) == "#272727"
    assert _mix("#f4f4f4", "#000000", 0.12) == "#1d1d1d"


def test_key_mix_darkens_tiles_on_paper():
    assert _mix("#151515", "#F3EEE2", 0.16) == "#cfcbc1"


def test_keys_are_round_and_fill_the_width():
    from xx_calculator.main import KEY_RATIO, key_geometry

    # FLX1 portrait (450 px minus the 8 px pad margins): round keys at
    # 80% of a column, as on Android.
    w, h = key_geometry(434, None)
    assert w == h == int(434 / 4 * KEY_RATIO)
    # Enough height keeps them round.
    assert key_geometry(434, 5 * (w + 10)) == (w, h)


def test_short_windows_turn_keys_into_pills():
    from xx_calculator.main import key_geometry

    w, h = key_geometry(344, 260)
    assert h == 260 // 5 - 10
    assert w > h
    assert w <= 344 / 4 - 8


def test_app_css_follows_text_size():
    from xx_calculator.main import app_css

    assert "font-size: 52px" in app_css(1.0)
    assert "font-size: 68px" in app_css(1.3)
    assert "border: 1px solid @borders" not in app_css(1.0)


def test_key_mix_falls_back_on_garbage():
    assert _mix("nope", "#000000", 0.16) == "#000000"
    assert _mix("#f4f4f4", "", 0.16) == ""
