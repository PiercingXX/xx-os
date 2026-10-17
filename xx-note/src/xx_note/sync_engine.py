"""Sync reconciliation between the local folder and WebDAV.

Applies :mod:`xx_note.sync_policy` verdicts per note: pull/push/merge/
fork/trash/resurrect/nothing, with three-way merge (prose via diff3,
checklists item-wise) and Synology-style fork names. Runs on a worker
thread; the UI posts results back with ``GLib.idle_add``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from xx_note import checklist as _checklist
from xx_note import conflict_namer as _namer
from xx_note import diff3 as _diff3
from xx_note import etag as _etag
from xx_note import frontmatter as _fm
from xx_note import store as _store
from xx_note import sync_policy as _policy
from xx_note import webdav as _webdav


@dataclass
class SyncResult:
    pushed: int = 0
    pulled: int = 0
    merged: int = 0
    forked: int = 0
    trashed: int = 0
    resurrected: int = 0
    errors: list[str] = field(default_factory=list)
    last_sync: str = ""


def _device_name() -> str:
    try:
        from pathlib import Path

        name = Path("/etc/hostname").read_text(encoding="utf-8").strip()
        return name or "linux-phone"
    except OSError:
        return "linux-phone"


def _merge_text(base: str, local: str, remote: str) -> str | None:
    base_doc = _fm.parse(base)
    local_doc = _fm.parse(local)
    remote_doc = _fm.parse(remote)
    if (
        base_doc.has_frontmatter
        and local_doc.has_frontmatter
        and remote_doc.has_frontmatter
        and base_doc.note_type == "checklist"
        and local_doc.note_type == "checklist"
        and remote_doc.note_type == "checklist"
    ):
        merged_body = _checklist.merge_bodies(
            base_doc.body_text, local_doc.body_text, remote_doc.body_text
        )
        if merged_body is None:
            return None
        head = local[: len(local) - len(local_doc.body_text)]
        return head + merged_body
    result = _diff3.merge(base.split("\n"), local.split("\n"), remote.split("\n"))
    if result[0] != "merged":
        return None
    return "\n".join(result[1])


def reconcile(
    vault: _store.Vault,
    client: _webdav.WebDavClient,
    remote_dir: str,
    device_name: str | None = None,
) -> SyncResult:
    """One full push/pull pass. Blocking; call from a worker thread."""
    result = SyncResult()
    namer = _namer.ConflictNamer(device_name or _device_name())
    try:
        entries = {e.href.rsplit("/", 1)[-1]: e for e in client.list(remote_dir)}
    except _webdav.WebDavError as exc:
        result.errors.append(str(exc))
        return result
    local_by_id: dict[str, _store.Note] = {}
    local_text: dict[str, str] = {}
    for note in vault.scan():
        local_by_id[note.id] = note
        text = vault.read(note.path)
        if text is not None:
            local_text[note.id] = text
    remote_ids: set[str] = set()
    for href, entry in entries.items():
        if not href.endswith(".md"):
            continue
        name = href
        try:
            text, etag = client.read(f"{remote_dir}/{name}".strip("/"))
        except _webdav.WebDavError as exc:
            result.errors.append(f"{name}: {exc}")
            continue
        doc = _fm.parse(text)
        note_id = doc.id
        if not note_id:
            continue
        remote_ids.add(note_id)
        base = vault.base_of(note_id)
        local_state = _policy.NoteState(
            body=local_text.get(note_id),
            trashed=False,
        )
        remote_state = _policy.NoteState(body=text, trashed=False)
        base_snap = _policy.BaseSnapshot(base, vault.etag_of(note_id)) if base else None
        verdict = _policy.decide(base_snap, local_state, remote_state)
        _apply(
            vault, client, remote_dir, namer, result,
            verdict, note_id, name, local_text.get(note_id), text, etag,
        )
    for note_id, text in local_text.items():
        if note_id in remote_ids:
            continue
        base = vault.base_of(note_id)
        base_snap = _policy.BaseSnapshot(base, vault.etag_of(note_id)) if base else None
        verdict = _policy.decide(base_snap, _policy.NoteState(text), _policy.NoteState(None))
        if verdict == _policy.PUSH:
            note = local_by_id[note_id]
            try:
                new_etag = client.write(
                    f"{remote_dir}/{note.path}".strip("/"), text,
                    vault.etag_of(note_id) if _etag.is_strong(vault.etag_of(note_id)) else None,
                )
                vault.set_base(note_id, text, new_etag or None)
                vault.clear_outbox(note_id)
                result.pushed += 1
            except _webdav.WebDavError as exc:
                result.errors.append(f"{note.path}: {exc}")
    result.last_sync = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return result


def _apply(
    vault: _store.Vault,
    client: _webdav.WebDavClient,
    remote_dir: str,
    namer: _namer.ConflictNamer,
    result: SyncResult,
    verdict: str,
    note_id: str,
    remote_name: str,
    local_text: str | None,
    remote_text: str,
    remote_etag: str,
) -> None:
    remote_path = f"{remote_dir}/{remote_name}".strip("/")
    if verdict == _policy.NOTHING:
        if local_text is not None:
            vault.set_base(note_id, local_text, remote_etag or None)
            vault.clear_outbox(note_id)
    elif verdict == _policy.PULL:
        name = remote_name
        vault.ensure()
        (vault.notes_dir / name).write_text(remote_text, encoding="utf-8")
        vault.set_base(note_id, remote_text, remote_etag or None)
        vault.clear_outbox(note_id)
        result.pulled += 1
    elif verdict == _policy.PUSH and local_text is not None:
        name = next(
            (n.path for n in vault.scan() if n.id == note_id), remote_name
        )
        try:
            new_etag = client.write(
                f"{remote_dir}/{name}".strip("/"), local_text,
                vault.etag_of(note_id) if _etag.is_strong(vault.etag_of(note_id)) else None,
            )
            vault.set_base(note_id, local_text, new_etag or None)
            vault.clear_outbox(note_id)
            result.pushed += 1
        except _webdav.WebDavError as exc:
            result.errors.append(f"{name}: {exc}")
    elif verdict == _policy.MERGE and local_text is not None:
        base = vault.base_of(note_id) or ""
        merged = _merge_text(base, local_text, remote_text)
        if merged is None:
            fork = namer.fork_name(remote_name)
            try:
                client.write(f"{remote_dir}/{fork}".strip("/"), local_text)
                (vault.notes_dir / fork).write_text(local_text, encoding="utf-8")
                fresh_id = _fm.parse(local_text).id or note_id
                vault.set_base(fresh_id, local_text)
                result.forked += 1
            except (OSError, _webdav.WebDavError) as exc:
                result.errors.append(f"{remote_name}: {exc}")
            return
        vault.ensure()
        name = next((n.path for n in vault.scan() if n.id == note_id), remote_name)
        (vault.notes_dir / name).write_text(merged, encoding="utf-8")
        try:
            new_etag = client.write(f"{remote_dir}/{name}".strip("/"), merged, remote_etag or None)
            vault.set_base(note_id, merged, new_etag or None)
            vault.clear_outbox(note_id)
            result.merged += 1
        except _webdav.WebDavError as exc:
            result.errors.append(f"{name}: {exc}")
    elif verdict == _policy.FORK and local_text is not None:
        fork = namer.fork_name(remote_name)
        try:
            client.write(f"{remote_dir}/{fork}".strip("/"), local_text)
            (vault.notes_dir / fork).write_text(local_text, encoding="utf-8")
            result.forked += 1
        except (OSError, _webdav.WebDavError) as exc:
            result.errors.append(f"{remote_name}: {exc}")
    elif verdict == _policy.TRASH:
        vault.trash_note(
            next((n.path for n in vault.scan() if n.id == note_id), remote_name)
        )
        result.trashed += 1
    elif verdict == _policy.RESURRECT and local_text is not None:
        try:
            new_etag = client.write(remote_path, local_text)
            vault.set_base(note_id, local_text, new_etag or None)
            result.resurrected += 1
        except _webdav.WebDavError as exc:
            result.errors.append(f"{remote_name}: {exc}")
