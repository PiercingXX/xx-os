"""Input-grammar state machine, ported from ExpressionBuilder.kt.

The builder stores the expression in display form using the glyphs the
UI shows: '-' (U+2212), 'x' (U+00D7), '/' (U+00F7), plus '%', '(', ')',
'!', '^', the pi symbol and 'e'. No implicit multiplication anywhere.
"""
from __future__ import annotations

from .engine import DIVIDE, MINUS, TIMES


def _is_operator(char: str) -> bool:
    return char in ("+", MINUS, TIMES, DIVIDE)


def _ends_value_char(char: str) -> bool:
    return (
        char.isdigit()
        or char in (".", ")", "%", "!", "π", "e")
    )


class ExpressionBuilder:
    """Display-form expression being typed, one grammar-checked key at a time."""

    def __init__(self) -> None:
        self._chars: list[str] = []

    @property
    def expression(self) -> str:
        return "".join(self._chars)

    @property
    def can_evaluate(self) -> bool:
        return bool(self._chars) and self._ends_with_value()

    def digit(self, char: str) -> None:
        if not char.isdigit() or len(char) != 1:
            return
        if self._current_number() == "0":
            self._chars.pop()
        self._chars.append(char)

    def decimal(self) -> None:
        number = self._current_number()
        if number is not None:
            if "." in number:
                return
            self._chars.append(".")
            return
        last = self._last()
        if last is not None and _ends_value_char(last):
            return
        self._chars.append("0.")

    def operator(self, char: str) -> None:
        glyph = {"+": "+", "-": MINUS, "*": TIMES, "/": DIVIDE}.get(char)
        if glyph is None:
            return
        last = self._last()
        if last is None or last == "(":
            if glyph == MINUS:
                self._chars.append(glyph)
            return
        if _is_operator(last):
            self._chars[-1] = glyph
            return
        self._chars.append(glyph)

    def percent(self) -> None:
        if self._ends_with_value():
            self._chars.append("%")

    def open_paren(self) -> None:
        self._chars.append("(")

    def close_paren(self) -> None:
        if self._can_close_paren():
            self._chars.append(")")

    def paren(self) -> None:
        if self._can_close_paren():
            self._chars.append(")")
        else:
            self.open_paren()

    def constant(self, char: str) -> None:
        if char in ("π", "e") and not self._ends_with_value():
            self._chars.append(char)

    def function(self, token: str) -> None:
        if not self._ends_with_value():
            self._chars.extend(token)

    def raw(self, token: str) -> None:
        self._chars.extend(token)

    def backspace(self) -> bool:
        if not self._chars:
            return False
        self._chars.pop()
        return True

    def clear(self) -> None:
        self._chars.clear()

    def seed(self, value: str) -> None:
        self._chars = list(value)

    # ------------------------------------------------------------- internals

    def _last(self) -> str | None:
        return self._chars[-1] if self._chars else None

    def _ends_with_value(self) -> bool:
        last = self._last()
        return last is not None and _ends_value_char(last)

    def _can_close_paren(self) -> bool:
        text = self.expression
        return text.count("(") > text.count(")") and self._ends_with_value()

    def _current_number(self) -> str | None:
        index = len(self._chars)
        while index > 0 and (
            self._chars[index - 1].isdigit() or self._chars[index - 1] == "."
        ):
            index -= 1
        if index == len(self._chars):
            return None
        return "".join(self._chars[index:])
