"""Ring fallback order (port of audio/RingCandidates.kt).

An alarm that makes no noise is not an alarm: the chosen tone is tried
first, then the system default, then the notification default. Blank
entries are dropped and duplicates collapse.
"""

from __future__ import annotations


def ring_candidates(chosen: str | None, default_alarm: str | None,
                    default_notification: str | None) -> list[str]:
    out: list[str] = []
    for uri in (chosen, default_alarm, default_notification):
        if uri is None or not uri.strip():
            continue
        if uri not in out:
            out.append(uri)
    return out
