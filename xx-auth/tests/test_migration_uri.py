"""MigrationUri tests. Translated from Android `ReviewPassTest` transfer-QR
cases; payloads are built with `struct`-style helpers mirroring the Kotlin
ones, so the vectors are independent of the parser."""
import base64
import urllib.parse

import pytest

from xx_auth import base32, migration_uri

SECRET = "JBSWY3DPEHPK3PXP"


def _varint(out: bytearray, value: int) -> None:
    part = value
    while part & ~0x7F:
        out.append((part & 0x7F) | 0x80)
        part >>= 7
    out.append(part)


def _field(out: bytearray, number: int, raw: bytes) -> None:
    _varint(out, (number << 3) | 2)
    _varint(out, len(raw))
    out += raw


def _field_varint(out: bytearray, number: int, value: int) -> None:
    _varint(out, number << 3)
    _varint(out, value)


def _params(secret: bytes, name: str, issuer: str, algo: int, digits: int, kind: int, counter: int = 0) -> bytes:
    out = bytearray()
    _field(out, 1, secret)
    _field(out, 2, name.encode())
    _field(out, 3, issuer.encode())
    _field_varint(out, 4, algo)
    _field_varint(out, 5, digits)
    _field_varint(out, 6, kind)
    if counter:
        _field_varint(out, 7, counter)
    return bytes(out)


def _migration_uri(*entries: bytes, batch_size: int = 1, batch_index: int = 0) -> str:
    out = bytearray()
    for entry in entries:
        _field(out, 1, entry)
    _field_varint(out, 2, 1)
    _field_varint(out, 3, batch_size)
    _field_varint(out, 4, batch_index)
    data = urllib.parse.quote(base64.b64encode(bytes(out)).decode("ascii"))
    return "otpauth-migration://offline?data=" + data


KEY = base32.decode(SECRET)


def test_decodes_every_account():
    uri = _migration_uri(
        _params(KEY, "GitHub:alice", "GitHub", 1, 1, 2),
        _params(KEY, "bob@example.com", "", 2, 2, 2),
        _params(KEY, "counter", "Bank", 3, 1, 1, counter=7),
        _params(KEY, "legacy", "Old", 4, 1, 2),
        batch_size=2, batch_index=1,
    )
    batch = migration_uri.parse(uri)
    assert len(batch.accounts) == 3
    assert batch.skipped == 1
    assert (batch.index, batch.size) == (1, 2)
    gh, bob, bank = batch.accounts
    assert (gh.issuer, gh.label, gh.algorithm, gh.digits) == ("GitHub", "alice", "SHA1", 6)
    assert bob.issuer is None
    assert (bob.algorithm, bob.digits) == ("SHA256", 8)
    assert (bank.type, bank.counter, bank.algorithm) == ("hotp", 7, "SHA512")
    assert gh.secret == KEY


def test_rejects_junk():
    assert migration_uri.is_migration(_migration_uri(_params(KEY, "a", "b", 1, 1, 2)))
    with pytest.raises(ValueError):
        migration_uri.parse("otpauth-migration://offline?data=%%%")
    with pytest.raises(ValueError):
        migration_uri.parse("https://example.com")
