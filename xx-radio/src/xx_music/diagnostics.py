"""Diagnostics: one copyable text block plus the shared redaction.

The block carries the app version, the engine versions in use, one status
word per capability, and the last error kinds with timestamps. Everything
leaving this module is redacted: use :func:`redact_text` before logging
anything that may carry headers, cookies or tokens, and
:func:`redact_mapping` for header dicts.

Generic on purpose: patterns cover ``cookie``/``authorization``/token and
hash values wherever they appear, so no source-specific helper is needed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

#: Header names whose values never leave this module unmasked.
MASKED_HEADERS = frozenset({
    "cookie", "authorization", "x-goog-page-hints",
    "x-goog-auth", "token", "access-token", "refresh-token",
    "password", "secret", "client-secret",
})

_SECRET_NAMES = (r"cookie|authorization|x-goog-page-hints|x-goog-auth|"
                 r"access[_-]?token|refresh[_-]?token|id[_-]?token|"
                 r"password|client[_-]?secret|(?<![a-z-])secret(?![a-z-])|"
                 r"(?<![a-z-])token(?![a-z-])")
#: ``name: value`` (headers): the value runs to the next ``;`` or the
#: end of the line, so structure around it stays debuggable.
_MASKED_COLON_RE = re.compile(
    rf"(?i)({_SECRET_NAMES})\s*:\s*(?!\*\*\*)\S[^;\n]*")
#: ``name=value`` (flags, query strings): one token, so words after the
#: value (hash markers, surrounding prose) survive for debugging.
_MASKED_EQ_RE = re.compile(rf"(?i)({_SECRET_NAMES})\s*=\s*[^\s;,&\"\n]+")
#: ``"name": "value"`` (stored credential dicts rendered into logs).
_JSON_SECRET_RE = re.compile(rf"(?i)(\"(?:{_SECRET_NAMES})\"\s*:\s*\")[^\"]*(\")")
#: Later pairs in one Cookie header (``; SAPISID=...`` past the first).
_COOKIE_PAIR_RE = re.compile(
    r"(?i)\b(sid|sapisid|ssid|hsid|apisid|sidcc|login_info|oauth_token)"
    r"\s*=\s*[^;\s,\"]+")
#: A bare ``token <value>`` with a credential-shaped value (has a digit);
#: plain words such as the helper's own name never match.
_BARE_TOKEN_RE = re.compile(r"(?i)\b(token)\s+(?=\S*[0-9])\S{6,}")
_SAPISID_RE = re.compile(r"(?i)(SAPISIDHASH\s+\d+_)[0-9a-f]+")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*")


def redact_mapping(headers: Mapping[str, str]) -> dict[str, str]:
    """Copy a header mapping with secret values masked, for logs."""
    redacted = dict(headers)
    for name in list(redacted):
        if name.lower() in MASKED_HEADERS:
            redacted[name] = "***"
    return redacted


def redact_text(text: str) -> str:
    """Mask cookie/authorization/token values and hash suffixes in text.

    Non-secret neighbours survive: ``x-goog-authuser: 0`` and user agents
    are kept, so redacted lines stay debuggable.
    """
    masked = _MASKED_COLON_RE.sub(lambda m: f"{m.group(1)}: ***", text or "")
    masked = _MASKED_EQ_RE.sub(lambda m: f"{m.group(1)}: ***", masked)
    masked = _JSON_SECRET_RE.sub(lambda m: f"{m.group(1)}***{m.group(2)}",
                                 masked)
    masked = _COOKIE_PAIR_RE.sub(lambda m: f"{m.group(1)}=***", masked)
    masked = _BARE_TOKEN_RE.sub(lambda m: f"{m.group(1)}: ***", masked)
    masked = _SAPISID_RE.sub(lambda m: f"{m.group(1)}***", masked)
    return _BEARER_RE.sub(lambda m: f"{m.group(1)}***", masked)


@dataclass(frozen=True)
class ErrorRecord:
    """One past engine failure for the diagnostics block."""

    when: str  # ISO timestamp text
    capability: str
    kind: str


def collect(app_name: str, app_version: str, *,
            engine_lines: Iterable[str] = (),
            capabilities: Mapping[str, str] | None = None,
            errors: Iterable[ErrorRecord] = (),
            max_errors: int = 20) -> str:
    """Build the copyable diagnostics block, fully redacted."""
    lines = [f"{app_name} {app_version}"]
    engine_lines = list(engine_lines)
    if engine_lines:
        lines.append("Engine:")
        lines.extend(f"  {line}" for line in engine_lines)
    if capabilities:
        lines.append("Capabilities:")
        for capability in sorted(capabilities):
            lines.append(f"  {capability}: {capabilities[capability]}")
    lines.append(f"Recent errors (last {max_errors}):")
    records = list(errors)[-max_errors:] if max_errors > 0 else []
    if records:
        for record in records:
            lines.append(f"  {record.when} {record.capability} {record.kind}")
    else:
        lines.append("  (none)")
    return redact_text("\n".join(lines) + "\n")
