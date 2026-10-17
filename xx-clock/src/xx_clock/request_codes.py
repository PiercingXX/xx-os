"""Stable schedule keys (port of ExactScheduler.requestCode).

Android packs the entry kind into the high bits of a PendingIntent request
code above a hi-xor-lo fold of the long id, so ids 2^32 apart can never
overwrite each other's intents. The daemon reuses the same packing for its
GLib source bookkeeping: (kind, id) pairs that truncate identically still
map to distinct non-negative keys.
"""

from __future__ import annotations

REQ_KIND_ALARM = 1
REQ_KIND_TIMER = 2
REQ_KIND_SHOW = 3

_REQ_CODE_ID_MASK = (1 << 29) - 1


def request_code(kind: int, entry_id: int) -> int:
    folded = ((entry_id ^ (entry_id >> 32)) & 0xFFFFFFFF)
    return (kind << 29) | (folded & _REQ_CODE_ID_MASK)
