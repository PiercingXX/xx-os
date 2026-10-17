"""Aegis vault tests. Translated from Android
`setup/VaultImportExportTest.kt` plus the `ReviewPassTest` import cases;
the vault under test is plaintext secrets in, ciphertext out, through the
real `SecretCipher`."""
import json

import pytest

from xx_auth import aegis, base32
from xx_auth.aegis import StoredAccount
from xx_auth.keystore import SecretCipher
from xx_auth.store import VaultStore


def _cipher() -> SecretCipher:
    import os

    return SecretCipher(os.urandom(32))


def _seed(store: VaultStore, cipher: SecretCipher) -> None:
    secret = cipher.encrypt(base32.decode("MZXW6YTBOI"))
    store.insert(StoredAccount("11111111-1111-1111-1111-111111111111", "Example", "alice@example.com",
                               "SHA1", 6, 30, None, secret, 1000, 0))
    store.insert(StoredAccount("22222222-2222-2222-2222-222222222222", "Example", "bob@example.com",
                               "SHA256", 8, 60, None, cipher.encrypt(base32.decode("MZXW6YTBOI")), 2000, 1))
    store.insert(StoredAccount("33333333-3333-3333-3333-333333333333", "", "hotp@example.com",
                               "SHA1", 6, 30, 7, cipher.encrypt(base32.decode("MZXW6YTBOI")), 3000, 2))


def _plaintext(store: VaultStore, cipher: SecretCipher) -> list[StoredAccount]:
    from dataclasses import replace

    return [replace(a, secret=cipher.decrypt(a.secret)) for a in store.get_all()]


def test_plain_export_is_a_real_aegis_vault_file():
    cipher, store = _cipher(), VaultStore().load()
    _seed(store, cipher)
    root = json.loads(aegis.export_vault(_plaintext(store, cipher), None))
    assert root["version"] == 1
    assert root["header"]["slots"] is None
    assert root["header"]["params"] is None
    db = root["db"]
    assert db["version"] == 2
    assert db["groups"] == []
    assert len(db["entries"]) == 3
    alice = db["entries"][0]
    assert alice["type"] == "totp"
    assert alice["uuid"] == "11111111-1111-1111-1111-111111111111"
    assert alice["name"] == "alice@example.com"
    assert alice["issuer"] == "Example"
    assert alice["note"] == "" and alice["favorite"] is False and alice["icon"] is None
    assert alice["info"] == {"secret": "MZXW6YTBOI", "algo": "SHA1", "digits": 6, "period": 30}
    hotp = db["entries"][2]
    assert hotp["type"] == "hotp" and hotp["info"]["counter"] == 7
    assert "period" not in hotp["info"] and hotp["issuer"] == ""
    assert '"accounts"' not in json.dumps(root)


def test_encrypted_round_trip_and_wrong_password():
    cipher, store = _cipher(), VaultStore().load()
    _seed(store, cipher)
    text = aegis.export_vault(_plaintext(store, cipher), "right")
    assert aegis.is_encrypted(text)
    parsed = aegis.parse_vault(text, "right")
    assert len(parsed.accounts) == 3 and parsed.skipped == 0
    assert parsed.accounts[0].secret == base32.decode("MZXW6YTBOI")
    with pytest.raises(ValueError, match="wrong password"):
        aegis.parse_vault(text, "wrong")
    assert not aegis.is_encrypted(aegis.export_vault(_plaintext(store, cipher), None))


def test_skips_foreign_and_malformed_entries():
    cipher, store = _cipher(), VaultStore().load()
    _seed(store, cipher)
    text = aegis.export_vault(_plaintext(store, cipher), None)
    root = json.loads(text)
    root["db"]["entries"].append({"type": "steam", "uuid": "s", "name": "x", "issuer": "y",
                                  "info": {"secret": "MZXW6YTBOI", "algo": "SHA1", "digits": 5, "period": 30}})
    root["db"]["entries"].append({"type": "totp", "info": {"secret": "!!"}})
    parsed = aegis.parse_vault(json.dumps(root), None)
    assert len(parsed.accounts) == 3 and parsed.skipped == 2


def test_accepts_db_versions_1_to_3():
    cipher, store = _cipher(), VaultStore().load()
    _seed(store, cipher)
    text = aegis.export_vault(_plaintext(store, cipher), None)
    for version in (1, 2, 3):
        root = json.loads(text)
        root["db"]["version"] = version
        assert len(aegis.parse_vault(json.dumps(root), None).accounts) == 3
    root = json.loads(text)
    root["version"] = 2
    with pytest.raises(ValueError, match="unsupported Aegis vault version"):
        aegis.parse_vault(json.dumps(root), None)


def test_rejects_non_vaults():
    with pytest.raises(ValueError):
        aegis.parse_vault("hello world", None)
    with pytest.raises(ValueError):
        aegis.parse_vault("", None)
    with pytest.raises(ValueError):
        aegis.parse_vault("{not json", None)


def test_uri_lists_import_with_comments_and_counts():
    secret = "JBSWY3DPEHPK3PXP"
    text = (f"otpauth://totp/A:one?secret={secret}\n# a comment line is ignored\n"
            f"otpauth://totp/B:two?secret={secret}&period=0\n"
            f"otpauth://hotp/C:three?secret={secret}&counter=3\n")
    parsed = aegis.parse_vault(text, None)
    assert [a.label for a in parsed.accounts] == ["one", "three"]
    assert parsed.skipped == 1


def test_encrypted_needs_password():
    cipher, store = _cipher(), VaultStore().load()
    _seed(store, cipher)
    text = aegis.export_vault(_plaintext(store, cipher), "pw")
    with pytest.raises(ValueError, match="password is required"):
        aegis.parse_vault(text, None)


def test_file_cap():
    with pytest.raises(ValueError, match="too large"):
        aegis.parse_file(b"x" * (aegis.MAX_IMPORT_BYTES + 1), None)
