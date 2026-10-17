"""Store and cipher tests: DAO contract, first-run dirs, ciphertext-only
disk, and the keyring/password key ladder."""
import json
import os
import stat

import pytest

from xx_auth import keystore
from xx_auth.aegis import StoredAccount
from xx_auth.keystore import MemoryKeyring, SecretCipher
from xx_auth.store import VaultStore


def _account(secret: bytes, order: int = 0, aid: str = "id-1") -> StoredAccount:
    return StoredAccount(aid, "Example", "alice", "SHA1", 6, 30, None, secret, 1, order)


def test_insert_aborts_on_duplicate(tmp_path):
    store = VaultStore(tmp_path / "vault.json").load()
    store.insert(_account(b"a"))
    with pytest.raises(ValueError):
        store.insert(_account(b"b"))


def test_update_throws_when_absent(tmp_path):
    store = VaultStore(tmp_path / "vault.json").load()
    with pytest.raises(ValueError):
        store.update(_account(b"a", aid="missing"))


def test_order_and_move_and_delete(tmp_path):
    store = VaultStore(tmp_path / "vault.json").load()
    store.insert(_account(b"a", 0, "a"))
    store.insert(_account(b"b", 1, "b"))
    assert [a.id for a in store.get_all()] == ["a", "b"]
    assert store.move("b", 0) == ["b", "a"]
    assert store.move("b", 5) is None
    store.delete("b")
    assert store.count() == 1


def test_advance_counter_hotp_only(tmp_path):
    from dataclasses import replace

    store = VaultStore(tmp_path / "vault.json").load()
    store.insert(replace(_account(b"a", 0, "h"), counter=7))
    store.insert(_account(b"b", 1, "t"))
    assert store.advance_counter("h").counter == 8
    assert store.advance_counter("t") is None
    assert store.advance_counter("missing") is None


def test_first_run_creates_dirs_and_round_trips(tmp_path):
    path = tmp_path / "deep" / "nested" / "vault.json"
    store = VaultStore(path).load()
    store.insert(_account(b"ciphertext"))
    store.save()
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    assert VaultStore(path).load().count() == 1


def test_corrupt_file_loads_empty(tmp_path):
    path = tmp_path / "vault.json"
    path.write_text("{nope", encoding="utf-8")
    assert VaultStore(path).load().count() == 0


def test_rows_hold_ciphertext_only(tmp_path):
    cipher = SecretCipher(os.urandom(32))
    store = VaultStore(tmp_path / "vault.json").load()
    store.insert(_account(cipher.encrypt(b"plaintext-secret")))
    store.save()
    raw = json.loads((tmp_path / "vault.json").read_text(encoding="utf-8"))
    assert "plaintext-secret" not in json.dumps(raw)


def test_cipher_blob_format_and_wrong_key():
    cipher = SecretCipher(os.urandom(32))
    blob = cipher.encrypt(b"hello")
    assert cipher.decrypt(blob) == b"hello"
    assert cipher.encrypt(b"hello") != cipher.encrypt(b"hello")
    with pytest.raises(ValueError):
        SecretCipher(os.urandom(32)).decrypt(blob)
    with pytest.raises(ValueError):
        cipher.decrypt(b"short")


def test_memory_keyring_ladder():
    keyring = MemoryKeyring()
    key = keystore.load_or_create_key(keyring)
    assert keystore.load_or_create_key(keyring) == key
    assert len(key) == 32


def test_password_derivation_is_stable():
    salt = keystore.new_salt()
    assert keystore.derive_key("correct horse", salt) == keystore.derive_key("correct horse", salt)
    assert keystore.derive_key("other", salt) != keystore.derive_key("correct horse", salt)


def test_password_verifier_round_trip():
    salt = keystore.new_salt()
    verifier = keystore.password_verifier("s3cret", salt)
    assert verifier == keystore.password_verifier("s3cret", salt)
    assert verifier != keystore.password_verifier("wrong", salt)
