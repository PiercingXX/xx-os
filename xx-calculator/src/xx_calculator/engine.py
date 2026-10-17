"""Calculation engine: lexer, parser, evaluator and display formatting.

Direct port of ref/xx-calculator's engine/ (Calculator.kt, Parser.kt,
Evaluator.kt, Format.kt, AngleMode.kt) to Python's decimal module, which
mirrors java.math.BigDecimal closely enough to keep the same precision
policy: exact + - x, 50-digit division, integer powers, factorials and
square roots; transcendental functions detour through platform doubles
and re-enter already snapped to display precision.

Never evaluates user input with eval: display-form text goes through a
real lexer and recursive-descent parser.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal
from decimal import InvalidOperation
from decimal import ROUND_DOWN
from decimal import ROUND_HALF_EVEN
from decimal import localcontext
from enum import Enum

MINUS = "−"
TIMES = "×"
DIVIDE = "÷"

PREC_WORK = 50
PREC_FUNC = 12
PREC_FINAL = 15
INT_MAX = 2_147_483_647


class ErrorKind(Enum):
    DIVIDE_BY_ZERO = "DIVIDE_BY_ZERO"
    SYNTAX = "SYNTAX"
    DOMAIN = "DOMAIN"


class AngleMode(Enum):
    DEG = "DEG"
    RAD = "RAD"


@dataclass(frozen=True)
class Success:
    value: Decimal
    text: str


@dataclass(frozen=True)
class Failure:
    kind: ErrorKind
    message: str


class EvalResult:
    Success = Success
    Failure = Failure


class EngineException(Exception):
    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


def _syntax() -> EngineException:
    return EngineException(ErrorKind.SYNTAX, "Invalid expression")


def _domain(message: str = "Invalid input") -> EngineException:
    return EngineException(ErrorKind.DOMAIN, message)


def _round(value: Decimal, prec: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = prec
        ctx.rounding = ROUND_HALF_EVEN
        return +value


def _work_div(a: Decimal, b: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = PREC_WORK
        ctx.rounding = ROUND_HALF_EVEN
        return a / b


# ------------------------------------------------------------------ format


_E13 = Decimal("1e13")
_E_MINUS_9 = Decimal("1e-9")
_GROUP_RE = re.compile(r"^(-?)([0-9]+)(\.[0-9]+)?$")


def _to_plain(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("-0", ""):
        return "0"
    return text


def _e_form(value: Decimal) -> str:
    sign = "-" if value.is_signed() else ""
    magnitude = abs(value)
    exponent = magnitude.adjusted()
    mantissa = _to_plain(magnitude.scaleb(-exponent))
    return f"{sign}{mantissa}E{exponent}"


def format_plain(value: Decimal) -> str:
    rounded = _round(value, PREC_FINAL)
    if rounded.is_zero():
        return "0"
    magnitude = abs(rounded)
    if magnitude < _E13 and magnitude >= _E_MINUS_9:
        return _to_plain(rounded)
    return _e_form(rounded)


def format_grouped(plain_text: str) -> str:
    match = _GROUP_RE.match(plain_text)
    if match is None:
        return plain_text
    sign, int_part, frac_part = match.groups()
    out = [sign]
    for index, char in enumerate(int_part):
        if index > 0 and (len(int_part) - index) % 3 == 0:
            out.append(",")
        out.append(char)
    if frac_part:
        out.append(frac_part)
    return "".join(out)


# ------------------------------------------------------------------ lexer


class _Kind(Enum):
    NUM = "NUM"
    OP = "OP"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    PERCENT = "PERCENT"
    FACT = "FACT"
    FUNC = "FUNC"
    CONST = "CONST"
    END = "END"


@dataclass(frozen=True)
class _Token:
    kind: _Kind
    text: str
    num: Decimal | None = None


_FUNCTIONS = frozenset(
    {"sin", "cos", "tan", "asin", "acos", "atan", "ln", "log", "√"}
)


def _trailing_dot_number(text: str) -> Decimal | None:
    if not text.endswith("."):
        return None
    prefix = text[:-1]
    if prefix and not prefix.isdigit():
        return None
    return Decimal(0) if not prefix else Decimal(prefix)


def _lex_number(text: str, start: int) -> tuple[_Token, int]:
    i = start
    while i < len(text) and (text[i].isdigit() or text[i] == "."):
        i += 1
    if i < len(text) and text[i] == "E":
        j = i + 1
        if j < len(text) and text[j] in ("+", "-"):
            j += 1
        if j < len(text) and text[j].isdigit():
            while j < len(text) and text[j].isdigit():
                j += 1
            i = j
    word = text[start:i]
    try:
        value = Decimal(word)
    except InvalidOperation:
        value = None
    if value is None:
        value = _trailing_dot_number(word)
    if value is None:
        raise _syntax()
    return _Token(_Kind.NUM, word, value), i


def _lex(text: str) -> list[_Token]:
    out: list[_Token] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char.isdigit() or char == ".":
            token, i = _lex_number(text, i)
            out.append(token)
        elif char == "+":
            out.append(_Token(_Kind.OP, "+"))
            i += 1
        elif char in (MINUS, "-"):
            out.append(_Token(_Kind.OP, MINUS))
            i += 1
        elif char in (TIMES, "*"):
            out.append(_Token(_Kind.OP, TIMES))
            i += 1
        elif char in (DIVIDE, "/"):
            out.append(_Token(_Kind.OP, DIVIDE))
            i += 1
        elif char == "^":
            out.append(_Token(_Kind.OP, "^"))
            i += 1
        elif char == "%":
            out.append(_Token(_Kind.PERCENT, "%"))
            i += 1
        elif char == "!":
            out.append(_Token(_Kind.FACT, "!"))
            i += 1
        elif char == "(":
            out.append(_Token(_Kind.LPAREN, "("))
            i += 1
        elif char == ")":
            out.append(_Token(_Kind.RPAREN, ")"))
            i += 1
        elif char == "π":
            out.append(_Token(_Kind.CONST, "π"))
            i += 1
        elif char == "e":
            out.append(_Token(_Kind.CONST, "e"))
            i += 1
        elif char.isalpha() or char == "√":
            start = i
            while i < len(text) and (text[i].isalpha() or text[i] == "√"):
                i += 1
            name = text[start:i]
            if name not in _FUNCTIONS:
                raise _syntax()
            if i >= len(text) or text[i] != "(":
                raise _syntax()
            out.append(_Token(_Kind.FUNC, name))
            out.append(_Token(_Kind.LPAREN, "("))
            i += 1
        elif char.isspace():
            i += 1
        else:
            raise _syntax()
    out.append(_Token(_Kind.END, ""))
    return out


# ------------------------------------------------------------------ parser


@dataclass(frozen=True)
class _Num:
    value: Decimal


@dataclass(frozen=True)
class _Neg:
    child: object


@dataclass(frozen=True)
class _Bin:
    op: str
    left: object
    right: object


@dataclass(frozen=True)
class _Pct:
    child: object


@dataclass(frozen=True)
class _Fact:
    child: object


@dataclass(frozen=True)
class _Call:
    func: str
    child: object


class _Parser:
    def __init__(self, tokens: list[_Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def parse(self) -> object:
        node = self._additive()
        if self._peek().kind is not _Kind.END:
            raise _syntax()
        return node

    def _peek(self) -> _Token:
        return self._tokens[self._pos]

    def _next(self) -> _Token:
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _additive(self) -> object:
        left = self._multiplicative()
        while self._peek().kind is _Kind.OP and self._peek().text in ("+", MINUS):
            op = self._next().text
            right = self._multiplicative()
            if isinstance(right, _Pct):
                fraction = _Bin(DIVIDE, right.child, _Num(Decimal(100)))
                right = _Bin(TIMES, left, fraction)
            left = _Bin(op, left, right)
        return left

    def _multiplicative(self) -> object:
        left = self._unary()
        while self._peek().kind is _Kind.OP and self._peek().text in (TIMES, DIVIDE):
            op = self._next().text
            left = _Bin(op, left, self._unary())
        return left

    def _unary(self) -> object:
        if self._peek().kind is _Kind.OP and self._peek().text == MINUS:
            self._next()
            return _Neg(self._unary())
        return self._power()

    def _power(self) -> object:
        base = self._postfix()
        if self._peek().kind is _Kind.OP and self._peek().text == "^":
            self._next()
            return _Bin("^", base, self._unary())
        return base

    def _postfix(self) -> object:
        node = self._primary()
        while True:
            kind = self._peek().kind
            if kind is _Kind.PERCENT:
                self._next()
                node = _Pct(node)
            elif kind is _Kind.FACT:
                self._next()
                node = _Fact(node)
            else:
                return node

    def _primary(self) -> object:
        token = self._next()
        if token.kind is _Kind.NUM:
            if token.num is None:
                raise _syntax()
            return _Num(token.num)
        if token.kind is _Kind.CONST:
            return _Num(_constant(token.text))
        if token.kind is _Kind.LPAREN:
            node = self._additive()
            if self._next().kind is not _Kind.RPAREN:
                raise _syntax()
            return node
        if token.kind is _Kind.FUNC:
            if self._next().kind is not _Kind.LPAREN:
                raise _syntax()
            node = self._additive()
            if self._next().kind is not _Kind.RPAREN:
                raise _syntax()
            return _Call(token.text, node)
        raise _syntax()


_PI = Decimal("3.1415926535897932384626433832795028841972")
_E = Decimal("2.7182818284590452353602874713526624977572")


def _constant(name: str) -> Decimal:
    if name == "π":
        return _PI
    if name == "e":
        return _E
    raise _syntax()


# --------------------------------------------------------------- evaluator


_HUNDRED = Decimal(100)
_ONE = Decimal(1)
_POLE_EPS = Decimal("1e-9")
_ONE_EIGHTY = Decimal(180)
_NINETY = Decimal(90)
_MAX_FACTORIAL = 169


def _to_float(value: Decimal) -> float:
    try:
        return float(value)
    except OverflowError:
        return math.inf if value >= 0 else -math.inf


def _remainder(left: Decimal, right: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = PREC_WORK
        ctx.rounding = ROUND_HALF_EVEN
        quotient = (left / right).to_integral_value(rounding=ROUND_DOWN)
        return left - quotient * right


def _as_int(value: Decimal) -> int | None:
    if value.is_zero():
        return 0
    stripped = value.normalize()
    if stripped.as_tuple().exponent < 0:
        return None
    try:
        number = int(stripped)
    except (ValueError, OverflowError):
        return None
    if abs(number) > INT_MAX:
        return None
    return number


def _integral_power(base: Decimal, kind_exp: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = PREC_WORK
        ctx.rounding = ROUND_HALF_EVEN
        if kind_exp >= 0:
            return base**kind_exp
        if base.is_zero():
            raise EngineException(
                ErrorKind.DIVIDE_BY_ZERO, "Can't divide by 0"
            )
        return _ONE / (base ** (-kind_exp))


def _power(base: Decimal, exponent: Decimal) -> Decimal:
    kind_exp = _as_int(exponent)
    if kind_exp is not None:
        return _integral_power(base, kind_exp)
    if base <= 0:
        raise _domain()
    try:
        approx = math.exp(_to_float(exponent) * math.log(_to_float(base)))
    except (OverflowError, ValueError):
        raise _domain()
    if not math.isfinite(approx):
        raise _domain()
    return _round(Decimal(approx), PREC_FINAL)


def _factorial(value: Decimal) -> Decimal:
    if value.is_zero():
        return Decimal(1)
    stripped = value.normalize()
    if stripped.as_tuple().exponent < 0 or stripped < 0:
        raise _domain()
    number = int(stripped)
    if number > _MAX_FACTORIAL:
        raise EngineException(ErrorKind.DOMAIN, "Overflow")
    return Decimal(math.factorial(number))


def _sqrt(value: Decimal) -> Decimal:
    if value.is_zero():
        return Decimal(0)
    with localcontext() as ctx:
        ctx.prec = PREC_WORK + 10
        ctx.rounding = ROUND_HALF_EVEN
        two = Decimal(2)
        seed = _to_float(value)
        if math.isfinite(seed) and seed > 0.0:
            guess = Decimal(math.sqrt(seed))
        else:
            guess = Decimal(1).scaleb((value.adjusted() + 1) // 2)
        for _ in range(64):
            nxt = (guess + value / guess) / two
            if nxt == guess:
                return _round(nxt, PREC_WORK)
            guess = nxt
        return _round(guess, PREC_WORK)


_SNAP_EPSILON = 1e-12


def _snap_unit(value: float) -> float:
    if abs(value) < _SNAP_EPSILON:
        return 0.0
    if abs(value - 1.0) < _SNAP_EPSILON:
        return 1.0
    if abs(value + 1.0) < _SNAP_EPSILON:
        return -1.0
    return value


def _from_double(value: float) -> Decimal:
    if not math.isfinite(value):
        raise _domain()
    return _round(Decimal(value), PREC_FUNC)


def _reject_tan_pole(value: Decimal, mode: AngleMode) -> None:
    if mode is AngleMode.DEG:
        rest = abs(_remainder(value, _ONE_EIGHTY))
        if abs(rest - _NINETY) < _POLE_EPS:
            raise _domain()
    else:
        half_pi = _work_div(_PI, Decimal(2))
        rest = abs(_remainder(value, _PI))
        if abs(rest - half_pi) < _POLE_EPS:
            raise _domain()


def _call(func: str, arg: Decimal, mode: AngleMode) -> Decimal:
    if func == "√":
        if arg < 0:
            raise _domain()
        return _sqrt(arg)
    if func == "ln":
        if arg <= 0:
            raise _domain()
        return _from_double(math.log(_to_float(arg)))
    if func == "log":
        if arg <= 0:
            raise _domain()
        return _from_double(math.log10(_to_float(arg)))
    if func in ("sin", "cos", "tan"):
        radians = (
            math.radians(_to_float(arg))
            if mode is AngleMode.DEG
            else _to_float(arg)
        )
        if func == "sin":
            return _from_double(_snap_unit(math.sin(radians)))
        if func == "cos":
            return _from_double(_snap_unit(math.cos(radians)))
        _reject_tan_pole(arg, mode)
        return _from_double(_snap_unit(math.tan(radians)))
    if func in ("asin", "acos", "atan"):
        if func != "atan" and abs(arg) > _ONE:
            raise _domain()
        raw = _to_float(arg)
        if func == "asin":
            result = math.asin(raw)
        elif func == "acos":
            result = math.acos(raw)
        else:
            result = math.atan(raw)
        if mode is AngleMode.DEG:
            result = math.degrees(result)
        return _from_double(result)
    raise _syntax()


def _eval(node: object, mode: AngleMode) -> Decimal:
    if isinstance(node, _Num):
        return node.value
    if isinstance(node, _Neg):
        return -_eval(node.child, mode)
    if isinstance(node, _Pct):
        return _work_div(_eval(node.child, mode), _HUNDRED)
    if isinstance(node, _Fact):
        return _factorial(_eval(node.child, mode))
    if isinstance(node, _Call):
        return _call(node.func, _eval(node.child, mode), mode)
    if isinstance(node, _Bin):
        if node.op == "+":
            return _eval(node.left, mode) + _eval(node.right, mode)
        if node.op == MINUS:
            return _eval(node.left, mode) - _eval(node.right, mode)
        if node.op == TIMES:
            return _eval(node.left, mode) * _eval(node.right, mode)
        if node.op == DIVIDE:
            right = _eval(node.right, mode)
            if right.is_zero():
                raise EngineException(
                    ErrorKind.DIVIDE_BY_ZERO, "Can't divide by 0"
                )
            return _work_div(_eval(node.left, mode), right)
        if node.op == "^":
            return _power(_eval(node.left, mode), _eval(node.right, mode))
    raise _syntax()


# ------------------------------------------------------------------ entry


class Calculator:
    """Public entry point: evaluate display-form expressions."""

    @staticmethod
    def evaluate(
        expression: str, angle_mode: AngleMode = AngleMode.DEG
    ) -> Success | Failure:
        trimmed = expression.strip()
        if not trimmed:
            return EvalResult.Failure(ErrorKind.SYNTAX, "Invalid expression")
        try:
            node = _Parser(_lex(trimmed)).parse()
            value = _round(_eval(node, angle_mode), PREC_FINAL)
            if value.is_zero():
                value = Decimal(0)
            return EvalResult.Success(value, format_plain(value))
        except EngineException as exc:
            return EvalResult.Failure(exc.kind, exc.message)
        except InvalidOperation:
            return EvalResult.Failure(ErrorKind.DOMAIN, "Invalid input")
        except (OverflowError, ValueError):
            return EvalResult.Failure(ErrorKind.DOMAIN, "Invalid input")
