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
from xx_auth.aegis import StoredAccount, export_vault, parse_file, parse_vault
from xx_auth.keystore import SecretCipher
from xx_auth.otp import hotp_generate, remaining_millis, totp_generate
from xx_auth.otp_uri import MAX_PERIOD, OtpAccount
from xx_auth.otp_uri import parse as parse_otp
from xx_auth.otp_uri import serialize as serialize_otp
from xx_auth.qr import account_payload, otpauth_payload
from xx_auth.store import VaultStore, now_millis

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
