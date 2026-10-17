"""Local vault: plain Markdown files + a small JSON index.

Each note is one ``<ulid>-<slug>.md`` file with YAML frontmatter (see
``frontmatter.py``). The index (``<notes>/.xxnote/index.json``) holds base
snapshots for three-way merge, etags, and the outbox — it is a cache, and
the files are the source of truth. Deletes move to ``.xxnote/trash/`` and
only 7-day expiry unlinks (D9).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from xx_note import frontmatter as _fm
from xx_note import search_query as _sq
from xx_note import slug as _slug
from xx_note import trash as _trash
from xx_note import ulid as _ulid
from xx_note import paths as _paths

INDEX_NAME = "index.json"
TRASH_KEEP_MS = _trash.EXPIRY_MS


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class Note:
    id: str
    path: str
    title: str
    body: str
    pinned: bool = False
    archived: bool = False
    trashed: bool = False
    trashed_at: str | None = None
    color: str | None = None
    labels: list[str] = field(default_factory=list)
    note_type: str = "note"
    updated: str | None = None
    created: str | None = None


def _derive_title(doc: _fm.FrontmatterDocument) -> str:
    if doc.title:
        return doc.title
    for line in doc.body_text.split("\n"):
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped[:120]
    return "Untitled"


def _preview(body: str, lines: int = 6) -> str:
    out: list[str] = []
    for line in body.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        out.append(stripped[:160])
        if len(out) >= lines:
            break
    return "\n".join(out)


class Vault:
    def __init__(self, notes_dir: Path) -> None:
        self.notes_dir = notes_dir
        self._index_path = notes_dir / ".xxnote" / INDEX_NAME
        self._index: dict = {}

    def ensure(self) -> None:
        _paths.ensure_notes_tree(self.notes_dir)
        self._load_index()

    def _load_index(self) -> None:
        try:
            self._index = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._index = {"bases": {}, "etags": {}, "outbox": []}

    def _save_index(self) -> None:
        self._index_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self._index_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._index, indent=1), encoding="utf-8")
        tmp.replace(self._index_path)

    def scan(self) -> list[Note]:
        self._load_index()
        notes: list[Note] = []
        for path in sorted(self.notes_dir.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            doc = _fm.parse(text)
            note_id = doc.id or ""
            if not note_id:
                continue
            notes.append(
                Note(
                    id=note_id,
                    path=path.name,
                    title=_derive_title(doc),
                    body=doc.body_text,
                    pinned=bool(doc.pinned),
                    archived=bool(doc.archived),
                    color=doc.color,
                    labels=list(doc.labels),
                    note_type=doc.note_type,
                    updated=doc.updated,
                    created=doc.created,
                )
            )
        return notes

    def scan_trash(self) -> list[Note]:
        trash_dir = self.notes_dir / ".xxnote" / "trash"
        notes: list[Note] = []
        for path in sorted(trash_dir.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            doc = _fm.parse(text)
            notes.append(
                Note(
                    id=doc.id or path.name,
                    path=path.name,
                    title=_derive_title(doc),
                    body=doc.body_text,
                    trashed=True,
                    trashed_at=doc.trashed_at,
                    color=doc.color,
                    labels=list(doc.labels),
                    note_type=doc.note_type,
                    updated=doc.updated,
                    created=doc.created,
                )
            )
        return notes

    def read(self, name: str) -> str | None:
        try:
            return (self.notes_dir / name).read_text(encoding="utf-8")
        except OSError:
            return None

    def create(self, title: str, body: str = "", note_type: str = "note",
               labels: list[str] | None = None) -> Note:
        self.ensure()
        note_id = _ulid.generate()
        now = _utc_now_iso()
        doc_text = (
            f"---\nid: {note_id}\ntitle: {_quote_if_needed(title)}\n"
            f"created: {now}\nupdated: {now}\n"
        )
        if note_type == "checklist":
            doc_text += "type: checklist\n"
        if labels:
            doc_text += f"labels: [{', '.join(labels)}]\n"
        doc_text += f"---\n\n{body}" if body else "---\n"
        name = f"{note_id}-{_slug.of(title or 'untitled')}.md"
        (self.notes_dir / name).write_text(doc_text, encoding="utf-8")
        self._mark_outbox(note_id)
        return self._note_from_text(note_id, name, doc_text)

    def write(self, name: str, text: str) -> None:
        (self.notes_dir / name).write_text(text, encoding="utf-8")
        doc = _fm.parse(text)
        if doc.id:
            self._mark_outbox(doc.id)

    def trash_note(self, name: str) -> bool:
        src = self.notes_dir / name
        try:
            text = src.read_text(encoding="utf-8")
        except OSError:
            return False
        doc = _fm.parse(text)
        now = _utc_now_iso()

        def _stamp(keys: _fm.OwnedKeys) -> None:
            keys.trashed_at = now

        stamped = doc.rewritten(_stamp)
        dest = self.notes_dir / ".xxnote" / "trash" / name
        try:
            dest.write_text(stamped, encoding="utf-8")
            src.unlink()
        except OSError:
            return False
        if doc.id:
            self._mark_outbox(doc.id)
        return True

    def restore(self, name: str) -> bool:
        src = self.notes_dir / ".xxnote" / "trash" / name
        try:
            text = src.read_text(encoding="utf-8")
        except OSError:
            return False
        doc = _fm.parse(text)

        def _unstamp(keys: _fm.OwnedKeys) -> None:
            keys.trashed_at = None

        restored = doc.rewritten(_unstamp)
        try:
            (self.notes_dir / name).write_text(restored, encoding="utf-8")
            src.unlink()
        except OSError:
            return False
        return True

    def expire_trash(self, now_ms: int | None = None) -> int:
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        removed = 0
        for note in self.scan_trash():
            stamp_ms = _parse_iso_ms(note.trashed_at)
            if stamp_ms is None:
                continue
            if stamp_ms + TRASH_KEEP_MS <= now_ms:
                try:
                    (self.notes_dir / ".xxnote" / "trash" / note.path).unlink()
                    removed += 1
                except OSError:
                    continue
        return removed

    def search(self, query: str) -> list[Note]:
        terms = [t.strip('"* ') for t in _sq.sanitize(query).split() if t.strip('"* ')]
        notes = [n for n in self.scan() if not n.archived]
        if not terms:
            return []
        scored: list[tuple[int, Note]] = []
        for note in notes:
            hay = f"{note.title}\n{note.body}".casefold()
            hits = sum(hay.count(t.casefold()) for t in terms if t)
            if hits:
                scored.append((hits, note))
        scored.sort(key=lambda pair: -pair[0])
        return [note for _, note in scored]

    def base_of(self, note_id: str) -> str | None:
        return self._index.get("bases", {}).get(note_id)

    def set_base(self, note_id: str, body: str | None, etag: str | None = None) -> None:
        self._load_index()
        bases = self._index.setdefault("bases", {})
        etags = self._index.setdefault("etags", {})
        if body is None:
            bases.pop(note_id, None)
            etags.pop(note_id, None)
        else:
            bases[note_id] = body
            if etag is not None:
                etags[note_id] = etag
        self._save_index()

    def etag_of(self, note_id: str) -> str | None:
        return self._index.get("etags", {}).get(note_id)

    def _mark_outbox(self, note_id: str) -> None:
        self._load_index()
        outbox = self._index.setdefault("outbox", [])
        if note_id not in outbox:
            outbox.append(note_id)
        self._save_index()

    def outbox(self) -> list[str]:
        return list(self._index.get("outbox", []))

    def clear_outbox(self, note_id: str) -> None:
        self._load_index()
        outbox = self._index.get("outbox", [])
        if note_id in outbox:
            outbox.remove(note_id)
            self._save_index()

    def _note_from_text(self, note_id: str, name: str, text: str) -> Note:
        doc = _fm.parse(text)
        return Note(
            id=note_id,
            path=name,
            title=_derive_title(doc),
            body=doc.body_text,
            pinned=bool(doc.pinned),
            archived=bool(doc.archived),
            color=doc.color,
            labels=list(doc.labels),
            note_type=doc.note_type,
            updated=doc.updated,
            created=doc.created,
        )


def _quote_if_needed(value: str) -> str:
    if not value or value != value.strip() or ": " in value or value.endswith(":"):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if value[0] in "[]{}'\"#&*!|>%@`-?:":
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return value


def _parse_iso_ms(value: str | None) -> int | None:
    if not value:
        return None
    from datetime import datetime, timezone

    try:
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return int(moment.timestamp() * 1000)
    except ValueError:
        return None
