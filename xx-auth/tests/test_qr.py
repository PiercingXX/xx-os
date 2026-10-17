"""QR gate tests. Translated from the `ReviewPassTest` scanner cases; image
decoding itself is backend-driven and tested with fakes."""
from xx_auth import qr


def test_otpauth_gate_accepts_valid_rejects_junk():
    uri = "otpauth://totp/Example:alice?secret=JBSWY3DPEHPK3PXP&issuer=Example"
    assert qr.otpauth_payload(uri) == uri
    assert qr.otpauth_payload("https://example.com") is None
    assert qr.otpauth_payload("otpauth://totp/a?secret=!!!") is None


def test_account_gate_accepts_migration_rejects_junk():
    import base64
    import urllib.parse

    out = bytes([0x0A, 0x05, 0x0A, 0x03, 0x01, 0x02, 0x03, 0x18, 0x01])
    uri = "otpauth-migration://offline?data=" + urllib.parse.quote(base64.b64encode(out).decode())
    assert qr.account_payload(uri) == uri
    assert qr.account_payload("otpauth-migration://offline?data=%%%") is None
    assert qr.account_payload("https://example.com") is None


def test_decode_without_backend_explains():
    if qr.available_backends():
        return
    try:
        qr.decode_image_file("/nonexistent.png")
    except ValueError as exc:
        assert "no QR decoder" in str(exc)
    else:
        raise AssertionError("expected ValueError")
    assert "Paste the URI" in qr.backend_help()


def test_scan_payloads_picks_first_usable():
    from xx_auth.accounts import scan_payloads

    assert scan_payloads(["https://example.com", "otpauth://totp/a?secret=JBSWY3DPEHPK3PXP"]) is not None
    assert scan_payloads(["https://example.com"]) is None
