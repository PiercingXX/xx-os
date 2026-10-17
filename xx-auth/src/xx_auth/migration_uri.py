"""`otpauth-migration://` transfer payloads. Port of Android
`engine/MigrationUri.kt`: the documented protobuf layout decoded by hand, no
protobuf dependency. MD5 entries are skipped and counted."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from urllib.parse import unquote_plus

from xx_auth.otp_uri import OtpAccount

_SCHEME = "otpauth-migration://"

_VARINT, _I64, _LEN, _I32 = 0, 1, 2, 5


class _Reader:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def more(self) -> bool:
        return self._pos < len(self._data)

    def varint(self) -> int:
        result = 0
        shift = 0
        while True:
            if self._pos >= len(self._data):
                raise ValueError("truncated migration data")
            byte = self._data[self._pos]
            self._pos += 1
            result |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return result
            shift += 7
            if shift >= 64:
                raise ValueError("malformed migration data")

    def tag(self) -> tuple[int, int]:
        key = self.varint()
        return key >> 3, key & 7

    def read_bytes(self) -> bytes:
        length = self.varint()
        if length < 0 or self._pos + length > len(self._data):
            raise ValueError("truncated migration data")
        out = self._data[self._pos:self._pos + length]
        self._pos += length
        return out

    def skip(self, wire: int) -> None:
        if wire == _VARINT:
            self.varint()
        elif wire == _I64:
            self._pos += 8
        elif wire == _LEN:
            self.read_bytes()
        elif wire == _I32:
            self._pos += 4
        else:
            raise ValueError("malformed migration data")
        if self._pos > len(self._data):
            raise ValueError("truncated migration data")


@dataclass
class Batch:
    accounts: list[OtpAccount] = field(default_factory=list)
    skipped: int = 0
    index: int = 0
    size: int = 1


def is_migration(text: str) -> bool:
    return text.strip().lower().startswith(_SCHEME)


def parse(uri: str) -> Batch:
    trimmed = uri.strip()
    if not is_migration(trimmed):
        raise ValueError("not an otpauth-migration:// URI")
    query = trimmed.partition("?")[2]
    data_text = ""
    for pair in query.split("&"):
        key, _, value = pair.partition("=")
        if key.lower() == "data":
            data_text = value
            break
    if not data_text:
        raise ValueError("migration URI has no data")
    raw = _decode_base64(unquote_plus(data_text))
    batch = Batch()
    reader = _Reader(raw)
    while reader.more():
        number, wire = reader.tag()
        if number == 1 and wire == _LEN:
            account = _parse_parameters(reader.read_bytes())
            if account is None:
                batch.skipped += 1
            else:
                batch.accounts.append(account)
        elif number == 3 and wire == _VARINT:
            batch.size = max(1, reader.varint())
        elif number == 4 and wire == _VARINT:
            batch.index = reader.varint()
        else:
            reader.skip(wire)
    if not batch.accounts and not batch.skipped:
        raise ValueError("migration QR holds no accounts")
    return batch


def _parse_parameters(data: bytes) -> OtpAccount | None:
    secret = b""
    name = ""
    issuer = ""
    algorithm = 1
    digits = 1
    kind = 2
    counter = 0
    reader = _Reader(data)
    while reader.more():
        number, wire = reader.tag()
        if number == 1 and wire == _LEN:
            secret = reader.read_bytes()
        elif number == 2 and wire == _LEN:
            name = reader.read_bytes().decode("utf-8")
        elif number == 3 and wire == _LEN:
            issuer = reader.read_bytes().decode("utf-8")
        elif number == 4 and wire == _VARINT:
            algorithm = reader.varint()
        elif number == 5 and wire == _VARINT:
            digits = reader.varint()
        elif number == 6 and wire == _VARINT:
            kind = reader.varint()
        elif number == 7 and wire == _VARINT:
            counter = reader.varint()
        else:
            reader.skip(wire)
    algo = {0: "SHA1", 1: "SHA1", 2: "SHA256", 3: "SHA512"}.get(algorithm)
    if algo is None or not secret:
        return None
    prefix = name.split(":", 1)[0].strip() or None if ":" in name else None
    resolved = issuer.strip() or (prefix or "")
    if prefix is not None and prefix.lower() == resolved.lower():
        label = name.split(":", 1)[1].strip()
    else:
        label = name.strip()
    hotp = kind == 1
    return OtpAccount(
        "hotp" if hotp else "totp", label,
        resolved or None, algo, 8 if digits == 2 else 6, 30,
        max(0, counter) if hotp else None, secret,
    )


def _decode_base64(text: str) -> bytes:
    cleaned = text.strip().replace(" ", "+")
    try:
        if "-" in cleaned or "_" in cleaned:
            padded = cleaned + "=" * (-len(cleaned) % 4)
            return base64.urlsafe_b64decode(padded)
        padded = cleaned + "=" * (-len(cleaned) % 4)
        return base64.b64decode(padded)
    except Exception as exc:
        raise ValueError("migration data is not Base64") from exc
