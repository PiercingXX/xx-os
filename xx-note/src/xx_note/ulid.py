"""ULID identifiers per the ULID spec (design D3).

Port of ``core/Ulid.kt``: 26 chars of Crockford Base32 encoding a 48-bit
millisecond timestamp + 80 bits of randomness. Lexical order equals
creation order. Consecutive calls in the same millisecond increment the
randomness instead of redrawing, so ids never collide or regress.
"""

from __future__ import annotations

import random as _random
import threading
import time

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
LENGTH = 26
MAX_TIMESTAMP = (1 << 48) - 1

_lock = threading.Lock()
_last_millis = -1
_last_high = 0
_last_low = 0


def _fresh(epoch_milli: int, rand: _random.Random) -> tuple[int, int]:
    first = rand.getrandbits(64)
    second = rand.getrandbits(64)
    high = ((epoch_milli << 16) | (first >> 48)) & ((1 << 64) - 1)
    return high, second


def _encode(high: int, low: int) -> str:
    raw = high.to_bytes(8, "big") + low.to_bytes(8, "big")
    chars = [""] * LENGTH
    byte_index = 0
    buffer = raw[byte_index]
    chars[0] = ALPHABET[(buffer >> 5) & 0x7]
    bits = 5
    for ci in range(1, LENGTH):
        while bits < 5:
            byte_index += 1
            buffer = ((buffer << 8) | raw[byte_index]) & 0xFFFFFFFFFFFF
            bits += 8
        bits -= 5
        chars[ci] = ALPHABET[(buffer >> bits) & 0x1F]
    return "".join(chars)


def generate() -> str:
    return generate_at(int(time.time() * 1000), _random.Random())


def generate_at(epoch_milli: int, rand: _random.Random | None = None) -> str:
    if epoch_milli < 0 or epoch_milli > MAX_TIMESTAMP:
        raise ValueError(f"timestamp out of 48-bit range: {epoch_milli}")
    if rand is None:
        rand = _random.Random()
    global _last_millis, _last_high, _last_low
    with _lock:
        if _last_millis == epoch_milli:
            low = (_last_low + 1) & ((1 << 64) - 1)
            high = _last_high + (1 if low == 0 else 0)
            high &= (1 << 64) - 1
            if (high >> 16) != epoch_milli:
                raise OverflowError(
                    f"ULID randomness overflowed 80 bits within millisecond {epoch_milli}"
                )
        else:
            _last_millis = epoch_milli
            high, low = _fresh(epoch_milli, rand)
        _last_high, _last_low = high, low
        return _encode(high, low)


def reset_for_tests() -> None:
    global _last_millis, _last_high, _last_low
    with _lock:
        _last_millis = -1
        _last_high = 0
        _last_low = 0


def is_valid(value: str) -> bool:
    if len(value) != LENGTH:
        return False
    for index, char in enumerate(value):
        digit = ALPHABET.find(char)
        if digit < 0:
            return False
        if index == 0 and digit > 0b111:
            return False
    return True


def timestamp_of(value: str) -> int:
    if not is_valid(value):
        raise ValueError(f"not a valid ULID: {value}")
    high = 0
    low = 0
    for index, char in enumerate(value):
        digit = ALPHABET.find(char)
        if index == 0:
            digit &= 0b111
        high = ((high << 5) | (low >> 59)) & ((1 << 64) - 1)
        low = ((low << 5) | digit) & ((1 << 64) - 1)
    return high >> 16
