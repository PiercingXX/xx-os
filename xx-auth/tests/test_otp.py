"""HOTP/TOTP tests — RFC 4226 Appendix D and RFC 6238 Appendix B.
Translated from Android `engine/HotpTest.kt` and `engine/TotpTest.kt`, with
the C1 correction the Android tree owes itself: SHA256/SHA512 pin the real
Appendix-B key lengths and answers, not the 20-byte SHA1 secret."""
import pytest

from xx_auth import otp

SHA1_KEY = b"12345678901234567890"
SHA256_KEY = b"12345678901234567890123456789012"
SHA512_KEY = b"1234567890123456789012345678901234567890123456789012345678901234"

APPENDIX_D = {
    0: "755224", 1: "287082", 2: "359152", 3: "969429", 4: "338314",
    5: "254676", 6: "287922", 7: "162583", 8: "399871", 9: "520489",
}

APPENDIX_B = [
    (59, "94287082", "46119246", "90693936"),
    (1111111109, "07081804", "68084774", "25091201"),
    (1111111111, "14050471", "67062674", "99943326"),
    (1234567890, "89005924", "91819424", "93441116"),
    (2000000000, "69279037", "90698825", "38618901"),
    (20000000000, "65353130", "77737706", "47863826"),
]


@pytest.mark.parametrize("counter,expected", sorted(APPENDIX_D.items()))
def test_hotp_matches_rfc4226_appendix_d(counter, expected):
    assert otp.hotp_generate(SHA1_KEY, counter, 6, "SHA1") == expected


def test_hotp_eight_digits():
    eight = otp.hotp_generate(SHA1_KEY, 0, 8, "SHA1")
    assert eight == "84755224"
    assert otp.hotp_generate(SHA1_KEY, 0, 6, "SHA1") == "755224"


def test_hotp_seven_digits():
    seven = otp.hotp_generate(SHA1_KEY, 0, 7, "SHA1")
    assert len(seven) == 7 and seven.isdigit()


def test_hotp_digests_differ_and_repeat():
    assert otp.hotp_generate(SHA1_KEY, 42, 6, "SHA256") == otp.hotp_generate(SHA1_KEY, 42, 6, "SHA256")
    assert otp.hotp_generate(SHA1_KEY, 42, 6, "SHA256") != otp.hotp_generate(SHA1_KEY, 42, 6, "SHA1")
    assert otp.hotp_generate(SHA1_KEY, 42, 6, "SHA512") == otp.hotp_generate(SHA1_KEY, 42, 6, "SHA512")


def test_hotp_counter_is_big_endian():
    assert otp.hotp_generate(SHA1_KEY, 1, 6, "SHA1") != otp.hotp_generate(SHA1_KEY, 256, 6, "SHA1")


def test_hotp_pads_with_leading_zeros():
    code = otp.hotp_generate(bytes([1]), 0, 6, "SHA1")
    assert len(code) == 6 and code.isdigit()


def test_hotp_rejects_bad_digits():
    with pytest.raises(ValueError):
        otp.hotp_generate(SHA1_KEY, 0, 5, "SHA1")


@pytest.mark.parametrize("moment,sha1,sha256,sha512", APPENDIX_B)
def test_totp_matches_rfc6238_appendix_b(moment, sha1, sha256, sha512):
    assert otp.totp_generate(SHA1_KEY, 30, 8, "SHA1", moment) == sha1
    assert otp.totp_generate(SHA256_KEY, 30, 8, "SHA256", moment) == sha256
    assert otp.totp_generate(SHA512_KEY, 30, 8, "SHA512", moment) == sha512


def test_totp_counter_is_floor():
    assert otp.totp_generate(SHA1_KEY, 30, 8, "SHA1", 0) == otp.totp_generate(SHA1_KEY, 30, 8, "SHA1", 29)
    assert otp.totp_generate(SHA1_KEY, 30, 8, "SHA1", 59) != otp.totp_generate(SHA1_KEY, 30, 8, "SHA1", 0)


def test_totp_step_changes_code():
    assert otp.totp_generate(SHA1_KEY, 30, 8, "SHA1", 59) != otp.totp_generate(SHA1_KEY, 60, 8, "SHA1", 59)


def test_totp_six_digits_match_hotp_at_edge():
    assert otp.totp_generate(SHA1_KEY, 30, 6, "SHA1", 59) == "287082"
    assert otp.hotp_generate(SHA1_KEY, 1, 6, "SHA1") == "287082"


def test_remaining_millis_counts_down():
    assert otp.remaining_millis(30, 0) == 30000
    assert otp.remaining_millis(30, 15000) == 15000
    assert otp.remaining_millis(30, 30000) == 30000
    assert otp.remaining_millis(30, 59000) == 1000


def test_rejects_non_positive_step():
    with pytest.raises(ValueError):
        otp.totp_generate(SHA1_KEY, 0, 6, "SHA1", 59)
    with pytest.raises(ValueError):
        otp.remaining_millis(0, 0)
