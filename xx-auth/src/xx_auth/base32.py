"""Base32 codec (RFC 4648). Port of Android `engine/Base32.kt`."""

from __future__ import annotations

_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_DECODE = {c: i for i, c in enumerate(_ALPHABET)}


def decode(data: str) -> bytes:
    cleaned = "".join(ch for ch in data if ch != "=" and not ch.isspace()).upper()
    residue = len(cleaned) % 8
    if residue in (1, 3, 6):
        raise ValueError(f"invalid Base32 length {len(cleaned)} (residue {residue})")
    acc = 0
    bits = 0
    out = bytearray(len(cleaned) * 5 // 8)
    pos = 0
    for ch in cleaned:
        value = _DECODE.get(ch, -1)
        if value < 0:
            raise ValueError(f"invalid Base32 character: {ch}")
        acc = (acc << 5) | value
        bits += 5
        if bits >= 8:
            bits -= 8
            out[pos] = (acc >> bits) & 0xFF
            pos += 1
    return bytes(out)


def decode_or_null(data: str) -> bytes | None:
    try:
        return decode(data)
    except ValueError:
        return None


def encode(raw: bytes) -> str:
    parts: list[str] = []
    acc = 0
    bits = 0
    for byte in raw:
        acc = (acc << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            parts.append(_ALPHABET[(acc >> bits) & 0x1F])
    if bits:
        parts.append(_ALPHABET[(acc << (5 - bits)) & 0x1F])
    return "".join(parts)
