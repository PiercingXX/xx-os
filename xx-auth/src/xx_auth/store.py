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
from dataclasses import dataclass
from pathlib import Path

from xx_auth import scrypt_kdf
from xx_auth.aegis import StoredAccount


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "xx-auth"


VAULT_FORMAT = 1
KEY_SOURCE_PASSWORD = "password"
KEY_SOURCE_KEYRING = "keyring"


@dataclass
class VaultHeader:
    """Everything needed to derive the vault key, stored in the vault file
    itself so a key change commits in ONE file write. The settings copy of
    this material is only a cache: when it disagrees with the header, the
    header wins. Carries no key, no password and no 2FA secret — only the
    KDF salt/params, the key-source tag and an encrypted check value."""

    key_source: str = KEY_SOURCE_KEYRING
    kdf_n: int = scrypt_kdf.AEGIS_N
    kdf_r: int = scrypt_kdf.AEGIS_R
    kdf_p: int = scrypt_kdf.AEGIS_P
    kdf_salt_hex: str = ""
    check_hex: str = ""


def header_to_dict(header: VaultHeader) -> dict:
    kdf = None
    if header.key_source == KEY_SOURCE_PASSWORD:
        kdf = {"n": header.kdf_n, "r": header.kdf_r, "p": header.kdf_p,
               "salt": header.kdf_salt_hex}
    return {"key_source": header.key_source, "kdf": kdf, "check": header.check_hex}


def header_from_dict(raw: object) -> VaultHeader | None:
    if not isinstance(raw, dict):
        return None
    source = raw.get("key_source")
    check = raw.get("check")
    if source not in (KEY_SOURCE_PASSWORD, KEY_SOURCE_KEYRING):
        return None
    if not isinstance(check, str) or not check:
        return None
    try:
        bytes.fromhex(check)
    except ValueError:
        return None
    header = VaultHeader(key_source=source, check_hex=check)
    kdf = raw.get("kdf")
    if source == KEY_SOURCE_PASSWORD:
        if not isinstance(kdf, dict):
            return None
        try:
            header.kdf_n, header.kdf_r, header.kdf_p = (
                int(kdf["n"]), int(kdf["r"]), int(kdf["p"]))
            header.kdf_salt_hex = str(kdf["salt"])
            bytes.fromhex(header.kdf_salt_hex)
        except (KeyError, TypeError, ValueError):
            return None
    return header


def _write_tmp(tmp: Path, data: bytes) -> None:
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    os.chmod(tmp, 0o600)


def _fsync_dir(directory: Path) -> None:
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, data: bytes) -> None:
    """Durable single-commit file write: tmp file + fsync + atomic rename
    + fsync of the directory. A crash lands on either the old or the new
    file, never a mix. Small enough to share with the settings cache."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    tmp = path.with_suffix(".tmp")
    _write_tmp(tmp, data)
    os.replace(tmp, path)
    _fsync_dir(path.parent)


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
        self.header: VaultHeader | None = None

    @property
    def path(self) -> Path:
        return self._path

    def _ensure_dirs(self) -> None:
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self._path.parent, 0o700)
        except OSError:
            pass

    def load(self) -> VaultStore:
        self._ensure_dirs()
        self._rows = {}
        self.header = None
        if self._path.is_file():
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except ValueError:
                raw = []
            rows: object = raw
            if isinstance(raw, dict):
                self.header = header_from_dict(raw.get("header"))
                rows = raw.get("rows", [])
            for row in rows if isinstance(rows, list) else []:
                try:
                    account = _account(row)
                except (KeyError, ValueError, TypeError):
                    continue
                self._rows.setdefault(account.id, account)
        self._loaded = True
        return self

    def save(self) -> None:
        rows = [_row(a) for a in self.get_all()]
        if self.header is None:
            payload = json.dumps(rows)
        else:
            payload = json.dumps({"xx_auth_vault": VAULT_FORMAT,
                                  "header": header_to_dict(self.header),
                                  "rows": rows})
        atomic_write(self._path, payload.encode("utf-8"))

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
