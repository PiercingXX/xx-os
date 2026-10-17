"""Pure-logic ports of the Android JVM engine tests.

Covers CalculatorTest.kt, FormatTest.kt and CalculatorAngleModeTest.kt
from ref/xx-calculator: the evaluator, precision rules, display
thresholds, and trig modes behave exactly as the Play edition.
"""
from decimal import Decimal

from xx_calculator.engine import (
    AngleMode,
    Calculator,
    ErrorKind,
    EvalResult,
    format_grouped,
    format_plain,
)


def evaluate(expr, mode=AngleMode.DEG):
    return Calculator.evaluate(expr, mode)


def assert_text(expr, expected, mode=AngleMode.DEG):
    result = evaluate(expr, mode)
    assert isinstance(result, EvalResult.Success), f"expr={expr!r}: {result!r}"
    assert result.text == expected, f"expr={expr!r}"


def assert_error(expr, kind, message=None, mode=AngleMode.DEG):
    result = evaluate(expr, mode)
    assert isinstance(result, EvalResult.Failure), f"expr={expr!r}: {result!r}"
    assert result.kind is kind, f"expr={expr!r}"
    if message is not None:
        assert result.message == message, f"expr={expr!r}"


# ------------------------------------------------------------ arithmetic


def test_basic_addition():
    assert_text("2+3", "5")


def test_basic_subtraction():
    assert_text("7−10", "-3")


def test_basic_multiplication():
    assert_text("6×7", "42")


def test_basic_division():
    assert_text("9÷4", "2.25")


def test_decimal_exactness():
    assert_text("0.1+0.2", "0.3")


def test_repeating_division_rounds_to_15_sig_digits():
    assert_text("1÷3", "0.333333333333333")


# ------------------------------------------------------- trailing decimals


def test_lone_dot_evaluates_as_zero():
    assert_text(".", "0")


def test_trailing_dot_is_a_complete_number():
    assert_text("5.", "5")


def test_trailing_dot_number_in_arithmetic():
    assert_text("5.+3", "8")


def test_double_trailing_dot_rejected():
    assert_error("5..", ErrorKind.SYNTAX)


# ------------------------------------------------- precedence / grouping


def test_multiplication_binds_tighter_than_addition():
    assert_text("2+3×4", "14")


def test_left_associative_subtraction():
    assert_text("10−4−3", "3")


def test_right_associative_power():
    assert_text("2^3^2", "512")


def test_parentheses_override_precedence():
    assert_text("(2+3)×4", "20")


def test_unary_minus_leading():
    assert_text("−5+3", "-2")


def test_unary_minus_after_operator():
    assert_text("5×−3", "-15")


def test_negative_exponent():
    assert_text("2^−3", "0.125")


# ---------------------------------------------------------------- percent


def test_percent_addition_shopkeeper_semantics():
    assert_text("200+10%", "220")


def test_percent_subtraction_shopkeeper_semantics():
    assert_text("265−20%", "212")


def test_standalone_percent_is_divide_by_hundred():
    assert_text("50%", "0.5")


def test_percent_after_multiply_is_literal():
    assert_text("150×10%", "15")


def test_percent_inside_parens_rewrites_against_inner_total():
    assert_text("164+(265−20%)", "376")


# -------------------------------------------------------------- factorial


def test_factorial_five():
    assert_text("5!", "120")


def test_factorial_zero():
    assert_text("0!", "1")


def test_factorial_sixteen_displays_in_e_notation():
    assert_text("16!", "2.0922789888E13")


def test_large_factorial_stays_a_short_e_form_string():
    assert_text("50!", "3.04140932017134E64")


def test_factorial_overflow_at_170():
    assert_error("170!", ErrorKind.DOMAIN, "Overflow")


def test_factorial_of_non_integer_is_domain_error():
    assert_error("2.5!", ErrorKind.DOMAIN)


# ------------------------------------------------- D3 scientific notation


def test_just_under_one_e13_stays_plain_for_the_ui_to_group():
    assert_text("9999999999999", "9999999999999")


def test_tiny_division_result_uses_e_notation():
    assert_text("1÷10000000000", "1E-10")


def test_zero_never_gains_exponent_notation():
    assert_text("0", "0")


# ------------------------------------------------ E-form round trips


def test_post_equals_continuation_reparses_large_factorial_result():
    first = evaluate("16!")
    assert isinstance(first, EvalResult.Success)
    again = evaluate(first.text + "+1")
    assert isinstance(again, EvalResult.Success), f"cont={first.text}+1"


def test_post_equals_continuation_reparses_tiny_division_result():
    first = evaluate("1÷10000000000")
    assert isinstance(first, EvalResult.Success)
    assert first.text == "1E-10"
    again = evaluate(first.text + "×2")
    assert isinstance(again, EvalResult.Success)


def test_e_form_literal_evaluates_directly():
    assert_text("2.0922789888E13", "2.0922789888E13")


def test_negative_exponent_literal_evaluates_directly():
    assert_text("1E-10", "1E-10")


def test_uppercase_e_with_digits_is_scientific_notation():
    assert_text("2E3", "2000")


def test_lowercase_e_after_a_number_is_euler_not_an_exponent():
    assert_error("2e3", ErrorKind.SYNTAX)


def test_two_dot_number_rejected_as_syntax():
    assert_error("2.3.", ErrorKind.SYNTAX)


# -------------------------------------------------------------- functions


def test_square_root():
    assert_text("√(81)", "9")


def test_square_root_of_zero():
    assert_text("√(0)", "0")


def test_square_root_of_a_fraction():
    assert_text("√(0.25)", "0.5")


def test_irrational_square_root_rounds_to_display_precision():
    assert_text("√(2)", "1.4142135623731")


def test_square_roots_multiply_back_exactly():
    assert_text("√(2)×√(2)", "2")


def test_sin_thirty_degrees():
    assert_text("sin(30)", "0.5")


def test_cos_sixty_degrees():
    assert_text("cos(60)", "0.5")


def test_tan_forty_five_degrees():
    assert_text("tan(45)", "1")


def test_tan_ninety_degrees_is_domain_error():
    assert_error("tan(90)", ErrorKind.DOMAIN)


def test_ln_of_e():
    assert_text("ln(e)", "1")


def test_log_thousand():
    assert_text("log(1000)", "3")


# -------------------------------------------------------------- constants


def test_pi_to_fifteen_significant_digits():
    result = evaluate("π")
    assert isinstance(result, EvalResult.Success)
    assert result.text == "3.14159265358979"


def test_two_pi():
    result = evaluate("2×π")
    assert isinstance(result, EvalResult.Success)
    assert result.text == "6.28318530717959"


# ----------------------------------------------------------------- errors


def test_divide_by_zero():
    assert_error("5÷0", ErrorKind.DIVIDE_BY_ZERO, "Can't divide by 0")


def test_sqrt_of_negative():
    assert_error("√(−4)", ErrorKind.DOMAIN)


def test_ln_of_zero():
    assert_error("ln(0)", ErrorKind.DOMAIN)


def test_dangling_plus():
    assert_error("5+", ErrorKind.SYNTAX)


def test_unclosed_paren():
    assert_error("(2+3", ErrorKind.SYNTAX)


def test_empty_expression():
    assert_error("", ErrorKind.SYNTAX)


def test_stray_closing_paren():
    assert_error("5+6)", ErrorKind.SYNTAX)


def test_two_decimals_rejected():
    assert_error("2.3.4", ErrorKind.SYNTAX)


# --------------------------------------------------------------- formatting


def test_plain_strips_trailing_zeros():
    assert format_plain(Decimal("2.50")) == "2.5"


def test_plain_keeps_integers_without_exponent():
    assert format_plain(Decimal("100")) == "100"


def test_plain_normalizes_negative_zero():
    assert format_plain(Decimal("-0.000")) == "0"


def test_plain_rounds_to_fifteen_significant_digits():
    assert format_plain(Decimal("0.30000000000000001688")) == "0.3"


def test_plain_switches_to_e_form_at_exactly_one_e13():
    assert format_plain(Decimal("10000000000000")) == "1E13"


def test_plain_stays_plain_just_under_one_e13():
    assert format_plain(Decimal("9999999999999")) == "9999999999999"


def test_plain_switches_to_e_form_below_one_e_minus_nine():
    assert format_plain(Decimal("0.0000000001")) == "1E-10"


def test_plain_stays_plain_at_exactly_one_e_minus_nine():
    assert format_plain(Decimal("0.000000001")) == "0.000000001"


def test_plain_e_form_has_no_plus_sign_in_exponent():
    assert format_plain(Decimal("25000000000000")) == "2.5E13"


def test_plain_e_form_keeps_negative_exponent_sign_and_mantissa_sign():
    assert format_plain(Decimal("-20922789888000")) == "-2.0922789888E13"
    assert format_plain(Decimal("-0.0000000005")) == "-5E-10"


def test_plain_never_emits_zero_e_notation():
    assert format_plain(Decimal(0)) == "0"
    assert format_plain(Decimal("0E-10")) == "0"


def test_grouping_adds_thousands_separators():
    assert format_grouped("1234567") == "1,234,567"


def test_grouping_handles_negative_with_fraction():
    assert format_grouped("-9876543.21") == "-9,876,543.21"


def test_grouping_leaves_short_numbers_alone():
    assert format_grouped("12.5") == "12.5"


def test_grouping_passes_through_non_numeric_text():
    assert format_grouped("Can't divide by 0") == "Can't divide by 0"


def test_grouping_leaves_e_form_mantissa_ungrouped():
    assert format_grouped("2.0922789888E13") == "2.0922789888E13"
    assert format_grouped("-5E-10") == "-5E-10"
    assert format_grouped("1E13") == "1E13"


# ------------------------------------------------------ degrees (default)


def test_sin_thirty_in_degrees():
    assert_text("sin(30)", "0.5", AngleMode.DEG)


def test_cos_sixty_in_degrees():
    assert_text("cos(60)", "0.5", AngleMode.DEG)


def test_tan_ninety_in_degrees_is_domain_error():
    assert_error("tan(90)", ErrorKind.DOMAIN, mode=AngleMode.DEG)


def test_tan_two_seventy_in_degrees_is_domain_error():
    assert_error("tan(270)", ErrorKind.DOMAIN, mode=AngleMode.DEG)


def test_tan_four_fifty_in_degrees_is_domain_error():
    assert_error("tan(450)", ErrorKind.DOMAIN, mode=AngleMode.DEG)


def test_tan_negative_ninety_degrees_is_domain_error():
    assert_error("tan(−90)", ErrorKind.DOMAIN, mode=AngleMode.DEG)


def test_asin_one_in_degrees_is_ninety():
    assert_text("asin(1)", "90", AngleMode.DEG)


def test_cos_ninety_degrees_snaps_to_zero():
    assert_text("cos(90)", "0", AngleMode.DEG)


def test_sin_one_eighty_degrees_snaps_to_zero():
    assert_text("sin(180)", "0", AngleMode.DEG)


def test_sin_zero_snaps_to_zero():
    assert_text("sin(0)", "0", AngleMode.DEG)


def test_sin_ninety_degrees_snaps_to_one():
    assert_text("sin(90)", "1", AngleMode.DEG)


def test_cos_one_eighty_degrees_snaps_to_minus_one():
    assert_text("cos(180)", "-1", AngleMode.DEG)


# --------------------------------------------------------------- radians


def test_sin_of_pi_over_two_in_radians_is_one():
    assert_text("sin(π÷2)", "1", AngleMode.RAD)


def test_sin_pi_radians_snaps_to_zero():
    assert_text("sin(π)", "0", AngleMode.RAD)


def test_cos_pi_over_two_radians_snaps_to_zero():
    assert_text("cos(π÷2)", "0", AngleMode.RAD)


def test_tan_of_pi_over_two_in_radians_is_domain_error():
    assert_error("tan(π÷2)", ErrorKind.DOMAIN, mode=AngleMode.RAD)


def test_asin_one_in_radians_is_pi_over_two_within_display_rounding():
    result = evaluate("asin(1)", AngleMode.RAD)
    assert isinstance(result, EvalResult.Success)
    assert abs(float(result.value) - 1.5707963267949) < 1e-11


def test_ninety_radians_is_not_a_tan_pole():
    import math

    result = evaluate("tan(90)", AngleMode.RAD)
    assert isinstance(result, EvalResult.Success)
    assert abs(float(result.value) - math.tan(90.0)) < 1e-9


# ------------------------------------------------- default overload pins


def test_single_argument_evaluate_defaults_to_degrees():
    assert Calculator.evaluate("sin(30)") == Calculator.evaluate(
        "sin(30)", AngleMode.DEG
    )


def test_angle_mode_changes_the_result_of_the_same_expression():
    deg = evaluate("sin(30)", AngleMode.DEG)
    rad = evaluate("sin(30)", AngleMode.RAD)
    assert isinstance(deg, EvalResult.Success)
    assert isinstance(rad, EvalResult.Success)
    assert abs(float(rad.value) - (-0.9880316240928618)) < 1e-11
    assert abs(float(deg.value) - 0.5) < 1e-12


def test_evaluating_huge_powers_stays_a_domain_error_not_a_hang():
    assert_error("2^99999999999999999999999", ErrorKind.DOMAIN)
    assert_error("9^9999999999", ErrorKind.DOMAIN)


def test_no_eval_of_user_input():
    import pathlib

    pkg = pathlib.Path(__file__).parent.parent / "src" / "xx_calculator"
    for path in sorted(pkg.glob("*.py")):
        text = path.read_text()
        assert "\neval(" not in text and " eval(" not in text, path.name
        assert "exec(" not in text, path.name
