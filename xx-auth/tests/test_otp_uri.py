"""OtpUri tests — Key URI Format. Translated from Android
`engine/OtpUriTest.kt` plus the `ReviewPassTest` URI hardening cases."""
import pytest

from xx_auth.otp_uri import OtpAccount, parse, serialize

SECRET = "JBSWY3DPEHPK3PXP"


def test_parses_full_totp_uri():
    account = parse(
        "otpauth://totp/Example%3Aalice%40example.com?secret=MZXW6YTBOI&issuer=Example&algorithm=SHA256&digits=8&period=60"
    )
    assert account.type == "totp"
    assert account.label == "alice@example.com"
    assert account.issuer == "Example"
    assert account.algorithm == "SHA256"
    assert account.digits == 8
    assert account.period == 60
    assert account.counter is None
    assert account.secret == b"foobar"


def test_applies_defaults():
    account = parse("otpauth://totp/alice?secret=MZXW6YTBOI")
    assert (account.algorithm, account.digits, account.period) == ("SHA1", 6, 30)
    assert account.issuer is None
    assert account.secret == b"foobar"


def test_parses_hotp_with_counter():
    account = parse("otpauth://hotp/alice?secret=MZXW6YTBOI&counter=5")
    assert account.type == "hotp" and account.counter == 5


def test_issuer_falls_back_to_prefix():
    account = parse("otpauth://totp/Example:alice?secret=MZXW6YTBOI")
    assert (account.issuer, account.label) == ("Example", "alice")


def test_query_issuer_wins():
    account = parse("otpauth://totp/Other:alice?secret=MZXW6YTBOI&issuer=Example")
    assert (account.issuer, account.label) == ("Example", "Other:alice")


def test_spaces_around_prefix_colon_trimmed():
    account = parse("otpauth://totp/Example:%20alice?secret=MZXW6YTBOI")
    assert (account.issuer, account.label) == ("Example", "alice")


def test_round_trips():
    account = OtpAccount("totp", "alice@example.com", "Example", "SHA256", 8, 60, None, b"foobar")
    assert parse(serialize(account)) == account
    hotp = OtpAccount("hotp", "alice", None, "SHA1", 6, 30, 7, b"foobar")
    assert parse(serialize(hotp)) == hotp


def test_serialize_exact_totp():
    account = OtpAccount("totp", "alice", None, "SHA1", 6, 30, None, b"foobar")
    assert serialize(account) == "otpauth://totp/alice?secret=MZXW6YTBOI&algorithm=SHA1&digits=6&period=30"


def test_serialize_percent_encodes_label():
    account = OtpAccount("totp", "alice@example.com", "Example", "SHA1", 6, 30, None, b"foobar")
    assert serialize(account) == (
        "otpauth://totp/Example:alice%40example.com?secret=MZXW6YTBOI&issuer=Example&algorithm=SHA1&digits=6&period=30"
    )
    assert parse(serialize(account)) == account


def test_upper_case_payloads_parse():
    account = parse(f"OTPAUTH://TOTP/ACME:ALICE?SECRET={SECRET}&ISSUER=ACME")
    assert (account.type, account.issuer, account.label) == ("totp", "ACME", "ALICE")


def test_labels_decode_as_utf8():
    account = parse("otpauth://totp/M%C3%BCller%20GmbH:j%C3%B6rg?secret=" + SECRET)
    assert (account.issuer, account.label) == ("Müller GmbH", "jörg")
    assert parse(serialize(account)) == account


def test_plus_in_label_path_stays_plus():
    assert parse("otpauth://totp/a+b@example.com?secret=" + SECRET).label == "a+b@example.com"


def test_malformed_escapes_kept_literally():
    assert parse("otpauth://totp/50%-off?secret=" + SECRET).label == "50%-off"


@pytest.mark.parametrize("uri", [
    f"otpauth://totp/a?secret={SECRET}&period=0",
    f"otpauth://totp/a?secret={SECRET}&period=-30",
    "otpauth://totp/a?secret=",
])
def test_rejects_bad_params(uri):
    with pytest.raises(ValueError):
        parse(uri)


def test_errors_never_echo_secret():
    bad = [
        f"otpauth://totp?secret={SECRET}",
        f"otpauth://xotp/a?secret={SECRET}",
        f"otpauth://totp/a?secret={SECRET}&digits=x",
        f"otpauth://totp/a?secret={SECRET}&algorithm=MD5",
    ]
    for uri in bad:
        with pytest.raises(ValueError) as caught:
            parse(uri)
        assert SECRET not in str(caught.value)


def test_rejects_non_otpauth():
    with pytest.raises(ValueError):
        parse("https://example.com")
