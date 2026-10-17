"""Account flows. Port of the pure-JVM halves of Android
`ui/AddAccountScreen.kt`, `ui/AccountListScreen.kt`,
`ui/AccountDetailScreen.kt` and `ui/ImportExportScreen.kt`: add paths,
card model, search, transfer URIs, import/export seams and the plain-sentence
error mappings. They run against `VaultStore` + `SecretCipher` instead of
Room, with identical messages and ordering."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from xx_auth import base32, migration_uri
from xx_auth import keystore as keystore_mod
from xx_auth import prefs as prefs_mod
from xx_auth.aegis import StoredAccount, export_vault, parse_file, parse_vault
from xx_auth.keystore import SecretCipher
from xx_auth.otp import hotp_generate, remaining_millis, totp_generate
from xx_auth.otp_uri import MAX_PERIOD, OtpAccount
from xx_auth.otp_uri import parse as parse_otp
from xx_auth.otp_uri import serialize as serialize_otp
from xx_auth.prefs import Prefs
from xx_auth.qr import account_payload, otpauth_payload
from xx_auth.store import (
    KEY_SOURCE_KEYRING,
    KEY_SOURCE_PASSWORD,
    VaultHeader,
    VaultStore,
    now_millis,
)

WARN_MILLIS = 5_000


@dataclass
class AccountCard:
    id: str
    issuer: str
    label: str
    code: str
    remaining_millis: int | None
    progress_fraction: float | None
    counter: int | None = None
    period: int = 30
    unreadable: bool = False
    group: str = ""

    @property
    def is_hotp(self) -> bool:
        return self.counter is not None


def filter_cards(cards: list[AccountCard], query: str | None) -> list[AccountCard]:
    text = (query or "").strip()
    if not text:
        return list(cards)
    folded = text.casefold()
    return [c for c in cards if folded in c.issuer.casefold() or folded in c.label.casefold()]


def _card_for(store: VaultStore, account: StoredAccount, unix_millis: int, cipher: SecretCipher) -> AccountCard:
    try:
        secret = cipher.decrypt(account.secret)
        if account.counter is None:
            code = totp_generate(secret, max(1, account.period), account.digits, account.algorithm, unix_millis // 1000)
        else:
            code = hotp_generate(secret, account.counter, account.digits, account.algorithm)
    except Exception:
        code = None
    if code is None:
        digits = min(8, max(6, account.digits))
        return AccountCard(account.id, account.issuer, account.label, "-" * digits, None, None,
                           account.counter, account.period, True, account.group)
    if account.counter is None:
        period = max(1, account.period)
        remaining = remaining_millis(period, unix_millis)
        progress = remaining / (period * 1000)
        return AccountCard(account.id, account.issuer, account.label, code, remaining, progress,
                           None, account.period, False, account.group)
    return AccountCard(account.id, account.issuer, account.label, code, None, None,
                       account.counter, account.period, False, account.group)


def account_cards(store: VaultStore, unix_millis: int, cipher: SecretCipher) -> list[AccountCard]:
    return [_card_for(store, account, unix_millis, cipher) for account in store.get_all()]


def account_card(store: VaultStore, account_id: str, unix_millis: int, cipher: SecretCipher) -> AccountCard | None:
    account = store.get_by_id(account_id)
    return _card_for(store, account, unix_millis, cipher) if account else None


def reorder_account(store: VaultStore, account_id: str, target_index: int) -> list[str] | None:
    return store.move(account_id, target_index)


def _insert_parsed(store: VaultStore, parsed: OtpAccount, cipher: SecretCipher) -> StoredAccount:
    account = StoredAccount(
        str(uuid.uuid4()), parsed.issuer or "", parsed.label, parsed.algorithm,
        parsed.digits, parsed.period, parsed.counter,
        cipher.encrypt(parsed.secret), now_millis(), store.next_order(),
    )
    store.insert(account)
    return account


def add_account(store: VaultStore, text: str, cipher: SecretCipher) -> StoredAccount:
    trimmed = text.strip()
    if trimmed.lower().startswith("otpauth://"):
        return _insert_parsed(store, parse_otp(trimmed), cipher)
    raw = base32.decode_or_null(trimmed)
    if raw is None:
        raise ValueError("not an otpauth:// URI or a Base32 secret")
    if not raw:
        raise ValueError("secret is empty")
    return _insert_parsed(store, OtpAccount(
        "totp", "Secret " + base32.encode(raw)[:4], None, "SHA1", 6, 30, None, raw,
    ), cipher)


def add_manual(store: VaultStore, issuer: str, label: str, secret: str, hotp: bool,
               algorithm: str, digits: int, period: int, counter: int,
               cipher: SecretCipher) -> StoredAccount:
    raw = base32.decode_or_null(secret.strip())
    if raw is None:
        raise ValueError("secret is not valid Base32")
    if not raw:
        raise ValueError("secret is empty")
    if not issuer.strip() and not label.strip():
        raise ValueError("enter an issuer or an account name")
    algo = algorithm.upper().replace("-", "")
    if algo not in ("SHA1", "SHA256", "SHA512"):
        raise ValueError(f"unsupported algorithm {algorithm}")
    if digits not in (6, 7, 8):
        raise ValueError("digits must be 6 to 8")
    if hotp:
        if counter < 0:
            raise ValueError("counter must not be negative")
    elif period not in range(1, MAX_PERIOD + 1):
        raise ValueError(f"period must be 1..{MAX_PERIOD} seconds")
    return _insert_parsed(store, OtpAccount(
        "hotp" if hotp else "totp", label.strip(), issuer.strip() or None, algo,
        digits, 30 if hotp else period, counter if hotp else None, raw,
    ), cipher)


def add_scanned(store: VaultStore, raw: str, cipher: SecretCipher) -> StoredAccount | None:
    payload = otpauth_payload(raw)
    if payload is None:
        return None
    return add_account(store, payload, cipher)


@dataclass
class AddResult:
    accounts: list[StoredAccount] = field(default_factory=list)
    skipped: int = 0
    batch_index: int = 0
    batch_size: int = 1

    def summary(self) -> str:
        if len(self.accounts) == 1:
            first = self.accounts[0]
            base = f"Added {first.issuer or first.label}."
        else:
            noun = "account" if len(self.accounts) == 1 else "accounts"
            base = f"Added {len(self.accounts)} {noun}."
        skip = f" Skipped {self.skipped} unsupported." if self.skipped else ""
        batch = f" QR {self.batch_index + 1} of {self.batch_size}: scan the rest too." if self.batch_size > 1 else ""
        return base + skip + batch


def add_from_text(store: VaultStore, text: str, cipher: SecretCipher) -> AddResult:
    trimmed = text.strip()
    if not migration_uri.is_migration(trimmed):
        return AddResult([add_account(store, trimmed, cipher)])
    batch = migration_uri.parse(trimmed)
    if not batch.accounts:
        raise ValueError("that transfer QR holds no supported accounts")
    added = [_insert_parsed(store, otp, cipher) for otp in batch.accounts]
    return AddResult(added, batch.skipped, batch.index, batch.size)


def add_error_message(exc: BaseException) -> str:
    message = str(exc) or ""
    if "migration" in message or "transfer QR" in message:
        return "That transfer QR couldn't be read. Export it again and rescan."
    if "Base32" in message:
        return "That secret isn't valid Base32 (letters A–Z and digits 2–7)."
    if "secret is empty" in message or "no secret" in message:
        return "That code has no secret."
    if "algorithm" in message:
        return "That code uses an algorithm xx-auth doesn't support."
    if "digits" in message:
        return "Codes must be 6, 7 or 8 digits."
    if "period" in message:
        return f"The period must be between 1 and {MAX_PERIOD} seconds."
    if "counter" in message:
        return "An HOTP code needs a counter of 0 or more."
    if "issuer or an account" in message:
        return "Enter an issuer or an account name."
    if "no label or issuer" in message:
        return "That code has no account name or issuer."
    if "otpauth" in message:
        return "Not a valid otpauth:// URI or Base32 secret."
    return "Could not add that account."


def edit_account(store: VaultStore, account_id: str, issuer: str, label: str, group: str) -> StoredAccount:
    from dataclasses import replace

    current = store.get_by_id(account_id)
    if current is None:
        raise ValueError("no such account")
    if not issuer.strip() and not label.strip():
        raise ValueError("enter an issuer or an account name")
    updated = replace(current, issuer=issuer.strip(), label=label.strip(), group=group.strip())
    store.update(updated)
    return updated


def delete_account(store: VaultStore, account_id: str) -> None:
    store.delete(account_id)


def rekey_vault(store: VaultStore, old: SecretCipher, new: SecretCipher) -> int:
    """Re-encrypt every secret from ``old`` to ``new`` (an app-password
    change when the password derives the vault key). All rows decrypt
    before any is rewritten, so a bad row leaves the vault untouched.
    Returns the number of rows re-encrypted."""
    from dataclasses import replace

    rows = store.get_all()
    plain = [(row, old.decrypt(row.secret)) for row in rows]
    for row, secret in plain:
        store.update(replace(row, secret=new.encrypt(secret)))
    return len(plain)


class VaultUnreadable(ValueError):
    """The vault matches none of the available keys. The file is always
    kept as is; only the message reaches the UI."""


UNREADABLE_MESSAGE = "The vault couldn't be opened. Nothing was changed."
WRONG_PASSWORD_MESSAGE = "Wrong password."
WRONG_CURRENT_MESSAGE = "Wrong current password."
REKEY_FAILED_MESSAGE = "Couldn't re-encrypt the vault. Nothing changed."
SHORT_PASSWORD_MESSAGE = "Use at least 8 characters."
NO_KEYRING_MESSAGE = "The keyring isn't available."


@dataclass
class StartupResult:
    cipher: SecretCipher | None
    password_mode: bool


def _keyring_key(keyring) -> bytes | None:  # type: ignore[no-untyped-def]
    try:
        key = keyring.load_key()
    except Exception:
        return None
    return key if isinstance(key, bytes) and len(key) == 32 else None


def _password_key(password: str, salt_hex: str, header: VaultHeader | None = None) -> bytes | None:
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return None
    if not salt:
        return None
    if header is None:
        return keystore_mod.derive_key(password, salt)
    return keystore_mod.derive_key(password, salt, header.kdf_n, header.kdf_r, header.kdf_p)


def _rows_decrypt_all(rows: list[StoredAccount], cipher: SecretCipher) -> bool:
    try:
        for row in rows:
            cipher.decrypt(row.secret)
    except Exception:
        return False
    return True


def _password_header(password: str) -> tuple[VaultHeader, bytes]:
    salt = keystore_mod.new_salt()
    key = keystore_mod.derive_key(password, salt)
    return VaultHeader(key_source=KEY_SOURCE_PASSWORD, kdf_salt_hex=salt.hex(),
                       check_hex=keystore_mod.make_check(key)), key


def _keyring_header(key: bytes) -> VaultHeader:
    return VaultHeader(key_source=KEY_SOURCE_KEYRING,
                       check_hex=keystore_mod.make_check(key))


def _verifier_matches(password: str, prefs: Prefs) -> bool:
    import binascii

    try:
        salt = binascii.unhexlify(prefs.lock_salt)
    except (binascii.Error, ValueError):
        return False
    return bool(prefs.lock_hash) and keystore_mod.password_verifier(password, salt) == prefs.lock_hash


def _heal_prefs(prefs: Prefs, header: VaultHeader,
                password: str | None = None, prefs_path=None) -> None:  # type: ignore[no-untyped-def]
    """Refresh the settings cache from the authoritative vault header. The
    UI-gate verifier is rebuilt from the just-typed password when the
    cached copy went stale in a crash; its fast hash is never stored in
    the vault itself. A failed cache write is harmless, so it never fails
    the open."""
    changed = False
    want_disabled = header.key_source != KEY_SOURCE_KEYRING
    if prefs.keyring_disabled != want_disabled:
        prefs.keyring_disabled = want_disabled
        changed = True
    if header.key_source == KEY_SOURCE_PASSWORD:
        if prefs.lock_kdf_salt != header.kdf_salt_hex:
            prefs.lock_kdf_salt = header.kdf_salt_hex
            changed = True
        if password is not None and not _verifier_matches(password, prefs):
            fresh = keystore_mod.new_salt()
            prefs.lock_salt = fresh.hex()
            prefs.lock_hash = keystore_mod.password_verifier(password, fresh)
            changed = True
    if changed:
        try:
            prefs_mod.save(prefs, prefs_path)
        except OSError:
            pass


def open_at_startup(store: VaultStore, prefs: Prefs, keyring,  # type: ignore[no-untyped-def]
                    prefs_path=None) -> StartupResult:  # type: ignore[no-untyped-def]
    """Resolve the vault key without a password. Migrates a header-less
    vault the moment a known key proves it, and lets the header overrule a
    crash-stale settings copy. Raises VaultUnreadable (file kept) only
    when a non-empty vault matches nothing available."""
    store.load()
    header = store.header
    if header is not None:
        _heal_prefs(prefs, header, None, prefs_path)
        if header.key_source == KEY_SOURCE_KEYRING:
            key = _keyring_key(keyring)
            if key is None:
                return StartupResult(None, True)
            if not keystore_mod.verify_check(key, header.check_hex):
                raise VaultUnreadable(UNREADABLE_MESSAGE)
            return StartupResult(SecretCipher(key), False)
        return StartupResult(None, True)
    rows = store.get_all()
    if not rows:
        if prefs.keyring_disabled:
            return StartupResult(None, True)
        try:
            key = keystore_mod.load_or_create_key(keyring)
        except Exception:
            return StartupResult(None, True)
        if not isinstance(key, bytes) or len(key) != 32:
            return StartupResult(None, True)
        store.header = _keyring_header(key)
        store.save()
        _heal_prefs(prefs, store.header, None, prefs_path)
        return StartupResult(SecretCipher(key), False)
    if not prefs.keyring_disabled:
        key = _keyring_key(keyring)
        if key is not None and _rows_decrypt_all(rows, SecretCipher(key)):
            store.header = _keyring_header(key)
            store.save()
            _heal_prefs(prefs, store.header, None, prefs_path)
            return StartupResult(SecretCipher(key), False)
    return StartupResult(None, True)


def unlock_with_password(store: VaultStore, prefs: Prefs, keyring,  # type: ignore[no-untyped-def]
                         password: str, prefs_path=None) -> SecretCipher:  # type: ignore[no-untyped-def]
    """Open the vault with a typed password. Header salts and params win
    over the settings copy; a header-less vault left mismatched by the old
    code falls back to the other source before it is declared unreadable.
    A proven key migrates the header atomically. Never writes on failure."""
    store.load()
    header = store.header
    if header is not None:
        if header.key_source == KEY_SOURCE_PASSWORD:
            key = _password_key(password, header.kdf_salt_hex, header)
            if key is None or not keystore_mod.verify_check(key, header.check_hex):
                raise VaultUnreadable(WRONG_PASSWORD_MESSAGE)
            _heal_prefs(prefs, header, password, prefs_path)
            return SecretCipher(key)
        if prefs.lock_hash and not _verifier_matches(password, prefs):
            raise VaultUnreadable(WRONG_PASSWORD_MESSAGE)
        key = _keyring_key(keyring)
        if key is None or not keystore_mod.verify_check(key, header.check_hex):
            raise VaultUnreadable(UNREADABLE_MESSAGE)
        return SecretCipher(key)
    rows = store.get_all()
    if rows:
        key = _password_key(password, prefs.lock_kdf_salt) if prefs.lock_kdf_salt else None
        if key is not None and _rows_decrypt_all(rows, SecretCipher(key)):
            store.header = VaultHeader(
                key_source=KEY_SOURCE_PASSWORD, kdf_salt_hex=prefs.lock_kdf_salt,
                check_hex=keystore_mod.make_check(key))
            store.save()
            _heal_prefs(prefs, store.header, password, prefs_path)
            return SecretCipher(key)
        other = _keyring_key(keyring)
        if other is not None and _rows_decrypt_all(rows, SecretCipher(other)):
            store.header = _keyring_header(other)
            store.save()
            _heal_prefs(prefs, store.header, None, prefs_path)
            return SecretCipher(other)
        raise VaultUnreadable(WRONG_PASSWORD_MESSAGE)
    salt_hex = prefs.lock_kdf_salt
    try:
        salt = bytes.fromhex(salt_hex) if salt_hex else b""
    except ValueError:
        salt = b""
    if not salt:
        salt = keystore_mod.new_salt()
        salt_hex = salt.hex()
    key = keystore_mod.derive_key(password, salt)
    store.header = VaultHeader(key_source=KEY_SOURCE_PASSWORD, kdf_salt_hex=salt_hex,
                               check_hex=keystore_mod.make_check(key))
    store.save()
    _heal_prefs(prefs, store.header, password, prefs_path)
    return SecretCipher(key)


def setup_password(store: VaultStore, prefs: Prefs,
                   password: str, prefs_path=None) -> SecretCipher:  # type: ignore[no-untyped-def]
    """First-run password setup: one vault commit carrying the fresh header,
    then the settings cache. Refuses a non-empty vault instead of orphaning
    rows whose key is unknown."""
    if len(password) < 8:
        raise ValueError(SHORT_PASSWORD_MESSAGE)
    store.load()
    if store.get_all():
        raise VaultUnreadable(UNREADABLE_MESSAGE)
    header, key = _password_header(password)
    store.header = header
    store.save()
    auth_salt = keystore_mod.new_salt()
    prefs.lock_salt = auth_salt.hex()
    prefs.lock_hash = keystore_mod.password_verifier(password, auth_salt)
    prefs.lock_kdf_salt = header.kdf_salt_hex
    prefs_mod.save(prefs, prefs_path)
    return SecretCipher(key)


def change_password(store: VaultStore, prefs: Prefs, keyring,  # type: ignore[no-untyped-def]
                    current_password: str, new_password: str,
                    prefs_path=None) -> SecretCipher | None:  # type: ignore[no-untyped-def]
    """Change the app password. In password mode every secret is
    re-encrypted in memory and committed with the new header in ONE vault
    write; the settings save after it is only a cache. A crash lands on
    the old or the new vault, and the header tells which. In keyring mode
    the vault key is untouched and only the UI-gate verifier is saved."""
    if len(new_password) < 8:
        raise ValueError(SHORT_PASSWORD_MESSAGE)
    store.load()
    header = store.header
    source = header.key_source if header is not None else (
        KEY_SOURCE_KEYRING if not prefs.keyring_disabled else KEY_SOURCE_PASSWORD)
    if source == KEY_SOURCE_PASSWORD:
        if header is not None:
            old_key = _password_key(current_password, header.kdf_salt_hex, header)
            if old_key is None or not keystore_mod.verify_check(old_key, header.check_hex):
                raise ValueError(WRONG_CURRENT_MESSAGE)
        else:
            old_key = _password_key(current_password, prefs.lock_kdf_salt)
            if old_key is None or not _rows_decrypt_all(store.get_all(), SecretCipher(old_key)):
                raise ValueError(WRONG_CURRENT_MESSAGE)
        new_header, new_key = _password_header(new_password)
        rekey_vault(store, SecretCipher(old_key), SecretCipher(new_key))
        store.header = new_header
        store.save()
        auth_salt = keystore_mod.new_salt()
        prefs.lock_salt = auth_salt.hex()
        prefs.lock_hash = keystore_mod.password_verifier(new_password, auth_salt)
        prefs.lock_kdf_salt = new_header.kdf_salt_hex
        prefs_mod.save(prefs, prefs_path)
        return SecretCipher(new_key)
    if prefs.lock_hash and not _verifier_matches(current_password, prefs):
        raise ValueError(WRONG_CURRENT_MESSAGE)
    auth_salt = keystore_mod.new_salt()
    prefs.lock_salt = auth_salt.hex()
    prefs.lock_hash = keystore_mod.password_verifier(new_password, auth_salt)
    prefs_mod.save(prefs, prefs_path)
    key = _keyring_key(keyring)
    return SecretCipher(key) if key is not None else None


def switch_key_source(store: VaultStore, prefs: Prefs, keyring,  # type: ignore[no-untyped-def]
                      current_cipher: SecretCipher | None, password: str | None,
                      to_keyring: bool, prefs_path=None) -> SecretCipher:  # type: ignore[no-untyped-def]
    """Move the vault between the keyring key and a password-derived key.
    Same single-commit shape as a password change, so a crash keeps the
    old or the new vault and the header says which. Retrying after a crash
    heals the cache when the commit already landed."""
    store.load()
    rows = store.get_all()
    if to_keyring:
        try:
            new_key = keystore_mod.load_or_create_key(keyring)
        except Exception as exc:
            raise ValueError(NO_KEYRING_MESSAGE) from exc
        if not isinstance(new_key, bytes) or len(new_key) != 32:
            raise ValueError(NO_KEYRING_MESSAGE)
        header = store.header
        if (header is not None and header.key_source == KEY_SOURCE_KEYRING
                and keystore_mod.verify_check(new_key, header.check_hex)):
            _heal_prefs(prefs, header, None, prefs_path)
            return SecretCipher(new_key)
        if current_cipher is None or not _rows_decrypt_all(rows, current_cipher):
            raise VaultUnreadable(REKEY_FAILED_MESSAGE)
        rekey_vault(store, current_cipher, SecretCipher(new_key))
        store.header = _keyring_header(new_key)
        store.save()
        prefs.keyring_disabled = False
        prefs_mod.save(prefs, prefs_path)
        return SecretCipher(new_key)
    if password is None or len(password) < 8:
        raise ValueError(SHORT_PASSWORD_MESSAGE)
    header = store.header
    if header is not None and header.key_source == KEY_SOURCE_PASSWORD:
        trial = _password_key(password, header.kdf_salt_hex, header)
        if trial is not None and keystore_mod.verify_check(trial, header.check_hex):
            _heal_prefs(prefs, header, password, prefs_path)
            return SecretCipher(trial)
    if current_cipher is None or not _rows_decrypt_all(rows, current_cipher):
        raise VaultUnreadable(REKEY_FAILED_MESSAGE)
    new_header, new_key = _password_header(password)
    rekey_vault(store, current_cipher, SecretCipher(new_key))
    store.header = new_header
    store.save()
    auth_salt = keystore_mod.new_salt()
    prefs.lock_salt = auth_salt.hex()
    prefs.lock_hash = keystore_mod.password_verifier(password, auth_salt)
    prefs.lock_kdf_salt = new_header.kdf_salt_hex
    prefs.keyring_disabled = True
    prefs_mod.save(prefs, prefs_path)
    return SecretCipher(new_key)


def transfer_uri(store: VaultStore, account_id: str, cipher: SecretCipher) -> str | None:
    account = store.get_by_id(account_id)
    if account is None:
        return None
    return serialize_otp(OtpAccount(
        "hotp" if account.is_hotp else "totp", account.label,
        account.issuer or None, account.algorithm, account.digits,
        account.period, account.counter, cipher.decrypt(account.secret),
    ))


def reveal_secret(store: VaultStore, account_id: str, cipher: SecretCipher) -> str | None:
    account = store.get_by_id(account_id)
    if account is None:
        return None
    try:
        return base32.encode(cipher.decrypt(account.secret))
    except Exception:
        return None


def export_file_name(now: int | None = None) -> str:
    stamp = time.strftime("%Y-%m-%d", time.localtime((now if now is not None else now_millis()) / 1000))
    return f"xx-auth-{stamp}.json"


def export_secrets(store: VaultStore, cipher: SecretCipher) -> list[StoredAccount]:
    from dataclasses import replace

    out = []
    for account in store.get_all():
        out.append(replace(account, secret=cipher.decrypt(account.secret)))
    return out


def export_vault_text(store: VaultStore, password: str | None, cipher: SecretCipher) -> str:
    return export_vault(export_secrets(store, cipher), password)


def import_vault_text(store: VaultStore, text: str, password: str | None,
                      cipher: SecretCipher, merge: bool = False) -> tuple[int, int]:
    parsed = parse_vault(text, password)
    return _apply_parsed(store, parsed.accounts, parsed.skipped, cipher, merge)


def import_vault_file(store: VaultStore, raw: bytes, password: str | None,
                      cipher: SecretCipher, merge: bool = False) -> tuple[int, int]:
    parsed = parse_file(raw, password)
    return _apply_parsed(store, parsed.accounts, parsed.skipped, cipher, merge)


def _apply_parsed(store: VaultStore, accounts: list[StoredAccount], skipped: int,
                  cipher: SecretCipher, merge: bool) -> tuple[int, int]:
    from dataclasses import replace

    encrypted = [replace(a, secret=cipher.encrypt(a.secret)) for a in accounts]
    if not merge:
        store.delete_all()
        existing: set[str] = set()
        existing_ids: set[str] = set()
        order = 0
    else:
        existing = {(a.issuer.strip().casefold(), a.label.strip().casefold()) for a in store.get_all()}
        existing_ids = {a.id for a in store.get_all()}
        order = store.next_order()
    imported = 0
    missed = skipped
    for account in encrypted:
        key = (account.issuer.strip().casefold(), account.label.strip().casefold())
        if merge and key in existing:
            missed += 1
            continue
        account_id = account.id
        if account_id in existing_ids:
            account_id = str(uuid.uuid4())
        store.insert(replace(account, id=account_id, order=order if merge else account.order))
        existing.add(key)
        existing_ids.add(account_id)
        if merge:
            order += 1
        imported += 1
    return imported, missed


def import_error_message(exc: BaseException) -> str:
    message = str(exc) or ""
    if "wrong password" in message:
        return "Wrong password."
    if "encrypted" in message and "password" in message:
        return "This file is encrypted. Enter its password above and pick it again."
    if "empty" in message:
        return "That file is empty."
    if "too large" in message:
        return "That file is too large to be a vault file."
    if "no TOTP or HOTP" in message:
        return "That file holds no TOTP or HOTP accounts."
    if "corrupt" in message or "authentication" in message:
        return "That file is damaged and can't be decrypted."
    if "unsupported Aegis vault version" in message:
        return "That vault file is from a newer Aegis version xx-auth can't read."
    if "not an Aegis vault" in message or "not JSON" in message:
        return "That isn't an Aegis vault file or a list of otpauth:// links."
    if isinstance(exc, OSError):
        return "Couldn't read that file."
    return "Import failed."


def scan_payloads(payloads: list[str]) -> str | None:
    for raw in payloads:
        payload = account_payload(raw)
        if payload is not None:
            return payload
    return None
