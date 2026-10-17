"""HOTP (RFC 4226) and TOTP (RFC 6238). Port of Android `engine/Hotp.kt`,
`engine/Totp.kt` and `engine/Digest.kt`."""

from __future__ import annotations

import hashlib
import hmac
import struct

DIGESTS = ("SHA1", "SHA256", "SHA512")
_HASH = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}

_POW10 = (1, 10, 100, 1000, 10000, 100000, 1000000, 10000000, 100000000)


def check_algorithm(algorithm: str) -> str:
    algo = algorithm.upper().replace("-", "")
    if algo not in DIGESTS:
        raise ValueError(f"unsupported algorithm: {algorithm}")
    return algo


def hotp_generate(key: bytes, counter: int, digits: int, digest: str = "SHA1") -> str:
    if digits not in range(6, 9):
        raise ValueError(f"digits must be 6..8, was {digits}")
    algo = check_algorithm(digest)
    mac = hmac.new(key, struct.pack(">Q", counter), _HASH[algo]).digest()
    offset = mac[-1] & 0x0F
    binary = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(binary % _POW10[digits]).zfill(digits)


def totp_generate(key: bytes, time_step: int, digits: int, digest: str, unix_seconds: int) -> str:
    if time_step <= 0:
        raise ValueError(f"time_step must be positive, was {time_step}")
    return hotp_generate(key, unix_seconds // time_step, digits, digest)


def remaining_millis(time_step: int, unix_millis: int) -> int:
    if time_step <= 0:
        raise ValueError(f"time_step must be positive, was {time_step}")
    step_millis = time_step * 1000
    return step_millis - (unix_millis % step_millis)
