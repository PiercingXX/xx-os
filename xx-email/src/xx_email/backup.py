"""Versioned JSON backup for XX Email (stdlib only, no GTK).

Mirrors the shell's backup.py exclusions: settings, snooze wakes,
folder pagination cursors, and outbox *metadata* travel; tokens,
mail bodies, payload bytes, and label/thread caches never leave the
device (like Android's allowBackup=false for credentials).

Restore validates the whole payload first and never writes on
invalid input. Outbox rows restore only when their payload file is
still beside the database — a queued send without bytes cannot run,
so those entries are reported skipped, not resurrected broken.

Threading: call export/import on a worker thread (sqlite + files).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from xx_email import config as config_mod
from xx_email import db as db_mod
from xx_email.domain import OutboxFiles, SafePaths

BACKUP_SCHEMA_VERSION = 1
BACKUP_KIND = "xx-email-backup"


class BackupError(ValueError):
    """The payload failed validation; nothing was written."""


def _wake_to_dict(wake: db_mod.SnoozeWake) -> dict:
    return {"account": wake.account_email, "thread": wake.thread_id,
            "target_at": int(wake.target_at),
            "created_at": int(wake.created_at or 0)}


def _page_to_dict(page: db_mod.FolderPage) -> dict:
    return {"account": page.account_email, "folder": page.folder_key,
            "next_page_token": page.next_page_token}


def _payload_name(path: str | None) -> str | None:
    """Export-safe payload reference: only outbox/{id}.eml travels.

    The file name (never the bytes) is metadata; anything else shaped
    is dropped so a crafted backup cannot pull arbitrary paths on
    restore.
    """
    import re

    if not isinstance(path, str):
        return None
    if re.fullmatch(r"outbox/\d+\.eml", path) is None:
        return None
    return path


def _outbox_to_dict(row: db_mod.OutboxEntry) -> dict:
    return {"account": row.account_email, "kind": row.kind,
            "thread": row.thread_id, "subject": row.subject or "",
            "size": int(row.size or 0), "target_at": int(row.target_at),
            "state": row.state, "attempts": int(row.attempts or 0),
            "created_at": int(row.created_at or 0),
            "payload": _payload_name(row.path)}


def export_backup(conn, config=None) -> dict:
    """Snapshot settings + wakes + cursors + outbox metadata."""
    stored = config if config is not None else config_mod.load()
    return {
        "kind": BACKUP_KIND,
        "version": BACKUP_SCHEMA_VERSION,
        "exported_at": int(time.time() * 1000),
        "settings": config_mod.to_dict(stored),
        "snooze_wakes": [_wake_to_dict(wake)
                         for wake in db_mod.all_snooze_wakes(conn)],
        "folder_pages": [_page_to_dict(page)
                         for page in db_mod.all_folder_pages(conn)],
        "outbox": [_outbox_to_dict(row)
                   for account in db_mod.all_accounts(conn)
                   for row in db_mod.outbox_for_account(
                       conn, account.email)],
    }


def _require_mapping(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise BackupError("Backup is not a JSON object.")
    return payload


def validate_backup(payload: object) -> dict:
    """Check the whole payload; return it typed or raise BackupError."""
    data = _require_mapping(payload)
    if data.get("kind") != BACKUP_KIND:
        raise BackupError("Not an email backup.")
    if data.get("version") != BACKUP_SCHEMA_VERSION:
        raise BackupError(
            f"Unsupported backup version {data.get('version')!r}.")
    settings = data.get("settings")
    if not isinstance(settings, dict):
        raise BackupError("Backup has no settings object.")
    wakes = data.get("snooze_wakes", [])
    pages = data.get("folder_pages", [])
    outbox = data.get("outbox", [])
    if not isinstance(wakes, list) or not isinstance(pages, list) \
            or not isinstance(outbox, list):
        raise BackupError("Backup lists are malformed.")
    for wake in wakes:
        if not isinstance(wake, dict) or not wake.get("account") \
                or not wake.get("thread") \
                or not isinstance(wake.get("target_at"), int):
            raise BackupError("A snooze wake entry is malformed.")
    for page in pages:
        if not isinstance(page, dict) or not page.get("account") \
                or not page.get("folder"):
            raise BackupError("A folder cursor entry is malformed.")
    for row in outbox:
        if not isinstance(row, dict) or not row.get("account") \
                or row.get("kind") not in ("SEND", "SCHEDULED_SEND") \
                or not isinstance(row.get("target_at"), int):
            raise BackupError("An outbox entry is malformed.")
    return data


def restore_backup(conn, payload: object, *,
                   data_dir: Path | None = None,
                   config_path: Path | None = None) -> dict:
    """Validate, then write settings + wakes + cursors (+ payload-backed
    outbox rows). Returns {"restored": {...counts}, "skipped": [...]}.
    Outbox rows whose payload file is gone are skipped by name.
    """
    data = _require_mapping(payload)
    if data.get("kind") != BACKUP_KIND:
        raise BackupError("Not an email backup.")
    if data.get("version") != BACKUP_SCHEMA_VERSION:
        raise BackupError(
            f"Unsupported backup version {data.get('version')!r}.")
    settings = data.get("settings")
    wakes = data.get("snooze_wakes", [])
    pages = data.get("folder_pages", [])
    outbox = data.get("outbox", [])
    if not isinstance(settings, dict):
        raise BackupError("Backup has no settings object.")
    if not isinstance(wakes, list) or not isinstance(pages, list) \
            or not isinstance(outbox, list):
        raise BackupError("Backup lists are malformed.")
    parsed_wakes = []
    for wake in wakes:
        if not isinstance(wake, dict) or not wake.get("account") \
                or not wake.get("thread") \
                or not isinstance(wake.get("target_at"), int):
            raise BackupError("A snooze wake entry is malformed.")
        parsed_wakes.append(db_mod.SnoozeWake(
            account_email=str(wake["account"]),
            thread_id=str(wake["thread"]),
            target_at=int(wake["target_at"]),
            created_at=int(wake.get("created_at") or 0)))
    parsed_pages = []
    for page in pages:
        if not isinstance(page, dict) or not page.get("account") \
                or not page.get("folder"):
            raise BackupError("A folder cursor entry is malformed.")
        token = page.get("next_page_token")
        parsed_pages.append(db_mod.FolderPage(
            account_email=str(page["account"]),
            folder_key=str(page["folder"]),
            next_page_token=str(token) if token is not None else None))
    parsed_outbox = []
    for row in outbox:
        if not isinstance(row, dict) or not row.get("account") \
                or row.get("kind") not in ("SEND", "SCHEDULED_SEND") \
                or not isinstance(row.get("target_at"), int):
            raise BackupError("An outbox entry is malformed.")
        parsed_outbox.append(row)
    existing = {account.email for account in db_mod.all_accounts(conn)}
    skipped = [f"snooze wake for unknown account {w.account_email}"
               for w in parsed_wakes if w.account_email not in existing]
    skipped += [f"outbox send for unknown account {r['account']}"
                for r in parsed_outbox if r["account"] not in existing]
    config_mod.save(config_mod.from_dict(settings), config_path)
    counts = {"wakes": 0, "pages": 0, "outbox": 0}
    for wake in parsed_wakes:
        if wake.account_email not in existing:
            continue
        db_mod.upsert_snooze_wake(conn, wake)
        counts["wakes"] += 1
    for page in parsed_pages:
        if page.account_email not in existing:
            skipped.append(
                f"cursor for unknown account {page.account_email}")
            continue
        db_mod.upsert_folder_page(conn, page)
        counts["pages"] += 1
    files_dir = Path(data_dir) if data_dir is not None \
        else config_mod.data_dir()
    for row in parsed_outbox:
        if row["account"] not in existing:
            continue
        entry_id = _requeue_with_payload(conn, files_dir, row)
        if entry_id is None:
            skipped.append(
                f"queued send {row.get('subject') or '(no subject)'}"
                " lost its payload file")
        else:
            counts["outbox"] += 1
    return {"restored": counts, "skipped": skipped}


def _requeue_with_payload(conn, files_dir: Path, row: dict) -> int | None:
    """Re-insert one outbox row, moving its payload to the new id.

    The export names the payload file (outbox/{id}.eml); only that
    exact relative name is read, so a crafted backup cannot pull
    arbitrary paths. Returns the new id, or None when the file is
    gone (nothing is written then).
    """
    name = _payload_name(row.get("payload"))
    if name is None:
        return None
    source = Path(files_dir) / name
    try:
        if not SafePaths.is_inside(Path(files_dir), source):
            return None
        payload = source.read_bytes()
    except (OSError, ValueError):
        return None
    entry_id = db_mod.insert_outbox(conn, db_mod.OutboxEntry(
        account_email=str(row["account"]), kind=str(row["kind"]),
        thread_id=row.get("thread"),
        subject=str(row.get("subject") or ""),
        size=len(payload), target_at=int(row["target_at"]),
        state="QUEUED", attempts=0, created_at=int(
            row.get("created_at") or 0)))
    try:
        OutboxFiles.write_new(files_dir, entry_id, payload)
    except (OSError, ValueError):
        try:
            db_mod.delete_outbox(conn, entry_id)
        except Exception:
            pass
        return None
    try:
        db_mod.set_outbox_payload(
            conn, entry_id, OutboxFiles.relative_path(entry_id),
            len(payload))
    except Exception:
        pass
    try:
        new_path = Path(files_dir) / OutboxFiles.relative_path(entry_id)
        if new_path.resolve() != source.resolve():
            source.unlink()
    except OSError:
        pass
    return entry_id


def write_export_file(path: Path, payload: dict) -> Path:
    """Atomically write an export (tmp file + rename)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_text(raw, encoding="utf-8")
        tmp.replace(target)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    return target


def read_export_file(path: Path) -> dict:
    """Parse an export file; BackupError on missing/invalid JSON."""
    try:
        return _require_mapping(
            json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise BackupError(f"Cannot read backup: {exc}") from exc
