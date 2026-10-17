"""Aegis vault import/export. Port of Android `setup/VaultExporter.kt` and
`setup/VaultImporter.kt`, written from the public Aegis vault document; no
Aegis source is copied. AES-256-GCM goes through the `cryptography` distro
package; the KDF is `hashlib.scrypt` via `scrypt_kdf`."""

from __future__ import annotations

import base64
import json
import secrets
import uuid
from dataclasses import dataclass, field

from xx_auth import base32, scrypt_kdf
from xx_auth.otp_uri import MAX_PERIOD, OtpAccount

VAULT_VERSION = 1
DB_VERSION = 2
SLOT_PASSWORD = 1
_NONCE_LEN = 12
_TAG_LEN = 16
_KEY_LEN = 32
MAX_IMPORT_BYTES = 4 * 1024 * 1024


def _aesgcm():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM


def gcm_encrypt(key: bytes, nonce: bytes, plaintext: bytes) -> tuple[bytes, bytes]:
    sealed = _aesgcm()(key).encrypt(nonce, plaintext, None)
    return sealed[:-_TAG_LEN], sealed[-_TAG_LEN:]


def gcm_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes) -> bytes:
    try:
        return _aesgcm()(key).decrypt(nonce, ciphertext + tag, None)
    except Exception as exc:
        raise ValueError("vault db failed authentication: file is corrupt") from exc


def _hex(raw: bytes) -> str:
    return raw.hex()


def _unhex(text: str) -> bytes:
    try:
        return bytes.fromhex(text)
    except ValueError as exc:
        raise ValueError("encrypted vault field is not hex") from exc


@dataclass
class StoredAccount:
    id: str
    issuer: str
    label: str
    algorithm: str
    digits: int
    period: int
    counter: int | None
    secret: bytes
    created_at: int
    order: int
    group: str = ""

    @property
    def is_hotp(self) -> bool:
        return self.counter is not None

    def __str__(self) -> str:
        return (
            f"StoredAccount(id={self.id}, issuer={self.issuer}, label={self.label}, "
            f"algorithm={self.algorithm}, digits={self.digits}, period={self.period}, "
            f"counter={self.counter}, secret=[redacted], "
            f"createdAt={self.created_at}, order={self.order})"
        )


@dataclass
class ParsedVault:
    accounts: list[StoredAccount] = field(default_factory=list)
    skipped: int = 0


def _entry(account: StoredAccount) -> dict:
    info: dict = {
        "secret": base32.encode(account.secret),
        "algo": account.algorithm,
        "digits": account.digits,
    }
    if account.counter is None:
        info["period"] = account.period
    else:
        info["counter"] = account.counter
    return {
        "type": "hotp" if account.counter is not None else "totp",
        "uuid": account.id,
        "name": account.label,
        "issuer": account.issuer,
        "note": "",
        "favorite": False,
        "icon": None,
        "info": info,
    }


def db_json(accounts: list[StoredAccount]) -> dict:
    ordered = sorted(accounts, key=lambda account: account.order)
    groups = [{"uuid": str(uuid.uuid4()), "name": name} for name in sorted({a.group for a in ordered if a.group})]
    return {"version": DB_VERSION, "entries": [_entry(a) for a in ordered], "groups": groups}


def plain_file(db: dict) -> str:
    return json.dumps({"version": VAULT_VERSION, "header": {"slots": None, "params": None}, "db": db})


def encrypted_file(db: dict, password: str) -> str:
    master_key = secrets.token_bytes(_KEY_LEN)
    db_nonce = secrets.token_bytes(_NONCE_LEN)
    db_ct, db_tag = gcm_encrypt(master_key, db_nonce, json.dumps(db).encode("utf-8"))
    salt = secrets.token_bytes(scrypt_kdf.AEGIS_SALT_LEN)
    slot_key = scrypt_kdf.derive(
        password.encode("utf-8"), salt,
        scrypt_kdf.AEGIS_N, scrypt_kdf.AEGIS_R, scrypt_kdf.AEGIS_P, _KEY_LEN,
    )
    slot_nonce = secrets.token_bytes(_NONCE_LEN)
    wrapped, wrapped_tag = gcm_encrypt(slot_key, slot_nonce, master_key)
    slot = {
        "type": SLOT_PASSWORD,
        "uuid": str(uuid.uuid4()),
        "key": _hex(wrapped),
        "key_params": {"nonce": _hex(slot_nonce), "tag": _hex(wrapped_tag)},
        "n": scrypt_kdf.AEGIS_N,
        "r": scrypt_kdf.AEGIS_R,
        "p": scrypt_kdf.AEGIS_P,
        "salt": _hex(salt),
        "repaired": True,
        "is_backup": False,
    }
    return json.dumps({
        "version": VAULT_VERSION,
        "header": {"slots": [slot], "params": {"nonce": _hex(db_nonce), "tag": _hex(db_tag)}},
        "db": base64.b64encode(db_ct).decode("ascii"),
    })


def export_vault(accounts: list[StoredAccount], password: str | None) -> str:
    db = db_json(accounts)
    if not password:
        return plain_file(db)
    return encrypted_file(db, password)


def is_encrypted(text: str) -> bool:
    try:
        root = json.loads(text)
    except ValueError:
        return False
    return isinstance(root, dict) and isinstance(root.get("db"), str)


def _parse_entry(entry: dict) -> StoredAccount | None:
    if not isinstance(entry, dict):
        raise ValueError("entry is not an object")
    kind = str(entry.get("type") or "").lower()
    if kind not in ("totp", "hotp"):
        return None
    info = entry.get("info")
    if not isinstance(info, dict):
        raise ValueError("entry missing info")
    secret_text = info.get("secret") or ""
    secret = base32.decode_or_null(secret_text) if isinstance(secret_text, str) else None
    if not secret:
        raise ValueError("entry secret is empty")
    algorithm = str(info.get("algo") or "SHA1").upper()
    if algorithm not in ("SHA1", "SHA256", "SHA512"):
        raise ValueError(f"unsupported algorithm {algorithm}")
    digits = info.get("digits", 6)
    if not isinstance(digits, int) or digits not in (6, 7, 8):
        raise ValueError(f"unsupported digit count {digits}")
    if kind == "hotp":
        period = 30
        counter = info.get("counter", 0)
        if not isinstance(counter, int):
            raise ValueError("entry counter is not a number")
    else:
        period = info.get("period", 30)
        if not isinstance(period, int):
            raise ValueError("entry period is not a number")
        counter = None
    if period not in range(1, MAX_PERIOD + 1):
        raise ValueError(f"unsupported period {period}")
    entry_uuid = entry.get("uuid") or ""
    return StoredAccount(
        str(entry_uuid) or str(uuid.uuid4()),
        str(entry.get("issuer") or ""), str(entry.get("name") or ""),
        algorithm, digits, period, counter, secret, 0, 0,
    )


def _open_db(text: str, password: str | None) -> dict:
    try:
        root = json.loads(text)
    except ValueError as exc:
        raise ValueError("not an Aegis vault: malformed JSON") from exc
    if not isinstance(root, dict):
        raise ValueError("not an Aegis vault")
    version = root.get("version")
    if not isinstance(version, int):
        raise ValueError("not an Aegis vault: no version")
    if version not in range(1, VAULT_VERSION + 1):
        raise ValueError(f"unsupported Aegis vault version {version}")
    if "header" not in root:
        raise ValueError("not an Aegis vault: no header")
    db = root.get("db")
    if isinstance(db, dict):
        return db
    if isinstance(db, str):
        return _decrypt_db(root["header"], db, password)
    raise ValueError("not an Aegis vault: no db")


def _decrypt_db(header: object, db_base64: str, password: str | None) -> dict:
    if not password:
        raise ValueError("this vault file is encrypted: a password is required")
    if not isinstance(header, dict):
        raise ValueError("encrypted vault has no slots")
    slots = header.get("slots")
    params = header.get("params")
    if not isinstance(slots, list):
        raise ValueError("encrypted vault has no slots")
    if not isinstance(params, dict):
        raise ValueError("encrypted vault has no params")
    try:
        db_nonce = _unhex(str(params["nonce"]))
        db_tag = _unhex(str(params["tag"]))
    except KeyError as exc:
        raise ValueError("encrypted vault has no params") from exc
    try:
        ciphertext = base64.b64decode(db_base64)
    except Exception as exc:
        raise ValueError("encrypted vault db is not Base64") from exc
    password_slots = [s for s in slots if isinstance(s, dict) and s.get("type") == SLOT_PASSWORD]
    if not password_slots:
        raise ValueError("encrypted vault has no password slot")
    password_bytes = password.encode("utf-8")
    for slot in password_slots:
        assert isinstance(slot, dict)
        n = slot.get("n", scrypt_kdf.AEGIS_N)
        r = slot.get("r", scrypt_kdf.AEGIS_R)
        p = slot.get("p", scrypt_kdf.AEGIS_P)
        try:
            salt = _unhex(str(slot["salt"]))
            key_params = slot["key_params"]
            assert isinstance(key_params, dict)
            slot_nonce = _unhex(str(key_params["nonce"]))
            slot_tag = _unhex(str(key_params["tag"]))
            wrapped = _unhex(str(slot["key"]))
        except (KeyError, AssertionError) as exc:
            raise ValueError("password slot is malformed") from exc
        slot_key = scrypt_kdf.derive(password_bytes, salt, n, r, p, _KEY_LEN)
        try:
            master_key = _aesgcm()(slot_key).decrypt(slot_nonce, wrapped + slot_tag, None)
        except Exception:
            continue
        plain = gcm_decrypt(master_key, db_nonce, ciphertext, db_tag)
        try:
            db = json.loads(plain.decode("utf-8"))
        except ValueError as exc:
            raise ValueError("decrypted vault db is not JSON") from exc
        if not isinstance(db, dict):
            raise ValueError("decrypted vault db is not JSON")
        return db
    raise ValueError("wrong password")


def _parse_uri_list(text: str) -> ParsedVault:
    from xx_auth import migration_uri
    from xx_auth.otp_uri import parse as parse_otp

    parsed = ParsedVault()
    saw_uri = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.lower().startswith("otpauth://"):
            saw_uri = True
            try:
                otps = [parse_otp(stripped)]
            except ValueError:
                parsed.skipped += 1
                continue
        elif migration_uri.is_migration(stripped):
            saw_uri = True
            try:
                batch = migration_uri.parse(stripped)
            except ValueError:
                parsed.skipped += 1
                continue
            parsed.skipped += batch.skipped
            otps = batch.accounts
        else:
            continue
        for otp in otps:
            parsed.accounts.append(_account_from_otp(otp, len(parsed.accounts)))
    if not saw_uri:
        raise ValueError("not an Aegis vault or a list of otpauth:// URIs")
    return parsed


def _account_from_otp(otp: OtpAccount, order: int) -> StoredAccount:
    import time

    return StoredAccount(
        str(uuid.uuid4()), otp.issuer or "", otp.label, otp.algorithm,
        otp.digits, otp.period, otp.counter, otp.secret,
        int(time.time() * 1000), order,
    )


def parse_vault(text: str, password: str | None) -> ParsedVault:
    trimmed = text.strip()
    if not trimmed:
        raise ValueError("the file is empty")
    if not trimmed.startswith("{"):
        return _parse_uri_list(trimmed)
    db = _open_db(trimmed, password)
    entries = db.get("entries")
    if not isinstance(entries, list):
        raise ValueError("not an Aegis vault: no entries")
    parsed = ParsedVault()
    for entry in entries:
        try:
            account = _parse_entry(entry)
        except ValueError:
            account = None
            parsed.skipped += 1
        else:
            if account is None:
                parsed.skipped += 1
                continue
            account.order = len(parsed.accounts)
            parsed.accounts.append(account)
    return parsed


def parse_file(raw: bytes, password: str | None) -> ParsedVault:
    if len(raw) > MAX_IMPORT_BYTES:
        raise ValueError("that file is too large to be a vault file")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("not an Aegis vault or a list of otpauth:// URIs") from exc
    parsed = parse_vault(text, password)
    if not parsed.accounts and not parsed.skipped:
        raise ValueError("the file holds no TOTP or HOTP accounts")
    return parsed
