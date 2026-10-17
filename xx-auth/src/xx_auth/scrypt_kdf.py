"""scrypt (RFC 7914) via the stdlib. Linux mapping of Android
`setup/Scrypt.kt`: the Aegis KDF is scrypt(N=32768, r=8, p=1) with a 32-byte
salt and key, so the pure-Kotlin Salsa20/8 core maps to `hashlib.scrypt` and
the RFC 7914 §12 vectors pin the mapping instead of a fresh implementation."""

from __future__ import annotations

import hashlib

AEGIS_N = 32768
AEGIS_R = 8
AEGIS_P = 1
AEGIS_SALT_LEN = 32
AEGIS_KEY_LEN = 32


def derive(password: bytes, salt: bytes, n: int, r: int, p: int, dk_len: int) -> bytes:
    # OpenSSL caps scrypt at 32 MB by default; N=32768/r=8 needs ~33.5 MB.
    return hashlib.scrypt(password, salt=salt, n=n, r=r, p=p, dklen=dk_len,
                          maxmem=max(256 * 1024 * 1024, 256 * r * n * p))
