"""Android BackupJson v1 backup: byte-compatible read/write.

Format mirrors
``app/src/main/.../settings/BackupJson.kt``: ``app: "xx-calendar"``,
``version: 1``, the settings object with the DataStore preference names
(which double as documentation), and the sigil map as
``{calendarId, accountName, tier}`` rows with tier ordinals. The reader
is total: missing keys fall back, unknown keys are ignored, and corrupt
input reports Corrupt without applying anything. Restore is wholesale
replacement, so callers surface the consequence before applying.

Pure Python, no GTK/D-Bus.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from xx_calendar.app_config import DEFAULTS, parse_settings
from xx_calendar.models import CalendarKey

APP_TAG = "calendar"
FORMAT_VERSION = 1

MAX_BACKUP_BYTES = 10 * 1024 * 1024

SETTINGS_KEYS: tuple[str, ...] = (
    "default_view",
    "start_day_of_week",
    "week_numbers",
    "show_declined",
    "dim_past",
    "density",
    "default_duration_min",
    "default_notification_min",
    "all_day_notify_hour",
    "all_day_notify_days_before",
    "lock_screen_title",
    "daily_agenda",
    "heads_up",
    "hide_auto_added",
    "background",
    "font",
    "text_size_scale",
    "auto_added_filter_mode",
)

_MAX_TIER_ORDINAL = 5
_MAX_DEPTH = 64


@dataclass(frozen=True)
class BackupSnapshot:
    settings: dict = field(default_factory=dict)
    sigils: dict = field(default_factory=dict)


class BackupRead:
    """Base for the total reader result."""


@dataclass(frozen=True)
class Ok(BackupRead):
    snapshot: BackupSnapshot


@dataclass(frozen=True)
class Corrupt(BackupRead):
    reason: str = "unreadable backup"


CORRUPT = Corrupt()


def json_escape(value: str) -> str:
    """Escape a string exactly like BackupJson.jsonEscape."""
    out: list[str] = []
    for char in value:
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif char == "\b":
            out.append("\\b")
        elif char == "\f":
            out.append("\\f")
        elif ord(char) < 0x20:
            out.append("\\u%04x" % ord(char))
        else:
            out.append(char)
    return "".join(out)


def _float_to_json(value: object) -> str:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "1.0"
    if number != number or number in (float("inf"), float("-inf")):
        return "1.0"
    text = repr(number)
    if "." not in text and "e" not in text and "E" not in text:
        text += ".0"
    return text


def _bool_text(value: object, fallback: bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "true" if fallback else "false"


def _int_text(value: object, fallback: int) -> str:
    if isinstance(value, bool):
        return str(fallback)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(fallback)


def _calendar_id_text(calendar_id: object) -> str:
    if isinstance(calendar_id, bool):
        return '"%s"' % json_escape(str(calendar_id))
    if isinstance(calendar_id, int):
        return str(calendar_id)
    text = str(calendar_id)
    try:
        if str(int(text)) == text:
            return text
    except (TypeError, ValueError):
        pass
    return '"%s"' % json_escape(text)


def _tier_ordinal(tier: object) -> int | None:
    if isinstance(tier, bool):
        return None
    if isinstance(tier, int) and 0 <= tier <= _MAX_TIER_ORDINAL:
        return tier
    if isinstance(tier, float) and tier.is_integer() \
            and 0 <= int(tier) <= _MAX_TIER_ORDINAL:
        return int(tier)
    try:
        from xx_calendar.models import SigilTier

        if isinstance(tier, SigilTier):
            ordinal = list(SigilTier).index(tier)
            if 0 <= ordinal <= _MAX_TIER_ORDINAL:
                return ordinal
    except Exception:
        pass
    return None


def write(settings: dict, sigils: dict) -> str:
    """Serialize settings + sigils deterministically, Android byte-compatible."""
    full = parse_settings(settings if isinstance(settings, dict) else {})
    parts: list[str] = []
    parts.append("{\n")
    parts.append('  "app": "%s",\n' % APP_TAG)
    parts.append("  \"version\": %d,\n" % FORMAT_VERSION)
    parts.append('  "settings": {\n')
    entries = [
        '"default_view": "%s"' % json_escape(str(full["default_view"])),
        '"start_day_of_week": "%s"' % json_escape(str(full["start_day_of_week"])),
        '"week_numbers": %s' % _bool_text(full.get("week_numbers"),
                                          DEFAULTS["week_numbers"]),
        '"show_declined": %s' % _bool_text(full.get("show_declined"),
                                          DEFAULTS["show_declined"]),
        '"dim_past": %s' % _bool_text(full.get("dim_past"),
                                      DEFAULTS["dim_past"]),
        '"density": "%s"' % json_escape(str(full["density"])),
        '"default_duration_min": %s'
        % _int_text(full.get("default_duration_min"),
                    DEFAULTS["default_duration_min"]),
        '"default_notification_min": %s'
        % _int_text(full.get("default_notification_min"),
                    DEFAULTS["default_notification_min"]),
        '"all_day_notify_hour": %s'
        % _int_text(full.get("all_day_notify_hour"),
                    DEFAULTS["all_day_notify_hour"]),
        '"all_day_notify_days_before": %s'
        % _int_text(full.get("all_day_notify_days_before"),
                    DEFAULTS["all_day_notify_days_before"]),
        '"lock_screen_title": %s' % _bool_text(full.get("lock_screen_title"),
                                               DEFAULTS["lock_screen_title"]),
        '"daily_agenda": %s' % _bool_text(full.get("daily_agenda"),
                                          DEFAULTS["daily_agenda"]),
        '"heads_up": %s' % _bool_text(full.get("heads_up"),
                                      DEFAULTS["heads_up"]),
        '"hide_auto_added": %s' % _bool_text(full.get("hide_auto_added"),
                                             DEFAULTS["hide_auto_added"]),
        '"background": "%s"' % json_escape(str(full["background"])),
        '"font": "%s"' % json_escape(str(full["font"])),
        '"text_size_scale": %s' % _float_to_json(full.get("text_size_scale")),
        '"auto_added_filter_mode": "%s"'
        % json_escape(str(full["auto_added_filter_mode"])),
    ]
    parts.append("    " + ",\n    ".join(entries) + "\n")
    parts.append("  },\n")
    rows: list[str] = []
    items = list((sigils or {}).items()) if isinstance(sigils, dict) else []
    items.sort(key=lambda kv: (str(kv[0].calendar_id)
                               if hasattr(kv[0], "calendar_id")
                               else str(kv[0]),
                               str(getattr(kv[0], "account_name", ""))))
    for key, tier in items:
        ordinal = _tier_ordinal(tier)
        if ordinal is None:
            continue
        if isinstance(key, CalendarKey):
            calendar_id = key.calendar_id
            account = key.account_name
        elif isinstance(key, (tuple, list)) and len(key) == 2:
            calendar_id, account = key[0], key[1]
        else:
            continue
        if not isinstance(account, str):
            continue
        rows.append('    {"calendarId": %s, "accountName": "%s", "tier": %d}'
                    % (_calendar_id_text(calendar_id),
                       json_escape(account), ordinal))
    if not rows:
        parts.append('  "sigils": []\n')
    else:
        parts.append('  "sigils": [\n')
        parts.append(",\n".join(rows) + "\n  ]\n")
    parts.append("}\n")
    return "".join(parts)


write_backup = write


def _check_depth(node: object, depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        raise ValueError("nesting too deep")
    if isinstance(node, dict):
        for value in node.values():
            _check_depth(value, depth + 1)
    elif isinstance(node, list):
        for value in node:
            _check_depth(value, depth + 1)


def _parse_sigils(raw: object) -> dict:
    out: dict[CalendarKey, int] = {}
    if raw is None:
        return out
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("calendarId")
        account = item.get("accountName")
        raw_tier = item.get("tier")
        if isinstance(raw_id, bool):
            continue
        if isinstance(raw_id, int):
            calendar_id = str(raw_id)
        elif isinstance(raw_id, str) and raw_id != "":
            calendar_id = raw_id
        else:
            continue
        if not isinstance(account, str):
            continue
        ordinal = _tier_ordinal(raw_tier)
        if ordinal is None:
            continue
        out[CalendarKey(str(calendar_id), account)] = ordinal
    return out


def read(text: str) -> BackupRead:
    """Parse backup *text*; Ok(snapshot) or Corrupt, never half-applied."""
    if not isinstance(text, (str, bytes)):
        return CORRUPT
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8-sig")
        except ValueError:
            return CORRUPT
    else:
        if text.startswith("\ufeff"):
            text = text[1:]
    if not text.strip():
        return CORRUPT
    try:
        root = json.loads(text)
    except (ValueError, RecursionError):
        return CORRUPT
    except Exception:
        return CORRUPT
    if not isinstance(root, dict):
        return CORRUPT
    try:
        _check_depth(root)
    except (ValueError, RecursionError):
        return CORRUPT
    for key in list(root.keys()):
        if key not in ("app", "version", "settings", "sigils"):
            continue
    settings_raw = root.get("settings")
    if isinstance(settings_raw, dict):
        settings = parse_settings(settings_raw)
    else:
        settings = parse_settings({})
    try:
        sigils = _parse_sigils(root.get("sigils"))
    except Exception:
        return CORRUPT
    if root.keys() - {"app", "version", "settings", "sigils"}:
        pass
    return Ok(BackupSnapshot(settings=settings, sigils=sigils))


parse_backup = read
read_backup = read


def read_bounded_text(path: object, max_bytes: int = MAX_BACKUP_BYTES) -> str:
    """Read a picked backup file in fixed chunks, aborting past *max_bytes*."""
    name = str(path)
    total = 0
    chunks: list[bytes] = []
    with open(name, "rb") as handle:
        while True:
            piece = handle.read(64 * 1024)
            if not piece:
                break
            total += len(piece)
            if total > max_bytes:
                raise ValueError("file too large")
            chunks.append(piece)
    raw = b"".join(chunks)
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def collect_sigils(store) -> dict[CalendarKey, int]:
    """Current sigil map from the store's calendars table."""
    out: dict[CalendarKey, int] = {}
    for record in store.list_calendars():
        try:
            tier = int(record.sigil_tier)
        except (TypeError, ValueError):
            continue
        if 0 <= tier <= _MAX_TIER_ORDINAL:
            out[CalendarKey(str(record.id),
                            str(getattr(record, "account_name", "")))] = tier
    return out


def apply_sigils(store, sigils: dict) -> None:
    """Wholesale-replace sigil tiers; unknown calendars are ignored."""
    wanted: dict[tuple[str, str], int] = {}
    for key, tier in (sigils or {}).items():
        ordinal = _tier_ordinal(tier)
        if ordinal is None:
            continue
        if isinstance(key, CalendarKey):
            wanted[(str(key.calendar_id), str(key.account_name))] = ordinal
        elif isinstance(key, (tuple, list)) and len(key) == 2:
            wanted[(str(key[0]), str(key[1]))] = ordinal
    from xx_calendar.store import CalendarRecord

    for record in store.list_calendars():
        match = wanted.get((str(record.id),
                            str(getattr(record, "account_name", ""))))
        if match is None:
            match = next((tier for (cid, _acct), tier in wanted.items()
                          if cid == str(record.id)), None)
        fresh = CalendarRecord(
            id=record.id, display_name=record.display_name,
            account_name=record.account_name, color=record.color,
            is_visible=record.is_visible, is_writable=record.is_writable,
            sigil_tier=int(match) if match is not None else 0)
        store.update_calendar(fresh)


def export_backup(store, settings: dict) -> str:
    """Serialize the live store sigils plus *settings* to a backup string."""
    return write(settings, collect_sigils(store))


def restore(store, settings_path: object, text: str) -> BackupSnapshot:
    """Wholesale-restore settings + sigils; Corrupt raises, writes nothing."""
    parsed = read(text)
    if not isinstance(parsed, Ok):
        raise ValueError("unreadable backup — nothing changed")
    snapshot = parsed.snapshot
    from xx_calendar.app_config import save_settings

    save_settings(snapshot.settings, settings_path)  # type: ignore[arg-type]
    apply_sigils(store, snapshot.sigils)
    return snapshot


def write_backup_file(path: object, settings: dict, sigils: dict) -> str:
    """Atomically write a backup document at mode 0600."""
    from xx_calendar.store import ensure_private_dir, fix_file_mode, private_umask

    name = str(path)
    ensure_private_dir(os.path.dirname(os.path.abspath(name)))
    text = write(settings, sigils)
    tmp = name + ".tmp"
    with private_umask():
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    fix_file_mode(tmp)
    os.replace(tmp, name)
    fix_file_mode(name)
    return name
