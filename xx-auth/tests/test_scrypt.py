"""scrypt tests — RFC 7914 §12 vectors. Translated from Android
`setup/ScryptTest.kt`; they pin `scrypt_kdf.derive` (hashlib.scrypt) to the
RFC answers, plus the production-parameter guard."""
from xx_auth import scrypt_kdf


def _hex(text: str) -> bytes:
    return bytes.fromhex("".join(text.split()))


def test_rfc7914_vector1():
    out = scrypt_kdf.derive(b"", b"", 16, 1, 1, 64)
    assert out == _hex(
        "77d6576238657b203b19ca42c18a0497f16b4844e3074ae8dfdffa3fede21442"
        "fcd0069ded0948f8326a753a0fc81f17e8d3e0fb2e0d3628cf35e20c38d18906"
    )


def test_rfc7914_vector2():
    out = scrypt_kdf.derive(b"password", b"NaCl", 1024, 8, 16, 64)
    assert out == _hex(
        "fdbabe1c9d3472007856e7190d01e9fe7c6ad7cbc8237830e77376634b373162"
        "2eaf30d92e22a3886ff109279d9830dac727afb94a83ee6d8360cbdfa2cc0640"
    )


def test_rfc7914_vector3():
    out = scrypt_kdf.derive(b"pleaseletmein", b"SodiumChloride", 16384, 8, 1, 64)
    assert out == _hex(
        "70 23 bd cb 3a fd 73 48 46 1c 06 cd 81 fd 38 eb "
        "fd a8 fb ba 90 4f 8e 3e a9 b5 43 f6 54 5d a1 f2 "
        "d5 43 29 55 61 3f 0f cf 62 d4 97 05 24 2a 9a f9 "
        "e6 1e 85 dc 0d 65 1e 40 df cf 01 7b 45 57 58 87"
    )


def test_production_params_match_aegis():
    assert (scrypt_kdf.AEGIS_N, scrypt_kdf.AEGIS_R, scrypt_kdf.AEGIS_P) == (32768, 8, 1)
    assert (scrypt_kdf.AEGIS_SALT_LEN, scrypt_kdf.AEGIS_KEY_LEN) == (32, 32)
