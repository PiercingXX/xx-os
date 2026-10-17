"""Atomic key changes: one vault commit per key change, crash-safe.

A password or key-source change used to write the vault first and the
settings second; a crash between the two left rows under a key the saved
settings could not derive, losing every 2FA secret. The vault file now
carries its own header (key-source tag, KDF salt/params, key check value)
in a single atomic write, and the settings copy is only a cache.
No real keyring here: MemoryKeyring and small fakes stand in."""
import hashlib
import json
import os

import pytest

from xx_auth import accounts as accounts_mod
from xx_auth import base32, keystore as keystore_mod
from xx_auth import prefs as prefs_mod
from xx_auth.accounts import VaultUnreadable
from xx_auth.keystore import MemoryKeyring, SecretCipher
from xx_auth.store import VaultStore

OLD_PASSWORD = "old-password-9"
NEW_PASSWORD = "new-password-9"
SECRET_A = "JBSWY3DPEHPK3PXP"
SECRET_B = "MZXW6YTBOI"


class _DeadKeyring:
    def load_key(self):
        raise ValueError("no Secret Service on the bus")

    def store_key(self, key):
        raise ValueError("no Secret Service on the bus")

    def available(self):
        return False


def _paths(tmp_path):
    return tmp_path / "vault.json", tmp_path / "settings.json"


def _fresh(vault_path, prefs_path):
    return VaultStore(vault_path).load(), prefs_mod.load(prefs_path)


def _try_password(store, prefs, keyring, password, prefs_path):
    try:
        return accounts_mod.unlock_with_password(store, prefs, keyring, password, prefs_path)
    except VaultUnreadable:
        return None


def _row_secrets(store, cipher):
    return sorted(cipher.decrypt(row.secret) for row in store.get_all())


def _setup_password_vault(tmp_path, password=OLD_PASSWORD):
    vault_path, prefs_path = _paths(tmp_path)
    store, prefs = _fresh(vault_path, prefs_path)
    prefs.keyring_disabled = True
    cipher = accounts_mod.setup_password(store, prefs, password, prefs_path)
    accounts_mod.add_from_text(store, f"otpauth://totp/Example:alice?secret={SECRET_A}", cipher)
    accounts_mod.add_from_text(store, f"otpauth://totp/Example:bob?secret={SECRET_B}", cipher)
    store.save()
    return vault_path, prefs_path, cipher


def _boom_before(*_args, **_kwargs):
    raise OSError("injected crash before commit")


def _boom_rename(*_args, **_kwargs):
    raise OSError("injected crash at rename")


def _change_with_crash(monkeypatch, tmp_path, target, operation):
    """Run ``operation`` with ``target`` patched to crash; return the paths."""
    import xx_auth.store as store_mod

    vault_path, prefs_path, cipher = _setup_password_vault(tmp_path)
    store, prefs = _fresh(vault_path, prefs_path)
    if target == "tmp_write":
        monkeypatch.setattr(store_mod, "_write_tmp", operation)
    else:
        monkeypatch.setattr(os, "replace", operation)
    with pytest.raises(OSError):
        accounts_mod.change_password(store, prefs, MemoryKeyring(),
                                     OLD_PASSWORD, NEW_PASSWORD, prefs_path)
    return vault_path, prefs_path


def test_password_change_commits_header_and_opens_with_new_only(tmp_path):
    vault_path, prefs_path, _ = _setup_password_vault(tmp_path)
    store, prefs = _fresh(vault_path, prefs_path)
    cipher = accounts_mod.change_password(store, prefs, MemoryKeyring(),
                                          OLD_PASSWORD, NEW_PASSWORD, prefs_path)
    assert _row_secrets(store, cipher) == [base32.decode(SECRET_A), base32.decode(SECRET_B)]
    reopened, reprefs = _fresh(vault_path, prefs_path)
    assert reopened.header is not None and reopened.header.key_source == "password"
    assert _try_password(reopened, reprefs, MemoryKeyring(), NEW_PASSWORD, prefs_path) is not None
    reopened, reprefs = _fresh(vault_path, prefs_path)
    assert _try_password(reopened, reprefs, MemoryKeyring(), OLD_PASSWORD, prefs_path) is None


@pytest.mark.parametrize("seam", ["tmp_write", "rename"])
def test_password_change_crash_before_commit_keeps_old_key(tmp_path, monkeypatch, seam):
    """Crash in the tmp write or the rename: the old vault is intact."""
    if seam == "tmp_write":
        vault_path, prefs_path = _change_with_crash(
            monkeypatch, tmp_path, "tmp_write", _boom_before)
    else:
        vault_path, prefs_path = _change_with_crash(
            monkeypatch, tmp_path, "rename", _boom_rename)
    store, prefs = _fresh(vault_path, prefs_path)
    assert json.loads(vault_path.read_text(encoding="utf-8"))["header"]
    old_cipher = _try_password(store, prefs, MemoryKeyring(), OLD_PASSWORD, prefs_path)
    assert old_cipher is not None
    assert _row_secrets(store, old_cipher) == [base32.decode(SECRET_A), base32.decode(SECRET_B)]
    store, prefs = _fresh(vault_path, prefs_path)
    assert _try_password(store, prefs, MemoryKeyring(), NEW_PASSWORD, prefs_path) is None


@pytest.mark.parametrize("seam", ["dir_fsync", "prefs_save"])
def test_password_change_crash_after_commit_keeps_new_key(tmp_path, monkeypatch, seam):
    """Crash after the rename (or in the settings-cache write): the new
    vault is intact and the header overrules the stale settings copy."""
    import xx_auth.store as store_mod

    vault_path, prefs_path, _ = _setup_password_vault(tmp_path)
    store, prefs = _fresh(vault_path, prefs_path)
    if seam == "dir_fsync":
        monkeypatch.setattr(store_mod, "_fsync_dir", _boom_before)
    else:
        monkeypatch.setattr(prefs_mod, "save", _boom_before)
    with pytest.raises(OSError):
        accounts_mod.change_password(store, prefs, MemoryKeyring(),
                                     OLD_PASSWORD, NEW_PASSWORD, prefs_path)
    store, prefs = _fresh(vault_path, prefs_path)
    new_cipher = _try_password(store, prefs, MemoryKeyring(), NEW_PASSWORD, prefs_path)
    assert new_cipher is not None
    assert _row_secrets(store, new_cipher) == [base32.decode(SECRET_A), base32.decode(SECRET_B)]
    assert prefs.lock_kdf_salt == store.header.kdf_salt_hex
    store, prefs = _fresh(vault_path, prefs_path)
    assert _try_password(store, prefs, MemoryKeyring(), OLD_PASSWORD, prefs_path) is None


def test_password_change_needs_current_password_and_keeps_file(tmp_path):
    vault_path, prefs_path, _ = _setup_password_vault(tmp_path)
    before = vault_path.read_bytes()
    store, prefs = _fresh(vault_path, prefs_path)
    with pytest.raises(ValueError, match="Wrong current password"):
        accounts_mod.change_password(store, prefs, MemoryKeyring(),
                                     "not-the-password", NEW_PASSWORD, prefs_path)
    assert vault_path.read_bytes() == before


def test_password_to_keyring_switch_is_atomic(tmp_path, monkeypatch):
    vault_path, prefs_path, cipher = _setup_password_vault(tmp_path)
    keyring = MemoryKeyring()
    store, prefs = _fresh(vault_path, prefs_path)
    monkeypatch.setattr(os, "replace", _boom_rename)
    with pytest.raises(OSError):
        accounts_mod.switch_key_source(store, prefs, keyring, cipher, None, True, prefs_path)
    monkeypatch.undo()
    store, prefs = _fresh(vault_path, prefs_path)
    assert _try_password(store, prefs, keyring, OLD_PASSWORD, prefs_path) is not None

    store, prefs = _fresh(vault_path, prefs_path)
    cipher = _try_password(store, prefs, keyring, OLD_PASSWORD, prefs_path)
    moved = accounts_mod.switch_key_source(store, prefs, keyring, cipher, None, True, prefs_path)
    assert _row_secrets(store, moved) == [base32.decode(SECRET_A), base32.decode(SECRET_B)]
    reopened, reprefs = _fresh(vault_path, prefs_path)
    resolved = accounts_mod.open_at_startup(reopened, reprefs, keyring, prefs_path)
    assert resolved.cipher is not None and not resolved.password_mode
    assert _row_secrets(reopened, resolved.cipher) == [base32.decode(SECRET_A), base32.decode(SECRET_B)]


def test_keyring_to_password_switch_survives_prefs_crash(tmp_path, monkeypatch):
    vault_path, prefs_path = _paths(tmp_path)
    keyring = MemoryKeyring()
    key = keystore_mod.load_or_create_key(keyring)
    store, prefs = _fresh(vault_path, prefs_path)
    cipher = SecretCipher(key)
    accounts_mod.add_from_text(store, f"otpauth://totp/Example:alice?secret={SECRET_A}", cipher)
    store.header = accounts_mod._keyring_header(key)
    store.save()
    assert accounts_mod.open_at_startup(*_fresh(vault_path, prefs_path), keyring, prefs_path).cipher is not None

    store, prefs = _fresh(vault_path, prefs_path)
    cipher = accounts_mod.open_at_startup(store, prefs, keyring, prefs_path).cipher
    monkeypatch.setattr(prefs_mod, "save", _boom_before)
    with pytest.raises(OSError):
        accounts_mod.switch_key_source(store, prefs, keyring, cipher, NEW_PASSWORD, False, prefs_path)
    store, prefs = _fresh(vault_path, prefs_path)
    moved = _try_password(store, prefs, keyring, NEW_PASSWORD, prefs_path)
    assert moved is not None
    assert _row_secrets(store, moved) == [base32.decode(SECRET_A)]
    assert prefs.lock_kdf_salt == store.header.kdf_salt_hex


def test_keyring_switch_without_keyring_changes_nothing(tmp_path):
    vault_path, prefs_path, cipher = _setup_password_vault(tmp_path)
    before = vault_path.read_bytes()
    store, prefs = _fresh(vault_path, prefs_path)
    with pytest.raises(ValueError, match="keyring"):
        accounts_mod.switch_key_source(store, prefs, _DeadKeyring(), cipher, None, True, prefs_path)
    assert vault_path.read_bytes() == before


def test_legacy_password_vault_migrates_on_unlock(tmp_path):
    vault_path, prefs_path = _paths(tmp_path)
    store, prefs = _fresh(vault_path, prefs_path)
    prefs.keyring_disabled = True
    cipher = accounts_mod.setup_password(store, prefs, OLD_PASSWORD, prefs_path)
    accounts_mod.add_from_text(store, f"otpauth://totp/Example:alice?secret={SECRET_A}", cipher)
    store.header = None
    store.save()
    assert isinstance(json.loads(vault_path.read_text(encoding="utf-8")), list)

    migrated, mprefs = _fresh(vault_path, prefs_path)
    opened = accounts_mod.unlock_with_password(migrated, mprefs, MemoryKeyring(),
                                               OLD_PASSWORD, prefs_path)
    assert _row_secrets(migrated, opened) == [base32.decode(SECRET_A)]
    root = json.loads(vault_path.read_text(encoding="utf-8"))
    assert root["header"]["key_source"] == "password"
    assert root["header"]["kdf"]["salt"] == mprefs.lock_kdf_salt


def test_legacy_keyring_vault_migrates_at_startup(tmp_path):
    vault_path, prefs_path = _paths(tmp_path)
    keyring = MemoryKeyring()
    key = keystore_mod.load_or_create_key(keyring)
    store, prefs = _fresh(vault_path, prefs_path)
    cipher = SecretCipher(key)
    accounts_mod.add_from_text(store, f"otpauth://totp/Example:alice?secret={SECRET_A}", cipher)
    store.save()
    assert isinstance(json.loads(vault_path.read_text(encoding="utf-8")), list)

    reopened, reprefs = _fresh(vault_path, prefs_path)
    resolved = accounts_mod.open_at_startup(reopened, reprefs, keyring, prefs_path)
    assert resolved.cipher is not None
    assert _row_secrets(reopened, resolved.cipher) == [base32.decode(SECRET_A)]
    assert json.loads(vault_path.read_text(encoding="utf-8"))["header"]["key_source"] == "keyring"


def test_recovery_tries_keyring_when_settings_claim_password(tmp_path):
    """Old-code leftover: rows under the keyring key, settings pointing at
    a password. The keyring source is tried before giving up."""
    vault_path, prefs_path = _paths(tmp_path)
    keyring = MemoryKeyring()
    key = keystore_mod.load_or_create_key(keyring)
    store, prefs = _fresh(vault_path, prefs_path)
    cipher = SecretCipher(key)
    accounts_mod.add_from_text(store, f"otpauth://totp/Example:alice?secret={SECRET_A}", cipher)
    store.save()
    prefs.keyring_disabled = True
    prefs.lock_kdf_salt = keystore_mod.new_salt().hex()
    prefs.lock_salt = keystore_mod.new_salt().hex()
    prefs.lock_hash = keystore_mod.password_verifier("some-password", bytes.fromhex(prefs.lock_salt))
    prefs_mod.save(prefs, prefs_path)

    reopened, reprefs = _fresh(vault_path, prefs_path)
    recovered = accounts_mod.unlock_with_password(reopened, reprefs, keyring,
                                                  "some-password", prefs_path)
    assert _row_secrets(reopened, recovered) == [base32.decode(SECRET_A)]
    assert reprefs.keyring_disabled is False
    assert json.loads(vault_path.read_text(encoding="utf-8"))["header"]["key_source"] == "keyring"


def test_recovery_tries_password_when_settings_claim_keyring(tmp_path):
    """Old-code leftover: rows under a password key, settings (and a wrong
    keyring key) claiming keyring. Startup stays locked; the password
    still opens and migrates the vault."""
    vault_path, prefs_path = _paths(tmp_path)
    store, prefs = _fresh(vault_path, prefs_path)
    prefs.keyring_disabled = True
    cipher = accounts_mod.setup_password(store, prefs, OLD_PASSWORD, prefs_path)
    accounts_mod.add_from_text(store, f"otpauth://totp/Example:alice?secret={SECRET_A}", cipher)
    store.header = None
    store.save()
    prefs.keyring_disabled = False
    prefs_mod.save(prefs, prefs_path)
    keyring = MemoryKeyring()
    keystore_mod.load_or_create_key(keyring)

    reopened, reprefs = _fresh(vault_path, prefs_path)
    assert accounts_mod.open_at_startup(reopened, reprefs, keyring, prefs_path).cipher is None
    reopened, reprefs = _fresh(vault_path, prefs_path)
    recovered = accounts_mod.unlock_with_password(reopened, reprefs, keyring,
                                                  OLD_PASSWORD, prefs_path)
    assert _row_secrets(reopened, recovered) == [base32.decode(SECRET_A)]
    assert reprefs.keyring_disabled is True


def test_unreadable_vault_is_kept_and_plain(tmp_path):
    vault_path, prefs_path, _ = _setup_password_vault(tmp_path)
    before = vault_path.read_bytes()
    store, prefs = _fresh(vault_path, prefs_path)
    with pytest.raises(VaultUnreadable) as exc:
        accounts_mod.unlock_with_password(store, prefs, MemoryKeyring(),
                                          "wrong-password", prefs_path)
    assert str(exc.value) == "Wrong password."
    assert vault_path.read_bytes() == before

    dead = _DeadKeyring()
    keyring = MemoryKeyring()
    store, prefs = _fresh(vault_path, prefs_path)
    cipher = _try_password(store, prefs, keyring, OLD_PASSWORD, prefs_path)
    accounts_mod.switch_key_source(store, prefs, keyring, cipher, None, True, prefs_path)
    lost = vault_path.read_bytes()
    store, prefs = _fresh(vault_path, prefs_path)
    assert accounts_mod.open_at_startup(store, prefs, dead, prefs_path).cipher is None
    store, prefs = _fresh(vault_path, prefs_path)
    with pytest.raises(VaultUnreadable) as gate_exc:
        accounts_mod.unlock_with_password(store, prefs, dead, "whatever-password", prefs_path)
    assert str(gate_exc.value) == "Wrong password."
    store, prefs = _fresh(vault_path, prefs_path)
    with pytest.raises(VaultUnreadable) as lost_exc:
        accounts_mod.unlock_with_password(store, prefs, dead, OLD_PASSWORD, prefs_path)
    assert str(lost_exc.value) == accounts_mod.UNREADABLE_MESSAGE
    assert vault_path.read_bytes() == lost


def test_aegis_round_trip_through_atomic_vault(tmp_path):
    from xx_auth import aegis

    vault_path, prefs_path, cipher = _setup_password_vault(tmp_path)
    store, _ = _fresh(vault_path, prefs_path)
    plain = accounts_mod.export_vault_text(store, None, cipher)
    locked = accounts_mod.export_vault_text(store, "export-pw", cipher)
    assert aegis.is_encrypted(locked) and not aegis.is_encrypted(plain)
    for text, secret in ((plain, None), (locked, "export-pw")):
        target = VaultStore(tmp_path / f"roundtrip-{bool(secret)}.json").load()
        fresh_cipher = SecretCipher(os.urandom(32))
        imported, skipped = accounts_mod.import_vault_text(target, text, secret, fresh_cipher)
        assert (imported, skipped) == (2, 0)
        assert sorted(fresh_cipher.decrypt(row.secret) for row in target.get_all()) == [
            base32.decode(SECRET_A), base32.decode(SECRET_B)]


def test_no_secret_in_clear_on_disk(tmp_path):
    password = "s3cr3t-horse-battery-9"
    vault_path, prefs_path = _paths(tmp_path)
    keyring = MemoryKeyring()
    store, prefs = _fresh(vault_path, prefs_path)
    prefs.keyring_disabled = True
    cipher = accounts_mod.setup_password(store, prefs, password, prefs_path)
    accounts_mod.add_from_text(store, f"otpauth://totp/Example:alice?secret={SECRET_A}", cipher)
    store.save()
    store, prefs = _fresh(vault_path, prefs_path)
    accounts_mod.change_password(store, prefs, keyring, password, NEW_PASSWORD, prefs_path)
    store, prefs = _fresh(vault_path, prefs_path)
    cipher = _try_password(store, prefs, keyring, NEW_PASSWORD, prefs_path)
    accounts_mod.switch_key_source(store, prefs, keyring, cipher, None, True, prefs_path)

    leftovers = list(tmp_path.rglob("*.tmp"))
    assert leftovers == []
    needles = [password.encode(), NEW_PASSWORD.encode(),
               base32.decode(SECRET_A), SECRET_A.encode()]
    for path in sorted(tmp_path.rglob("*")):
        if path.is_file():
            blob = path.read_bytes()
            for needle in needles:
                assert needle not in blob, f"{needle!r} in clear in {path.name}"
    header = json.loads(vault_path.read_text(encoding="utf-8"))["header"]
    assert set(header) == {"key_source", "kdf", "check"}
    assert hashlib.sha256(password.encode()).hexdigest().encode() not in vault_path.read_bytes()
