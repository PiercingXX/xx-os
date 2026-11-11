"""One number-display rule for every surface that shows a number.

Port of Android's ``PhoneDisplay`` + ``NumberDisplay`` (identical in
xx-contacts, xx-dialer and TxxT): a valid number from the user's own
country reads in national format (``(614) 555-0142`` in the US), a valid
number from another country reads in international format
(``+44 20 7946 0958``), and anything that is not a complete valid number
(short codes, ``911``, alphanumeric senders, ``*``/``#`` codes, digits
still being typed) is shown as it came in, or with partial grouping.

Display only: storage, matching, dialing, vCards, D-Bus and backups keep
the stored number unchanged.

Pure module: no GTK, no blocking calls on import. The ``phonenumbers``
engine (Google libphonenumber, the same engine the dialer variant of the
Android rule runs on) is imported lazily, so a missing library degrades
to showing the number as it came in instead of raising.
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict

#: Region used when nothing names the user's country; only ``+`` numbers
#: parse under it. Mirrors ``PhoneDisplay.UNKNOWN_COUNTRY``.
UNKNOWN_COUNTRY = "ZZ"

#: How long the auto-resolved user country is trusted; roaming can change
#: the network country. Mirrors ``NumberDisplay``'s 60 s TTL.
COUNTRY_TTL_S = 60.0

_CACHE_SIZE = 256

_NUMBER_PUNCTUATION = frozenset("+-()./")

_MCC_TO_ISO: dict[str, str] = {
    "202": "GR", "204": "NL", "206": "BE", "208": "FR", "212": "CY",
    "214": "ES", "216": "HU", "219": "HR", "220": "RS", "222": "IT",
    "226": "RO", "228": "CH", "230": "CZ", "231": "SK", "232": "AT",
    "234": "GB", "235": "GB", "238": "DK", "240": "SE", "242": "NO",
    "244": "FI", "246": "LT", "247": "LV", "248": "EE", "250": "RU",
    "255": "UA", "257": "BY", "260": "PL", "262": "DE", "268": "PT",
    "270": "LU", "272": "IE", "274": "IS", "284": "BG", "286": "TR",
    "293": "SI",
    "302": "CA",
    "310": "US", "311": "US", "312": "US", "313": "US", "314": "US",
    "315": "US", "316": "US",
    "334": "MX",
    "404": "IN", "405": "IN",
    "420": "SA", "424": "AE", "425": "IL",
    "440": "JP", "441": "JP", "450": "KR", "460": "CN", "466": "TW",
    "505": "AU", "510": "ID", "515": "PH", "520": "TH", "525": "SG",
    "530": "NZ",
    "602": "EG",
    "724": "BR",
}

_lock = threading.Lock()
_shown: OrderedDict[tuple[str, str], str] = OrderedDict()
_auto_country: tuple[str, float] | None = None

_phonenumbers = None
_phonenumbers_tried = False


def _engine():
    """The ``phonenumbers`` module, or None when it is not installed."""
    global _phonenumbers, _phonenumbers_tried
    if not _phonenumbers_tried:
        try:
            import phonenumbers as _lib
        except Exception:
            _lib = None
        _phonenumbers = _lib
        _phonenumbers_tried = True
    return _phonenumbers


def mcc_to_iso() -> dict[str, str]:
    """The MCC to ISO 3166 table (a copy); every value is a region the
    ``phonenumbers`` engine knows."""
    return dict(_MCC_TO_ISO)


def _iso_from_operator(code: str | None) -> str:
    """ISO for a ModemManager operator code (``MCCMNC`` like ``310260``),
    or ``""`` when it names no known country."""
    if not code:
        return ""
    digits = "".join(c for c in code.strip() if c.isdigit())
    if len(digits) < 3:
        return ""
    return _MCC_TO_ISO.get(digits[:3], "")


def _iso_from_locale(environ) -> str:
    """Territory from ``LC_ALL``/``LC_MESSAGES``/``LANG`` (``en_US.UTF-8``
    to ``US``), or ``""`` when none names one."""
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        try:
            value = environ.get(key, "")
        except Exception:
            continue
        token = value.split(".")[0].split("@")[0]
        parts = token.replace("-", "_").split("_")
        for part in reversed(parts):
            iso = part.strip().upper()
            if len(iso) == 2 and iso.isalpha():
                return iso
            if part.strip():
                break
    return ""


def country_iso(network: str | None, sim: str | None,
                locale: str | None) -> str:
    """The user's country: the network first, then the SIM, then the
    locale. Blank or malformed values are skipped. Upper case, or ``""``
    when none of the three names a country."""
    for candidate in (network, sim, locale):
        iso = (candidate or "").strip().upper()
        if len(iso) == 2 and iso.isalpha():
            return iso
    return ""


def _auto_sources() -> tuple[str | None, str | None]:
    """``(operator_code, sim_operator)`` from ModemManager, ``(None, None)``
    when it is unreachable. Blocking: call only from a worker thread,
    never the GTK main loop."""
    try:
        from gi.repository import Gio
    except ImportError:
        return (None, None)
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except Exception:
        return (None, None)
    if bus is None:
        return (None, None)
    try:
        objects = bus.call_sync(
            "org.freedesktop.ModemManager1",
            "/org/freedesktop/ModemManager1",
            "org.freedesktop.DBus.ObjectManager",
            "GetManagedObjects", None, None,
            Gio.DBusCallFlags.NONE, 2_000, None,
        ).unpack()[0]
    except Exception:
        return (None, None)
    network = sim = None
    try:
        things = dict(objects)
    except Exception:
        return (None, None)
    sim_paths: list[str] = []
    for path, ifaces in things.items():
        try:
            props = dict(dict(ifaces).get(
                "org.freedesktop.ModemManager1.Modem3gpp", {}))
        except Exception:
            continue
        if not props:
            continue
        code = props.get("OperatorCode")
        if network is None and code:
            network = str(code.unpack() if hasattr(code, "unpack")
                          else code) or None
        try:
            modem_props = dict(dict(ifaces).get(
                "org.freedesktop.ModemManager1.Modem", {}))
        except Exception:
            modem_props = {}
        sim_path = modem_props.get("Sim")
        if sim_path is not None:
            try:
                sim_paths.append(str(sim_path.unpack()
                                     if hasattr(sim_path, "unpack")
                                     else sim_path))
            except Exception:
                pass
    for path in sim_paths:
        try:
            sim_props = dict(dict(things.get(path, {})).get(
                "org.freedesktop.ModemManager1.Sim", {}))
        except Exception:
            continue
        ident = sim_props.get("OperatorIdentifier")
        if ident:
            sim = str(ident.unpack() if hasattr(ident, "unpack")
                      else ident) or None
            break
    return (network, sim)


def user_country(operator_code: str | None = None,
                 sim_operator: str | None = None,
                 environ=None, _now=None) -> str:
    """The user's country as an upper-case ISO 3166 code, or ``""``.

    The network's MCC first, then the SIM's, then the locale territory.
    ``None`` for a source means "read the real one"; an explicit string
    (including ``""``) is used as-is, which is also the seam tests drive.
    Fully automatic resolutions are cached about 60 s (roaming); explicit
    values are already resolved, so they bypass the cache.
    """
    if operator_code is None and sim_operator is None and environ is None:
        clock = _now or time.monotonic
        with _lock:
            cached = _auto_country
        if cached is not None:
            value, at = cached
            try:
                fresh = clock() - at < COUNTRY_TTL_S
            except Exception:
                fresh = True
            if fresh:
                return value
        operator_code, sim_operator = _auto_sources()
        value = country_iso(_iso_from_operator(operator_code),
                            _iso_from_operator(sim_operator),
                            _iso_from_locale(os.environ))
        with _lock:
            globals()["_auto_country"] = (value, clock())
        return value
    if environ is None:
        environ = os.environ
    return country_iso(_iso_from_operator(operator_code),
                       _iso_from_operator(sim_operator),
                       _iso_from_locale(environ))


def display_country(fallback: str | None = None) -> str:
    """Country for display surfaces: the live user country (network, then
    SIM, then locale), else the app's configured region, else ``""``
    (unknown — only ``+`` numbers format under it)."""
    return user_country() or country_iso(fallback, None, None)


def _looks_like_number(text: str) -> bool:
    """Digits plus the punctuation people write numbers with; letters,
    ``*``, ``#``, ``@`` and pauses are not."""
    return any(c.isdigit() for c in text) and all(
        c.isdigit() or c.isspace() or c in _NUMBER_PUNCTUATION
        for c in text)


def _country_or_unknown(iso: str | None) -> str:
    return country_iso(iso, None, None) or UNKNOWN_COUNTRY


def _to_e164(number: str, iso: str) -> str | None:
    """E.164 for a valid number, else None."""
    lib = _engine()
    if lib is None:
        return None
    try:
        parsed = lib.parse(number, iso)
    except Exception:
        return None
    try:
        if lib.is_valid_number(parsed):
            return lib.format_number(parsed, lib.PhoneNumberFormat.E164)
    except Exception:
        return None
    return None


def _group(number: str, iso: str) -> str | None:
    """The number grouped in its original form (national stays national,
    ``+`` stays ``+``), else None."""
    lib = _engine()
    if lib is None:
        return None
    try:
        parsed = lib.parse(number, iso, keep_raw_input=True)
        return lib.format_in_original_format(parsed, iso)
    except Exception:
        return None


def _national_form(e164: str, iso: str) -> str | None:
    """The digits a caller in ``iso`` would dial for ``e164``, or None
    when the number belongs to another country. Country calling codes
    are one to three digits; cutting the longest first means the first
    cut that parses back to the same number is the home code (a cut
    longer than the home code leaves too few digits to round-trip). The
    trunk-prefixed form (``020 7946 0958``, ``030 123456``) is tried
    first because that is how most countries print numbers nationally."""
    digits = e164.removeprefix("+")
    for code_length in (3, 2, 1):
        if len(digits) <= code_length:
            continue
        significant = digits[code_length:]
        for candidate in ("0" + significant, significant):
            if _to_e164(candidate, iso) == e164:
                return candidate
    return None


def _format_number(text: str, iso: str) -> str | None:
    e164 = _to_e164(text, iso)
    if e164 is None:
        return _group(text, iso)
    return _group(_national_form(e164, iso) or e164, iso)


def display(raw: str | None, country: str | None = None) -> str:
    """``raw`` as it should read for a user in ``country`` (ISO 3166
    alpha-2, any case; the user's own country when omitted)."""
    text = (raw or "").strip()
    if not _looks_like_number(text):
        return text
    iso = _country_or_unknown(country if country is not None
                              else user_country())
    key = (iso, text)
    with _lock:
        hit = _shown.get(key)
        if hit is not None:
            _shown.move_to_end(key)
            return hit
    try:
        shown = _format_number(text, iso)
    except Exception:
        shown = None
    if not shown or not shown.strip():
        shown = text
    with _lock:
        _shown[key] = shown
        while len(_shown) > _CACHE_SIZE:
            _shown.popitem(last=False)
    return shown


def as_you_type(text: str, country: str | None = None) -> str:
    """Keypad entry grouped as it is typed: ``614555`` reads ``614-555``.
    Star and hash codes, pauses and anything else that is not a plain
    number stay as typed."""
    entered = text if isinstance(text, str) else ""
    if not _looks_like_number(entered):
        return entered
    lib = _engine()
    if lib is None:
        return entered
    try:
        typing = lib.AsYouTypeFormatter(
            _country_or_unknown(country if country is not None
                                else user_country()))
        out = entered
        for char in entered:
            if char.isdigit() or char == "+":
                out = typing.input_digit(char)
        shown = out
    except Exception:
        return entered
    return shown if shown and shown.strip() else entered


# -- matching ---------------------------------------------------------------
#
# One "same number?" rule for every surface that compares two numbers
# (DnD exceptions, caller ID, the SMS blocklist and starred senders, the
# contacts lookup index). Port of libcutiephonenumber's ``locallyEqualTo``
# and GNOME Calls' contact query: libphonenumber's EXACT_MATCH counts, an
# NSN_MATCH counts only when nothing says the country codes disagree, and a
# shorter number that is just the tail of a longer one (SHORT_NSN_MATCH,
# the old "last 7 digits" rule) never counts: 614-555-0142 and
# 312-555-0142 are two different people.

#: A number with at most this many digits is a short code (``911``,
#: ``22395``): it matches only the same digits, never the tail of a longer
#: number.
SHORT_CODE_MAX_DIGITS = 6

#: Without the library a national number may still match an international
#: one by its tail, but only when the national side carries at least this
#: many digits and the extra digits fit a country calling code.
_FALLBACK_MIN_TAIL = 8
_FALLBACK_MAX_PREFIX = 3


def _match_country(country: str | None) -> str:
    """The region national numbers are read in for matching: ``country``
    when given, else the last auto-resolved user country, else the
    locale. Never blocks (no ModemManager call), so it is safe on the
    GTK main loop."""
    if country is not None:
        return country_iso(country, None, None)
    with _lock:
        cached = _auto_country
    if cached is not None and cached[0]:
        return cached[0]
    return _iso_from_locale(os.environ)


def _dial_form(text: str) -> str:
    """Digits, keeping a leading ``+`` (all libphonenumber needs)."""
    digits = "".join(c for c in text if c.isdigit())
    return ("+" + digits) if text.startswith("+") else digits


def _identity(text: str) -> str:
    """Comparison key for a non-number sender (``AMAZON``, ``*86``)."""
    return "".join(text.split()).casefold()


def _fallback_match(a: str, b: str) -> bool:
    """No-library rule: the same digits once the international prefix
    (``+``, ``00``) or a national trunk ``0`` is dropped, or a
    national number that is an international one minus a 1-3 digit
    country code. A bare 7-digit tail never matches."""
    def strip(form: str) -> tuple[str, bool]:
        if form.startswith("+"):
            return form[1:], True
        if form.startswith("00"):
            return form[2:], True
        return (form[1:] if form.startswith("0") else form), False

    da, intl_a = strip(a)
    db, intl_b = strip(b)
    if not da or not db:
        return False
    if da == db:
        return True
    if intl_a and intl_b:
        return False
    shorter, longer = (da, db) if len(da) <= len(db) else (db, da)
    extra = len(longer) - len(shorter)
    return (len(shorter) >= _FALLBACK_MIN_TAIL
            and 1 <= extra <= _FALLBACK_MAX_PREFIX
            and longer.endswith(shorter))


def _library_match(lib, a: str, b: str, iso: str) -> bool:
    match = lib.MatchType
    if iso:
        try:
            first = lib.parse(a, iso)
            second = lib.parse(b, iso)
        except Exception:
            first = second = None
        if first is not None and second is not None:
            # Both carry a country code now, so only EXACT_MATCH can
            # come back for the same number; NSN_MATCH would mean the
            # country codes differ.
            return lib.is_number_match(first, second) == match.EXACT_MATCH
    verdict = lib.is_number_match(a, b)
    if verdict == match.EXACT_MATCH:
        return True
    if iso:
        return False
    # No home region to read the national side in: an NSN_MATCH has
    # nothing contradicting it (Cutie's "both country codes unknown"),
    # and neither does a stored digits-only "16145550142".
    return verdict == match.NSN_MATCH or _fallback_match(a, b)


def numbers_match(a: str | None, b: str | None,
                  country: str | None = None) -> bool:
    """True when ``a`` and ``b`` are the same number for a user in
    ``country`` (ISO 3166 alpha-2; the user's country when omitted).

    ``+16145550142``, ``(614) 555-0142`` and ``1 614 555 0142`` match in
    the US; ``614-555-0142`` and ``312-555-0142`` never do, and neither
    do ``555-0142`` and either of them. Short codes (``22395``, ``911``)
    match only the same digits; alphanumeric senders and ``*``/``#``
    codes match only the same text, ignoring case and spaces. Never
    raises, never blocks: without the ``phonenumbers`` library a
    conservative digit rule stands in.
    """
    text_a = (a or "").strip() if isinstance(a, str) else ""
    text_b = (b or "").strip() if isinstance(b, str) else ""
    if not text_a or not text_b:
        return False
    if not (_looks_like_number(text_a) and _looks_like_number(text_b)):
        if _looks_like_number(text_a) or _looks_like_number(text_b):
            return False
        key = _identity(text_a)
        return bool(key) and key == _identity(text_b)
    form_a, form_b = _dial_form(text_a), _dial_form(text_b)
    digits_a, digits_b = form_a.lstrip("+"), form_b.lstrip("+")
    if digits_a == digits_b:
        return True
    if min(len(digits_a), len(digits_b)) <= SHORT_CODE_MAX_DIGITS:
        return False
    lib = _engine()
    if lib is not None:
        try:
            return bool(_library_match(lib, form_a, form_b,
                                       _match_country(country)))
        except Exception:
            pass
    return _fallback_match(form_a, form_b)


# -- DTMF -------------------------------------------------------------------

#: The sixteen DTMF tones ModemManager's Call.SendDtmf accepts.
DTMF_TONES = "0123456789*#ABCD"


def dtmf_tone(digit: str | None) -> str | None:
    """The single DTMF tone ``digit`` names (``a``-``d`` upper-cased,
    surrounding space ignored), or None for anything else: no ``+``,
    ``,``/``p`` pauses, letters or multi-character strings ever reach
    the modem. One rule for the shell's in-call keypad and xx-dialer."""
    tone = digit.strip().upper() if isinstance(digit, str) else ""
    return tone if len(tone) == 1 and tone in DTMF_TONES else None
