"""Pure-logic port of ExpressionBuilderTest.kt from ref/xx-calculator.

The input-grammar state machine: operator replacement, decimal rules,
leading-zero collapse, percent gating, labeled/combined paren rules,
constant/function gating, backspace/clear/seed, can_evaluate.
"""
from xx_calculator.builder import ExpressionBuilder


def build(*ops):
    builder = ExpressionBuilder()
    for op in ops:
        op(builder)
    return builder


def digit(char):
    return lambda b: b.digit(char)


def oper(char):
    return lambda b: b.operator(char)


# --------------------------------------------------- operator replacement


def test_operator_replaces_trailing_operator():
    assert build(digit("5"), oper("+"), oper("*")).expression == "5×"


def test_ascii_operators_map_to_display_glyphs():
    assert build(digit("8"), oper("-"), oper("/")).expression == "8÷"


def test_minus_allowed_as_leading_unary():
    assert build(oper("-"), digit("5")).expression == "−5"


def test_plus_ignored_on_empty():
    assert build(oper("+")).expression == ""


# ---------------------------------------------------------------- decimal


def test_second_decimal_ignored():
    b = build(digit("5"), lambda b: b.decimal(), lambda b: b.decimal())
    assert b.expression == "5."


def test_decimal_seeds_zero_point_on_empty():
    assert build(lambda b: b.decimal()).expression == "0."


def test_decimal_after_digit_extends_number():
    assert build(digit("0"), lambda b: b.decimal(), digit("5")).expression == "0.5"


def test_operator_attaches_after_trailing_dot_number():
    assert build(digit("5"), lambda b: b.decimal(), oper("+")).expression == "5.+"


def test_percent_after_trailing_dot_number():
    b = build(digit("5"), lambda b: b.decimal(), lambda b: b.percent())
    assert b.expression == "5.%"


# ----------------------------------------------------------- leading zeros


def test_leading_zeros_collapse():
    assert build(digit("0"), digit("7")).expression == "7"


def test_multiple_leading_zeros_collapse():
    assert build(digit("0"), digit("0"), digit("7")).expression == "7"


def test_zero_stays_when_followed_by_decimal():
    assert build(digit("0"), lambda b: b.decimal()).expression == "0."


# ---------------------------------------------------------------- percent


def test_percent_ignored_on_empty():
    assert build(lambda b: b.percent()).expression == ""


def test_percent_after_value():
    assert build(digit("5"), lambda b: b.percent()).expression == "5%"


def test_percent_ignored_after_operator():
    assert build(digit("5"), oper("+"), lambda b: b.percent()).expression == "5+"


# ---------------------------------------------------------- labeled parens


def test_open_paren_on_empty():
    assert build(lambda b: b.open_paren()).expression == "("


def test_open_paren_after_operator():
    assert build(digit("5"), oper("+"), lambda b: b.open_paren()).expression == "5+("


def test_open_paren_inserts_even_after_value():
    assert build(digit("5"), lambda b: b.open_paren()).expression == "5("


def test_open_paren_stacks_after_unclosed_group():
    b = build(digit("2"), oper("+"), digit("3"), lambda b: b.open_paren())
    assert b.expression == "2+3("


def test_close_paren_closes_an_open_group_at_value_position():
    b = build(lambda b: b.open_paren(), digit("1"), oper("+"), digit("2"),
              lambda b: b.close_paren())
    assert b.expression == "(1+2)"


def test_close_paren_on_balanced_expression_is_noop():
    b = build(lambda b: b.open_paren(), digit("1"), oper("+"), digit("2"),
              lambda b: b.close_paren(), lambda b: b.close_paren())
    assert b.expression == "(1+2)"


def test_close_paren_after_bare_value_is_noop():
    assert build(digit("5"), lambda b: b.close_paren()).expression == "5"


def test_close_paren_on_empty_is_noop():
    assert build(lambda b: b.close_paren()).expression == ""


# -------------------------------------------------------- combined parens


def test_paren_opens_on_empty():
    assert build(lambda b: b.paren()).expression == "("


def test_paren_opens_after_operator():
    assert build(digit("5"), oper("+"), lambda b: b.paren()).expression == "5+("


def test_paren_closes_when_unclosed_group_ends_in_value():
    b = build(lambda b: b.paren(), digit("1"), oper("+"), digit("2"),
              lambda b: b.paren())
    assert b.expression == "(1+2)"


def test_paren_closes_nested_group_first():
    b = build(lambda b: b.paren(), digit("1"), oper("+"), lambda b: b.paren(),
              digit("2"), lambda b: b.paren())
    assert b.expression == "(1+(2)"


def test_paren_opens_after_balanced_expression():
    b = build(lambda b: b.paren(), digit("1"), lambda b: b.paren(),
              lambda b: b.paren())
    assert b.expression == "(1)("


# --------------------------------------------------------------- constants


def test_constant_after_operator_allowed():
    assert build(digit("2"), oper("*"), lambda b: b.constant("π")).expression == "2×π"


def test_constant_ignored_right_after_value():
    assert build(digit("2"), lambda b: b.constant("π")).expression == "2"


# --------------------------------------------------------------- functions


def test_function_insert_on_empty():
    assert build(lambda b: b.function("√(")).expression == "√("


def test_function_insert_after_operator():
    b = build(digit("9"), oper("+"), lambda b: b.function("sin("))
    assert b.expression == "9+sin("


def test_function_insert_ignored_right_after_value():
    assert build(digit("9"), lambda b: b.function("√(")).expression == "9"


def test_function_insert_ignored_after_closing_paren():
    b = build(lambda b: b.open_paren(), digit("9"),
              lambda b: b.close_paren(), lambda b: b.function("sin("))
    assert b.expression == "(9)"


# ------------------------------------------------------- backspace / clear


def test_backspace_removes_last_char():
    assert build(digit("1"), digit("2"), lambda b: b.backspace()).expression == "1"


def test_backspace_on_empty_returns_false():
    assert ExpressionBuilder().backspace() is False


def test_backspace_drains_to_empty_then_reports_false():
    builder = build(digit("7"))
    assert builder.backspace() is True
    assert builder.backspace() is False


def test_clear_empties_everything():
    b = build(digit("9"), oper("+"), digit("1"), lambda b: b.clear())
    assert b.expression == ""


# ------------------------------------------------------------------- seed


def test_seed_replaces_content_with_plain_number():
    b = build(digit("9"), oper("+"), digit("1"), lambda b: b.seed("-12.5"))
    assert b.expression == "-12.5"
    assert b.can_evaluate is True


# ------------------------------------------------------------ can_evaluate


def test_can_evaluate_true_for_value_position():
    assert build(digit("5")).can_evaluate is True


def test_can_evaluate_false_for_dangling_operator():
    assert build(digit("5"), oper("+")).can_evaluate is False


def test_can_evaluate_false_when_empty():
    assert ExpressionBuilder().can_evaluate is False


# -------------------------------------------------------------------- raw


def test_raw_inserts_trusted_tokens_verbatim():
    b = build(digit("9"), lambda b: b.raw("√("))
    assert b.expression == "9√("
