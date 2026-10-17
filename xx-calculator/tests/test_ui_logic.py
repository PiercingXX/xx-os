"""Headless UI-logic tests: paste sanitizer, error strings, grouping hook."""
from xx_calculator.engine import DIVIDE, MINUS, TIMES, ErrorKind
from xx_calculator.main import (
    _mix,
    error_text,
    format_number,
    sanitize_paste,
)


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


def test_key_mix_falls_back_on_garbage():
    assert _mix("nope", "#000000", 0.16) == "#000000"
    assert _mix("#f4f4f4", "", 0.16) == ""
