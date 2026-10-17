"""Trash expiry math (port of ``ui/trash/TrashMath.kt``).

Keep's rule verbatim (D9): trash expires after 7 days, and expiry is the
only true deletion.
"""

from __future__ import annotations

EXPIRY_DAYS = 7
DAY_MS = 24 * 60 * 60 * 1000
EXPIRY_MS = EXPIRY_DAYS * DAY_MS


def days_remaining(trashed_at_ms: int | None, now_ms: int) -> int | None:
    if trashed_at_ms is None:
        return None
    remaining = trashed_at_ms + EXPIRY_MS - now_ms
    if remaining <= DAY_MS:
        return 0
    days = (remaining + DAY_MS - 1) // DAY_MS
    return min(days, EXPIRY_DAYS)
