"""`otpauth://` Key URI Format (RFC 6238 Annex B). Port of Android
`engine/OtpUri.kt`, including the ReviewPass hardening (upper-case payloads,
UTF-8 labels, `+` stays `+` in the path, literal malformed escapes, errors
that never echo the URI)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote, unquote

from xx_auth import base32

SCHEME = "otpauth"
MAX_PERIOD = 86400
_DEFAULT_ALGORITHM = "SHA1"
_DEFAULT_DIGITS = 6
_DEFAULT_PERIOD = 30

_HEX = "0123456789ABCDEF"


def _percent_decode(text: str, plus_is_space: bool = True) -> str:
    raw = text.encode("utf-8")
    out = bytearray()
    i = 0
    while i < len(raw):
        byte = raw[i]
        if byte == 0x25 and i + 2 < len(raw) and all(
            chr(c) in "0123456789abcdefABCDEF" for c in raw[i + 1:i + 3]
        ):
            out.append(int(raw[i + 1:i + 3], 16))
            i += 3
        elif byte == 0x2B and plus_is_space:
            out.append(0x20)
            i += 1
        else:
            out.append(byte)
            i += 1
    return bytes(out).decode("utf-8")


def _percent_encode(text: str) -> str:
    out: list[str] = []
    for byte in text.encode("utf-8"):
        ch = chr(byte)
        if byte < 0x80 and (ch.isalnum() or ch in "-_.~"):
            out.append(ch)
        else:
            out.append("%" + _HEX[byte >> 4] + _HEX[byte & 0x0F])
    return "".join(out)


@dataclass
class OtpAccount:
    type: str
    label: str
    issuer: str | None
    algorithm: str
    digits: int
    period: int
    counter: int | None
    secret: bytes


def parse(uri: str) -> OtpAccount:
    trimmed = uri.strip()
    if not trimmed[:len(SCHEME) + 3].lower() == SCHEME + "://":
        raise ValueError("not an otpauth:// URI")
    rest = trimmed[len(SCHEME) + 3:]
    host_end = rest.find("/")
    if host_end <= 0:
        raise ValueError("otpauth URI has no type or label")
    kind = rest[:host_end].lower()
    if kind not in ("totp", "hotp"):
        raise ValueError("unknown otpauth type")
    after = rest[host_end + 1:]
    query_index = after.find("?")
    label_part = after if query_index < 0 else after[:query_index]
    query = "" if query_index < 0 else after[query_index + 1:]
    raw_label = _percent_decode(label_part, plus_is_space=False)
    params: dict[str, str] = {}
    if query:
        for pair in query.split("&"):
            key, _, value = pair.partition("=")
            params[key.lower()] = unquote(value)

    secret_text = (params.get("secret") or "").strip()
    if not secret_text:
        raise ValueError("otpauth URI has no secret")
    decoded = base32.decode_or_null(secret_text)
    if decoded is None:
        raise ValueError("otpauth secret is not valid Base32")
    if not decoded:
        raise ValueError("otpauth URI has no secret")

    if ":" in raw_label:
        prefix = raw_label.split(":", 1)[0].strip()
        prefix = prefix or None
    else:
        prefix = None
    query_issuer = (params.get("issuer") or "").strip() or None
    issuer = query_issuer or prefix
    if prefix is not None and (query_issuer is None or query_issuer.lower() == prefix.lower()):
        label = raw_label.split(":", 1)[1].strip()
    else:
        label = raw_label.strip()
    if not label and issuer is None:
        raise ValueError("otpauth URI has no label or issuer")

    algorithm = ((params.get("algorithm") or "").strip() or _DEFAULT_ALGORITHM).upper().replace("-", "")
    digits_text = (params.get("digits") or "").strip()
    period_text = (params.get("period") or "").strip()
    counter_text = (params.get("counter") or "").strip()
    try:
        digits = int(digits_text) if digits_text else _DEFAULT_DIGITS
    except ValueError:
        raise ValueError("otpauth digits is not a number")
    try:
        period = int(period_text) if period_text else _DEFAULT_PERIOD
    except ValueError:
        raise ValueError("otpauth period is not a number")
    counter: int | None = None
    if counter_text:
        try:
            counter = int(counter_text)
        except ValueError:
            raise ValueError("otpauth counter is not a number")

    if digits not in (6, 7, 8):
        raise ValueError(f"digits must be 6..8, was {digits}")
    if algorithm not in ("SHA1", "SHA256", "SHA512"):
        raise ValueError("unsupported algorithm")
    if period not in range(1, MAX_PERIOD + 1):
        raise ValueError(f"period must be 1..{MAX_PERIOD} seconds, was {period}")
    if kind == "hotp":
        if counter is None:
            raise ValueError("hotp URI missing counter")
        if counter < 0:
            raise ValueError("hotp counter must not be negative")
        counter_value: int | None = counter
    else:
        counter_value = None
    return OtpAccount(kind, label, issuer, algorithm, digits, period, counter_value, decoded)


def serialize(account: OtpAccount) -> str:
    out = [SCHEME + "://" + account.type + "/"]
    if account.issuer is not None:
        out.append(_percent_encode(account.issuer) + ":")
    out.append(_percent_encode(account.label))
    out.append("?secret=" + base32.encode(account.secret))
    if account.issuer is not None:
        out.append("&issuer=" + quote(account.issuer, safe=""))
    out.append("&algorithm=" + account.algorithm)
    out.append("&digits=" + str(account.digits))
    if account.type == "totp":
        out.append("&period=" + str(account.period))
    if account.counter is not None:
        out.append("&counter=" + str(account.counter))
    return "".join(out)
