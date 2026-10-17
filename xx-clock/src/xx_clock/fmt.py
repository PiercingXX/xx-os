"""Time formatting helpers (port of util/Fmt.kt)."""

from __future__ import annotations

import locale
import time as _time


def until(now_ms: int, target_ms: int) -> str:
    seconds = max(0, (target_ms - now_ms) // 1000)
    days, seconds = divmod(seconds, 86_400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _ = divmod(seconds, 60)
    parts = ""
    if days > 0:
        parts += f"{days} d "
    if hours > 0 or days > 0:
        parts += f"{hours} h "
    parts += f"{minutes} m"
    return parts


def format_time(epoch_ms: int) -> str:
    local = _time.localtime(epoch_ms / 1000)
    try:
        conv = locale.localeconv()
    except Exception:
        conv = {}
    if conv.get("am") or conv.get("pm"):
        rendered = _time.strftime("%I:%M %p", local).lstrip("0")
    else:
        rendered = _time.strftime("%H:%M", local)
    return rendered
