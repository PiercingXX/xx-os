"""Byte-exact YAML-frontmatter view of one Markdown note.

Port of ``core/Frontmatter.kt`` (design sections 4.3, 8): parsing is
lenient — unknown keys survive verbatim, malformed blocks degrade to body
text, nothing is ever dropped. Only the first line (exactly ``---``) opens
a block, and only the first subsequent exact ``---`` or ``...`` closes it.
"""

from __future__ import annotations

from dataclasses import dataclass

OPEN_DELIMITER = "---"
CLOSE_DELIMITER = "---"
CLOSE_YAML_END = "..."
BOM = "\ufeff"

K_ID = "id"
K_TITLE = "title"
K_CREATED = "created"
K_UPDATED = "updated"
K_PINNED = "pinned"
K_ARCHIVED = "archived"
K_COLOR = "color"
K_LABELS = "labels"
K_TYPE = "type"
K_REMINDER = "reminder"
K_CONFLICT_OF = "conflictOf"
K_CONFLICT_AT = "conflictAt"
K_TRASHED_AT = "trashedAt"

ORDER = (
    K_ID, K_TITLE, K_CREATED, K_UPDATED, K_PINNED, K_ARCHIVED, K_COLOR,
    K_LABELS, K_TYPE, K_REMINDER, K_CONFLICT_OF, K_CONFLICT_AT, K_TRASHED_AT,
)

NOTE = "note"
CHECKLIST = "checklist"


def _lenient_bool(scalar: str) -> bool | None:
    low = scalar.lower()
    if low in ("true", "yes", "on", "1"):
        return True
    if low in ("false", "no", "off", "0"):
        return False
    return None


def _lenient_type(scalar: str | None) -> str:
    if scalar is not None and scalar.lower() == "checklist":
        return CHECKLIST
    return NOTE


def _unquote(value: str) -> str:
    if len(value) < 2:
        return value
    first, last = value[0], value[-1]
    if not ((first == '"' and last == '"') or (first == "'" and last == "'")):
        return value
    inner = value[1:-1]
    if first != '"':
        return inner
    out: list[str] = []
    i = 0
    while i < len(inner):
        char = inner[i]
        if char == "\\" and i + 1 < len(inner) and inner[i + 1] in ("\\", '"'):
            out.append(inner[i + 1])
            i += 2
            continue
        out.append(char)
        i += 1
    return "".join(out)


def _scalar_after(raw: str) -> str:
    value = raw[1:] if raw.startswith(" ") else raw
    return _unquote(value.rstrip())


def _flow_items(scalar: str) -> list[str]:
    text = scalar.strip()
    if not text.startswith("["):
        return [] if not text else [_unquote(text)]
    inner = text[1:-1] if text.endswith("]") else text[1:]
    out: list[str] = []
    for segment in _split_top_level(inner):
        if not segment.strip():
            continue
        out.append(_unquote(segment.strip()))
    return out


def _split_top_level(inner: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    single = False
    double = False
    escaped = False
    for char in inner:
        if escaped:
            current.append(char)
            escaped = False
        elif double and char == "\\":
            current.append(char)
            escaped = True
        elif char == "," and not single and not double:
            parts.append("".join(current))
            current = []
        else:
            if char == "'" and not double:
                single = not single
            elif char == '"' and not single:
                double = not double
            current.append(char)
    parts.append("".join(current))
    return parts


def _needs_quoting(value: str) -> bool:
    if not value or value != value.strip():
        return True
    if ": " in value or value.endswith(":"):
        return True
    if " #" in value:
        return True
    if any(c in value for c in (",", "[", "]")):
        return True
    if value[0] in "[]{}'\"#&*!|>%@`-?: " + "\t":
        return True
    return False


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _render_value(target: object) -> str:
    if isinstance(target, bool):
        return "true" if target else "false"
    if isinstance(target, str) and target in (NOTE, CHECKLIST):
        return target
    if isinstance(target, list):
        return "[" + ", ".join(
            _quote(i) if _needs_quoting(i) else i for i in target
        ) + "]"
    if isinstance(target, str):
        if "\n" in target or "\r" in target:
            raise ValueError("frontmatter value must not contain a line break")
        return _quote(target) if _needs_quoting(target) else target
    raise ValueError(f"unsupported frontmatter value: {target!r}")


def _key_shape_of(content: str) -> str | None:
    if not content.strip():
        return ""
    colon = content.find(":")
    if colon <= 0:
        return None
    key = content[:colon].strip()
    if not key:
        return None
    after = content[colon + 1: colon + 2]
    if after and after not in (" ", "\t"):
        return None
    return key


@dataclass
class _Line:
    text: str
    terminator: str
    key: str | None


@dataclass
class _Block:
    lines: list[_Line]
    entries: dict[str, str]
    open_terminator: str
    closing_text: str
    closing_terminator: str
    append_terminator: str
    body: int


class OwnedKeys:
    """Mutable §8 state handed to :meth:`FrontmatterDocument.rewritten`."""

    def __init__(self, **kwargs: object) -> None:
        self.touched: set[str] = set()
        self._labels: list[str] = list(kwargs.get("labels", []))
        self._labels_dirty = False
        self._values: dict[str, object] = dict(kwargs)

    def __setattr__(self, name: str, value: object) -> None:
        if name.startswith("_") or name == "touched":
            object.__setattr__(self, name, value)
            return
        key = _ATTR_TO_KEY.get(name, name)
        self._values[key] = value
        self.touched.add(key)

    def __getattr__(self, name: str) -> object:
        if name == "labels":
            return _TrackingLabels(self)
        key = _ATTR_TO_KEY.get(name, name)
        try:
            return self._values[key]
        except KeyError:
            raise AttributeError(name) from None

    def target_of(self, key: str) -> object:
        if key == K_LABELS:
            return list(self._labels)
        return self._values.get(key)


_ATTR_TO_KEY = {
    "conflict_of": K_CONFLICT_OF,
    "conflict_at": K_CONFLICT_AT,
    "trashed_at": K_TRASHED_AT,
    "note_type": K_TYPE,
}


class _TrackingLabels(list):  # type: ignore[type-arg]
    def __init__(self, owner: OwnedKeys) -> None:
        super().__init__(owner._labels)
        self._owner = owner

    def _mark(self) -> None:
        self._owner._labels_dirty = True

    def append(self, item: str) -> None:  # type: ignore[override]
        self._mark()
        super().append(item)

    def extend(self, items: object) -> None:  # type: ignore[override]
        self._mark()
        super().extend(items)  # type: ignore[arg-type]

    def insert(self, index: int, item: str) -> None:  # type: ignore[override]
        self._mark()
        super().insert(index, item)

    def remove(self, item: str) -> None:  # type: ignore[override]
        self._mark()
        super().remove(item)

    def pop(self, index: int = -1) -> str:
        self._mark()
        return super().pop(index)

    def clear(self) -> None:
        self._mark()
        super().clear()

    def __setitem__(self, index: object, value: object) -> None:
        self._mark()
        super().__setitem__(index, value)  # type: ignore[index]

    def __delitem__(self, index: object) -> None:
        self._mark()
        super().__delitem__(index)  # type: ignore[index]

    def commit(self) -> None:
        self._owner._labels = list(self)


@dataclass
class FrontmatterDocument:
    _raw: str
    _bom: str
    _block: _Block | None
    _saw_opening: bool

    @property
    def has_frontmatter(self) -> bool:
        return self._block is not None

    @property
    def is_malformed(self) -> bool:
        return self._block is None and self._saw_opening

    @property
    def body_text(self) -> str:
        if self._block is None:
            return self._raw
        return self._raw[self._block.body :]

    @property
    def id(self) -> str | None:
        return self._scalar(K_ID)

    @property
    def title(self) -> str | None:
        return self._scalar(K_TITLE)

    @property
    def created(self) -> str | None:
        return self._scalar(K_CREATED)

    @property
    def updated(self) -> str | None:
        return self._scalar(K_UPDATED)

    @property
    def pinned(self) -> bool | None:
        scalar = self._scalar(K_PINNED)
        return None if scalar is None else _lenient_bool(scalar)

    @property
    def archived(self) -> bool | None:
        scalar = self._scalar(K_ARCHIVED)
        return None if scalar is None else _lenient_bool(scalar)

    @property
    def color(self) -> str | None:
        return self._scalar(K_COLOR)

    @property
    def labels(self) -> list[str]:
        scalar = self._scalar(K_LABELS)
        return [] if scalar is None else _flow_items(scalar)

    @property
    def note_type(self) -> str:
        return _lenient_type(self._scalar(K_TYPE))

    @property
    def reminder(self) -> str | None:
        return self._scalar(K_REMINDER)

    @property
    def conflict_of(self) -> str | None:
        return self._scalar(K_CONFLICT_OF)

    @property
    def conflict_at(self) -> str | None:
        return self._scalar(K_CONFLICT_AT)

    @property
    def trashed_at(self) -> str | None:
        return self._scalar(K_TRASHED_AT)

    def raw(self) -> str:
        return self._raw

    def _scalar(self, key: str) -> str | None:
        if self._block is None:
            return None
        return self._block.entries.get(key)

    def _current_keys(self) -> OwnedKeys:
        keys = OwnedKeys(
            id=self.id,
            title=self.title,
            created=self.created,
            updated=self.updated,
            pinned=self.pinned,
            archived=self.archived,
            color=self.color,
            labels=self.labels,
            type=self.note_type,
            reminder=self.reminder,
            conflictOf=self.conflict_of,
            conflictAt=self.conflict_at,
            trashedAt=self.trashed_at,
        )
        keys.touched.clear()
        return keys

    def rewritten(self, mutate) -> str:  # type: ignore[no-untyped-def]
        current = self._current_keys()
        baseline = self._current_keys()
        mutate(current)
        if isinstance(current._labels, _TrackingLabels):
            current.commit()
        data = self._block
        if data is None:
            return _prepend_fresh(current, baseline, self._raw, self._bom)
        replacement: dict[str, str] = {}
        removals: set[str] = set()
        additions: list[str] = []
        for key in ORDER:
            touched = (
                current._labels_dirty if key == K_LABELS else key in current.touched
            )
            if not touched:
                continue
            target = current.target_of(key)
            if target == baseline.target_of(key):
                continue
            if target is None:
                if any(line.key == key for line in data.lines):
                    removals.add(key)
                continue
            line = f"{key}: {_render_value(target)}"
            if any(line_.key == key for line_ in data.lines):
                replacement[key] = line
            else:
                additions.append(line)
        if not replacement and not removals and not additions:
            return self._raw
        last_index: dict[str, int] = {}
        for i, line in enumerate(data.lines):
            if line.key:
                last_index[line.key] = i
        out = [self._bom, OPEN_DELIMITER, data.open_terminator]
        for i, line in enumerate(data.lines):
            key = line.key
            if key is not None and key in removals:
                continue
            if key is not None and key in replacement:
                if last_index[key] == i:
                    out.append(replacement[key])
                    out.append(line.terminator)
                continue
            out.append(line.text)
            out.append(line.terminator)
        for line in additions:
            out.append(line)
            out.append(data.append_terminator)
        out.append(data.closing_text)
        out.append(data.closing_terminator)
        out.append(self._raw[data.body :])
        return "".join(out)


def _prepend_fresh(
    after: OwnedKeys, baseline: OwnedKeys, raw_text: str, bom: str
) -> str:
    lines: list[str] = []
    for key in ORDER:
        touched = after._labels_dirty if key == K_LABELS else key in after.touched
        if not touched:
            continue
        target = after.target_of(key)
        if target is None or target == baseline.target_of(key):
            continue
        if isinstance(target, list) and not target:
            continue
        lines.append(f"{key}: {_render_value(target)}")
    if not lines:
        return raw_text
    eol = "\r\n" if "\r\n" in raw_text else "\n"
    rest = raw_text[len(bom):]
    out = [bom, OPEN_DELIMITER, eol]
    for line in lines:
        out.append(line)
        out.append(eol)
    out.append(CLOSE_DELIMITER)
    if rest:
        out.append(eol)
    out.append(rest)
    return "".join(out)


def parse(text: str) -> FrontmatterDocument:
    bom = BOM if text.startswith(BOM) else ""
    rest = text[len(bom):]
    first_break = rest.find("\n")
    first_line = (
        rest[:first_break].removesuffix("\r") if first_break >= 0 else rest
    )
    if first_line != OPEN_DELIMITER:
        return FrontmatterDocument(text, bom, None, False)
    open_end = first_break + 1 if first_break >= 0 else len(rest)
    if first_break >= 0:
        open_term = "\r\n" if first_break > 0 and rest[first_break - 1] == "\r" else "\n"
    else:
        open_term = ""
    lines: list[_Line] = []
    pos = open_end
    while True:
        if pos >= len(rest):
            return FrontmatterDocument(text, bom, None, True)
        brk = rest.find("\n", pos)
        end = brk if brk >= 0 else len(rest)
        if brk < 0:
            terminator = ""
        elif end > pos and rest[end - 1] == "\r":
            terminator = "\r\n"
        else:
            terminator = "\n"
        content_end = end - 1 if terminator == "\r\n" else end
        content = rest[pos:content_end]
        if content == OPEN_DELIMITER or content == CLOSE_YAML_END:
            entries: dict[str, str] = {}
            for line in lines:
                if not line.key:
                    continue
                if line.key in entries:
                    continue
                colon = line.text.find(":")
                raw = line.text[colon + 1 :] if colon >= 0 else ""
                entries[line.key] = _scalar_after(raw)
            last_term = next(
                (line.terminator for line in reversed(lines) if line.terminator), ""
            )
            append_term = last_term or terminator or "\n"
            body = len(bom) + end + (1 if brk >= 0 else 0)
            return FrontmatterDocument(
                text,
                bom,
                _Block(lines, entries, open_term, content, terminator, append_term, body),
                True,
            )
        key = _key_shape_of(content)
        if key is None:
            return FrontmatterDocument(text, bom, None, True)
        lines.append(_Line(content, terminator, key))
        pos = end + 1
