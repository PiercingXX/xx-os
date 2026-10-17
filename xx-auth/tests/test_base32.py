"""Base32 codec tests — RFC 4648 §10 vectors. Translated from Android
`engine/Base32Test.kt`."""
import pytest

from xx_auth import base32

VECTORS = {
    "": "",
    "f": "MY",
    "fo": "MZXQ",
    "foo": "MZXW6",
    "foob": "MZXW6YQ",
    "fooba": "MZXW6YTB",
    "foobar": "MZXW6YTBOI",
}


@pytest.mark.parametrize("plain,encoded", sorted(VECTORS.items()))
def test_encode_matches_rfc4648(plain, encoded):
    assert base32.encode(plain.encode()) == encoded


@pytest.mark.parametrize("plain,encoded", sorted(VECTORS.items()))
def test_decode_matches_rfc4648(plain, encoded):
    assert base32.decode(encoded) == plain.encode()


def test_hello_vector():
    assert base32.encode(b"Hello") == "JBSWY3DP"
    assert base32.decode("JBSWY3DP") == b"Hello"


def test_further_canonical_vectors():
    assert base32.encode(b"test") == "ORSXG5A"
    assert base32.decode("ORSXG5A") == b"test"
    assert base32.encode(b"1234567890") == "GEZDGNBVGY3TQOJQ"
    assert base32.decode("GEZDGNBVGY3TQOJQ") == b"1234567890"
    assert base32.encode(b"abc") == "MFRGG"
    assert base32.decode("MFRGG") == b"abc"


def test_single_ff_vector():
    assert base32.encode(bytes([0xFF])) == "74"
    assert base32.decode("74") == bytes([0xFF])
    assert base32.encode(bytes([0xFF, 0x00])) == "74AA"
    assert base32.decode("74AA") == bytes([0xFF, 0x00])


def test_decode_case_insensitive():
    assert base32.decode("mzxw6ytboi") == b"foobar"
    assert base32.decode("Mzxw6YtboI") == b"foobar"


def test_decode_ignores_padding_and_whitespace():
    assert base32.decode("MZXW6YTBOI==") == b"foobar"
    assert base32.decode("MZXW 6YTB OI") == b"foobar"
    assert base32.decode("MZXW6YTBOI\n") == b"foobar"


def test_round_trips():
    for raw in (b"", b"foobar", bytes([0]), bytes([0xFF]) * 7, b"The quick brown fox"):
        assert base32.decode(base32.encode(raw)) == raw


def test_decode_valid_residues():
    assert base32.decode("MZXW") == b"fo"
    assert base32.decode("MY") == b"f"
    three = bytes([1, 2, 3])
    four = bytes([1, 2, 3, 4])
    assert len(base32.encode(three)) % 8 == 5
    assert len(base32.encode(four)) % 8 == 7
    assert base32.decode(base32.encode(three)) == three
    assert base32.decode(base32.encode(four)) == four


def test_decode_combined_noise():
    assert base32.decode("mzxw 6ytb oi==") == b"foobar"


@pytest.mark.parametrize("bad", ["MZXW6YTBOI1", "MZXW6YTBX", "MZX", "MZXW6Y"])
def test_decode_rejects(bad):
    with pytest.raises(ValueError):
        base32.decode(bad)


def test_decode_or_null():
    assert base32.decode_or_null("MZXW6YTBOI1") is None
    assert base32.decode_or_null("MZXW6YTBX") is None
    assert base32.decode_or_null("mzxw6ytboi") == b"foobar"
