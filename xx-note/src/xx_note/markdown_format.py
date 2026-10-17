"""Markdown editing commands (port of ``core/MarkdownFormat.kt``).

The same commands, with the same results, as the editor toolbar: each is
a pure function of (text, selection) returning the new text and selection,
or None when there is nothing to do.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
import re

BLOCK_COMMANDS = frozenset(
    {
        "H1", "H2", "H3", "H0", "BULLET", "NUMBER", "TASK", "QUOTE",
        "CODE_BLOCK", "RULE", "INDENT", "OUTDENT",
    }
)


class Command(Enum):
    BOLD = auto()
    ITALIC = auto()
    STRIKE = auto()
    HIGHLIGHT = auto()
    CODE = auto()
    LINK = auto()
    H1 = auto()
    H2 = auto()
    H3 = auto()
    H0 = auto()
    BULLET = auto()
    NUMBER = auto()
    TASK = auto()
    QUOTE = auto()
    CODE_BLOCK = auto()
    RULE = auto()
    INDENT = auto()
    OUTDENT = auto()
    CLEAR = auto()


@dataclass(frozen=True)
class Edit:
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class ParsedLine:
    indent: str
    kind: str | None
    prefix: str
    rest: str
    n: int


_LIST = re.compile(
    r"^([ \t]*)(?:([-*+]) \[([ xX])\] |([-*+]) |(\d+)([.)]) |(> ?)|(#{1,6}) )?(.*)$"
)

_LIST_KINDS = frozenset({"TASK", "BULLET", "NUMBER", "QUOTE"})
_HEADINGS = ("H1", "H2", "H3", "H4", "H5", "H6")


def parse_line(line: str) -> ParsedLine:
    match = _LIST.match(line)
    if match is None:
        return ParsedLine("", None, "", line, 0)
    g = match.groups()
    indent, task_m, task_c, bullet, num, num_c, quote, hashes, rest = g
    if task_m:
        return ParsedLine(indent, "TASK", f"{task_m} [{task_c}] ", rest, 0)
    if bullet:
        return ParsedLine(indent, "BULLET", f"{bullet} ", rest, 0)
    if num:
        return ParsedLine(indent, "NUMBER", f"{num}{num_c} ", rest, int(num) if num.isdigit() else 0)
    if quote:
        return ParsedLine(indent, "QUOTE", quote, rest, 0)
    if hashes:
        kind = _HEADINGS[len(hashes) - 1]
        return ParsedLine(indent, kind, f"{hashes} ", rest, 0)
    return ParsedLine(indent, None, "", rest, 0)


def _is_heading(kind: str | None) -> bool:
    return kind in _HEADINGS


def apply(command: Command, text: str, start: int, end: int) -> Edit | None:
    s = min(start, end)
    e = max(start, end)
    s = max(0, min(s, len(text)))
    e = max(0, min(e, len(text)))
    if command == Command.BOLD:
        return _wrap(text, s, e, "**")
    if command == Command.ITALIC:
        return _wrap(text, s, e, "*")
    if command == Command.STRIKE:
        return _wrap(text, s, e, "~~")
    if command == Command.HIGHLIGHT:
        return _wrap(text, s, e, "==")
    if command == Command.CODE:
        return _wrap(text, s, e, "`")
    if command == Command.LINK:
        return _link(text, s, e)
    if command == Command.H1:
        return _heading(text, s, e, 1)
    if command == Command.H2:
        return _heading(text, s, e, 2)
    if command == Command.H3:
        return _heading(text, s, e, 3)
    if command == Command.H0:
        return _heading(text, s, e, 0)
    if command == Command.BULLET:
        return _block(text, s, e, "BULLET")
    if command == Command.NUMBER:
        return _block(text, s, e, "NUMBER")
    if command == Command.TASK:
        return _block(text, s, e, "TASK")
    if command == Command.QUOTE:
        return _block(text, s, e, "QUOTE")
    if command == Command.CODE_BLOCK:
        return _code_block(text, s, e)
    if command == Command.RULE:
        return _rule(text, s)
    if command == Command.INDENT:
        return _indent(text, s, e, 1)
    if command == Command.OUTDENT:
        return _indent(text, s, e, -1)
    if command == Command.CLEAR:
        return _clear(text, s, e)
    raise ValueError(command)


def enter(text: str, start: int, end: int) -> Edit | None:
    if start != end:
        return None
    s = max(0, min(start, len(text)))
    from_ = text.rfind("\n", 0, s - 1) + 1 if s > 0 else 0
    to = text.find("\n", s)
    if to < 0:
        to = len(text)
    p = parse_line(text[from_:to])
    if p.kind not in _LIST_KINDS or s < from_ + len(p.indent) + len(p.prefix):
        return None
    if not p.rest.strip():
        return Edit(text[:from_] + text[to:], from_, from_)
    if p.kind == "TASK":
        nxt = re.sub(r"\[[xX]\]", "[ ]", p.prefix)
    elif p.kind == "NUMBER":
        nxt = f"{p.n + 1}{p.prefix[len(str(p.n)):]}"
    else:
        nxt = p.prefix
    ins = "\n" + p.indent + nxt
    return Edit(text[:s] + ins + text[s:], s + len(ins), s + len(ins))


def toggle_checkbox(line: str) -> str | None:
    """Flip a GFM checkbox on one line; None when the line has none."""
    updated, count = re.subn(r"\[ \]", "[x]", line, count=1)
    if count:
        return updated
    updated, count = re.subn(r"\[[xX]\]", "[ ]", line, count=1)
    return updated if count else None


def _line_span(text: str, s: int, e: int) -> tuple[int, int]:
    from_ = text.rfind("\n", 0, s - 1) + 1 if s > 0 else 0
    stop = e - 1 if e > s and text[e - 1] == "\n" else e
    to = text.find("\n", stop)
    if to < 0:
        to = len(text)
    return from_, max(from_, to)


def _map_lines(text: str, s: int, e: int, fn) -> Edit | None:  # type: ignore[no-untyped-def]
    from_, to = _line_span(text, s, e)
    before = text[from_:to]
    after = "\n".join(fn(before.split("\n")))
    if after == before:
        return None
    out = text[:from_] + after + text[to:]
    if s == e and "\n" not in before:
        caret = min(from_ + len(after), max(from_, s + len(after) - len(before)))
        return Edit(out, caret, caret)
    return Edit(out, from_, from_ + len(after))


def _prefix_for(kind: str, n: int) -> str:
    if kind == "TASK":
        return "- [ ] "
    if kind == "BULLET":
        return "- "
    if kind == "NUMBER":
        return f"{n}. "
    return "> "


def _block(text: str, s: int, e: int, kind: str) -> Edit | None:
    def fn(lines: list[str]) -> list[str]:
        if len(lines) == 1 and not lines[0].strip():
            return [lines[0] + _prefix_for(kind, 1)]
        live = [None if not line.strip() else parse_line(line) for line in lines]
        all_same = all(p is None or p.kind == kind for p in live) and any(
            p is not None for p in live
        )
        n = 0
        out = []
        for line, p in zip(lines, live):
            if p is None:
                out.append(line)
            elif all_same:
                out.append(p.indent + p.rest)
            else:
                n += 1
                out.append(p.indent + _prefix_for(kind, n) + p.rest)
        return out

    return _map_lines(text, s, e, fn)


def _heading(text: str, s: int, e: int, level: int) -> Edit | None:
    want = _HEADINGS[level - 1] if level > 0 else None

    def fn(lines: list[str]) -> list[str]:
        all_same = all(not line.strip() or parse_line(line).kind == want for line in lines)
        out = []
        for line in lines:
            if not line.strip():
                out.append(line)
                continue
            p = parse_line(line)
            if _is_heading(p.kind):
                rest = p.rest
            elif p.kind is not None:
                rest = p.prefix + p.rest
            else:
                rest = p.rest
            if all_same or level == 0:
                out.append(p.indent + rest)
            else:
                out.append(p.indent + "#" * level + " " + rest)
        return out

    return _map_lines(text, s, e, fn)


_WORD_LEFT = re.compile(r"[\w'-]*$")
_WORD_RIGHT = re.compile(r"^[\w'-]*")


def _wrap(text: str, s: int, e: int, token: str, close: str | None = None) -> Edit:
    close = token if close is None else close
    sel = text[s:e]
    outside = (
        s >= len(token)
        and e + len(close) <= len(text)
        and text[s - len(token): s] == token
        and text[e: e + len(close)] == close
    )
    doubled = (
        len(token) == 1
        and s - len(token) - 1 >= 0
        and text[s - len(token) - 1] == token[0]
        and e + len(close) < len(text)
        and text[e + len(close)] == close[0]
    )
    if outside and not doubled:
        return Edit(
            text[: s - len(token)] + sel + text[e + len(close):],
            s - len(token),
            e - len(token),
        )
    if (
        len(sel) >= len(token) + len(close)
        and sel.startswith(token)
        and sel.endswith(close)
    ):
        inner = sel[len(token): len(sel) - len(close)]
        return Edit(text[:s] + inner + text[e:], s, s + len(inner))
    if s == e:
        left = (_WORD_LEFT.search(text[:s]) or re.match(r"", "")).group(0)
        right = (_WORD_RIGHT.match(text[s:]) or re.match(r"", "")).group(0)
        if left or right:
            return _wrap(text, s - len(left), s + len(right), token, close)
        return Edit(
            text[:s] + token + close + text[s:], s + len(token), s + len(token)
        )
    lead = len(sel) - len(sel.lstrip())
    trail = len(sel) - len(sel.rstrip())
    a = s + lead
    b = max(a, e - trail)
    return Edit(
        text[:a] + token + text[a:b] + close + text[b:],
        a + len(token),
        b + len(token),
    )


def _link(text: str, s: int, e: int) -> Edit:
    sel = text[s:e]
    if re.fullmatch(r"https?://\S+", sel or ""):
        return Edit(text[:s] + "[](" + sel + ")" + text[e:], s + 1, s + 1)
    out = text[:s] + "[" + sel + "](url)" + text[e:]
    u = s + len(sel) + 3
    if sel:
        return Edit(out, u, u + 3)
    return Edit(out, s + 1, s + 1)


def _code_block(text: str, s: int, e: int) -> Edit:
    from_, to = _line_span(text, s, e)
    body = text[from_:to]
    if re.fullmatch(r"```[^\n]*\n[\s\S]*\n```", body):
        inner = re.sub(r"^```[^\n]*\n", "", body)[: -len("\n```")]
        return Edit(text[:from_] + inner + text[to:], from_, from_ + len(inner))
    out = text[:from_] + "```\n" + body + "\n```" + text[to:]
    return Edit(out, from_ + 4, from_ + 4 + len(body))


def _rule(text: str, s: int) -> Edit:
    from_, to = _line_span(text, s, s)
    after = to + 1 if to != len(text) else len(text)
    if not text[from_:to].strip():
        return Edit(text[:from_] + "---\n" + text[after:], from_ + 4, from_ + 4)
    return Edit(text[:to] + "\n\n---\n" + text[after:], to + 6, to + 6)


def _indent(text: str, s: int, e: int, direction: int) -> Edit | None:
    def fn(lines: list[str]) -> list[str]:
        out = []
        for line in lines:
            if not line.strip():
                out.append(line)
            elif direction > 0:
                out.append("  " + line)
            elif line.startswith("\t"):
                out.append(line[1:])
            else:
                out.append(re.sub(r"^ {1,2}", "", line))
        return out

    return _map_lines(text, s, e, fn)


def _clear(text: str, s: int, e: int) -> Edit | None:
    if s == e:
        a, b = _line_span(text, s, e)
    else:
        a, b = s, e
    sel = text[a:b]
    out_lines = []
    for line in sel.split("\n"):
        p = parse_line(line)
        cur = p.indent + p.rest if (_is_heading(p.kind) or p.kind == "QUOTE") else line
        cur = re.sub(r"(\*\*|__|~~|==)(.+?)\1", r"\2", cur)
        cur = re.sub(r"`([^`]+)`", r"\1", cur)
        cur = re.sub(r"(^|[^*\w])\*([^\s*][^*]*?)\*(?!\*)", r"\1\2", cur)
        cur = re.sub(r"(^|[^_\w])_([^_\s][^_]*?)_(?!_)", r"\1\2", cur)
        out_lines.append(cur)
    out = "\n".join(out_lines)
    if out == sel:
        return None
    return Edit(text[:a] + out + text[b:], a, a + len(out))
