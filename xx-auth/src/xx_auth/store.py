"""JSON vault store. Linux mapping of Android's Room `VaultDao`: the same
row contract (insert aborts on duplicate id, update throws when absent,
ordered reads, HOTP-only `advance_counter`, C17 reorder), persisted as one
JSON file under XDG data. First run creates every directory it uses (0700)
before touching files."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from xx_auth.aegis import StoredAccount


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "xx-auth"


def _row(account: StoredAccount) -> dict:
    return {
        "id": account.id, "issuer": account.issuer, "label": account.label,
        "algorithm": account.algorithm, "digits": account.digits,
        "period": account.period, "counter": account.counter,
        "secret": base64.b64encode(account.secret).decode("ascii"),
        "createdAt": account.created_at, "order": account.order,
        "group": account.group,
    }


def _account(row: dict) -> StoredAccount:
    return StoredAccount(
        str(row["id"]), str(row.get("issuer") or ""), str(row.get("label") or ""),
        str(row.get("algorithm") or "SHA1"), int(row.get("digits", 6)),
        int(row.get("period", 30)),
        row.get("counter") if row.get("counter") is None else int(row["counter"]),
        base64.b64decode(row["secret"]),
        int(row.get("createdAt", 0)), int(row.get("order", 0)),
        str(row.get("group") or ""),
    )


class VaultStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or data_dir() / "vault.json"
        self._rows: dict[str, StoredAccount] = {}
        self._loaded = False

    def _ensure_dirs(self) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self._path.parent, 0o700)
        except OSError:
            pass

    def load(self) -> VaultStore:
        self._ensure_dirs()
        self._rows = {}
        if self._path.is_file():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except ValueError:
                raw = []
            for row in raw if isinstance(raw, list) else []:
                try:
                    account = _account(row)
                except (KeyError, ValueError, TypeError):
                    continue
                self._rows.setdefault(account.id, account)
        self._loaded = True
        return self

    def save(self) -> None:
        self._ensure_dirs()
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps([_row(a) for a in self.get_all()]), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self._path)

    def _require_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def insert(self, account: StoredAccount) -> None:
        self._require_loaded()
        if account.id in self._rows:
            raise ValueError(f"duplicate account id: {account.id}")
        self._rows[account.id] = account

    def update(self, account: StoredAccount) -> None:
        self._require_loaded()
        if account.id not in self._rows:
            raise ValueError(f"no such account: {account.id}")
        self._rows[account.id] = account

    def delete(self, account_id: str) -> None:
        self._require_loaded()
        self._rows.pop(account_id, None)

    def delete_all(self) -> None:
        self._require_loaded()
        self._rows.clear()

    def get_by_id(self, account_id: str) -> StoredAccount | None:
        self._require_loaded()
        return self._rows.get(account_id)

    def get_all(self) -> list[StoredAccount]:
        self._require_loaded()
        return sorted(self._rows.values(), key=lambda a: a.order)

    def count(self) -> int:
        self._require_loaded()
        return len(self._rows)

    def advance_counter(self, account_id: str) -> StoredAccount | None:
        current = self.get_by_id(account_id)
        if current is None or current.counter is None:
            return None
        from dataclasses import replace

        updated = replace(current, counter=current.counter + 1)
        self.update(updated)
        return updated

    def move(self, account_id: str, target_index: int) -> list[str] | None:
        from dataclasses import replace

        ordered = self.get_all()
        current = next((i for i, a in enumerate(ordered) if a.id == account_id), -1)
        if current < 0 or target_index not in range(len(ordered)):
            return None
        moved = ordered.pop(current)
        ordered.insert(target_index, moved)
        for index, account in enumerate(ordered):
            if account.order != index:
                self.update(replace(account, order=index))
        return [a.id for a in ordered]

    def next_order(self) -> int:
        self._require_loaded()
        if not self._rows:
            return 0
        return max(a.order for a in self._rows.values()) + 1


def now_millis() -> int:
    return int(time.time() * 1000)
