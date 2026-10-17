"""Hardening tests — C20. Translated from Android `HardeningTest.kt`: the
logger's redaction is pinned, and account stringification never prints a
secret."""
from xx_auth import applog
from xx_auth.aegis import StoredAccount
from xx_auth.applog import LogRing


def test_redact_strips_bearer():
    out = applog.redact("authorization: Bearer abc123DEF456")
    assert "abc123DEF456" not in out
    assert "Bearer ***" in out


def test_redact_strips_guarded_values():
    for raw, value, placeholder in (
        ("secret=MZXW6YTBOI", "MZXW6YTBOI", "secret=***"),
        ("password=hunter2", "hunter2", "password=***"),
        ("token=abc123", "abc123", "token=***"),
        ("otp=481516", "481516", "otp=***"),
        ("pin=1234", "1234", "pin=***"),
        ("Secret: MZXW6YTBOI", "MZXW6YTBOI", "Secret=***"),
    ):
        out = applog.redact(raw)
        assert value not in out, raw
        assert placeholder in out, raw


def test_redact_strips_emails():
    out = applog.redact("logged in as alice@example.com")
    assert "alice@example.com" not in out
    assert "***@***" in out


def test_redact_strips_full_otpauth_uris():
    uri = "otpauth://totp/Example:alice@example.com?secret=MZXW6YTBOI&issuer=Example"
    out = applog.redact(uri)
    assert "MZXW6YTBOI" not in out
    assert "otpauth://totp" not in out
    assert "otpauth://***" in out


def test_ring_never_emits_a_secret():
    ring = LogRing()
    ring.emit("vault", "added secret=MZXW6YTBOI for alice@example.com")
    dump = ring.dump()
    assert "MZXW6YTBOI" not in dump
    assert "alice@example.com" not in dump


def test_account_str_redacts():
    account = StoredAccount("id", "Example", "alice", "SHA1", 6, 30, None, b"ciphertext", 1, 0)
    assert "ciphertext" not in str(account)
    assert "[redacted]" in str(account)
