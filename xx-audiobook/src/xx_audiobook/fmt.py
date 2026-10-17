"""Display text for the UI (M3): times, speed, initials, progress lines.

Pure functions mirroring the Android ui/common formatters (formatMs,
formatDuration, speedText, the time-left line). No gi, no I/O.
"""

from __future__ import annotations

DAMAGED_LINE = "This book's audio is damaged or incomplete."


def format_ms(ms: int) -> str:
    """1:02:03 above an hour, else 2:03 (never negative)."""
    total = max(int(ms), 0) // 1000
    hours, rest = divmod(total, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def format_duration(seconds: float) -> str:
    """Book lengths: '9 h 12 min', '42 min', '< 1 min'."""
    total = int(max(seconds, 0.0)) // 60
    hours, minutes = divmod(total, 60)
    if hours and minutes:
        return f"{hours} h {minutes} min"
    if hours:
        return f"{hours} h"
    if minutes:
        return f"{minutes} min"
    return "< 1 min"


def speed_text(speed: float) -> str:
    """1×, 1.25×, 1.5× — trailing zeros dropped."""
    text = f"{speed:.2f}".rstrip("0").rstrip(".")
    return text + "×"


def scaled_ms(ms: int, speed: float) -> int:
    """Wall-clock time for ``ms`` of audio at ``speed``."""
    return int(max(ms, 0) / max(speed, 0.1))


def time_left_line(position_ms: int, duration_ms: int, speed: float,
                   chapter_end_ms: int | None = None) -> str:
    """'3:12:40 left · 12:05 in chapter', at the current speed."""
    if duration_ms <= 0:
        return ""
    parts = [format_ms(scaled_ms(duration_ms - position_ms, speed))
             + " left"]
    if chapter_end_ms is not None and chapter_end_ms > position_ms:
        parts.append(format_ms(scaled_ms(chapter_end_ms - position_ms,
                                         speed)) + " in chapter")
    line = " · ".join(parts)
    if abs(speed - 1.0) > 0.01:
        line += " at " + speed_text(speed)
    return line


def initials(title: str) -> str:
    """Two letters for a placeholder cover: first letters of the first
    two significant words ('The Long Way' -> 'LW')."""
    skip = {"the", "a", "an", "of", "and"}
    words = [w for w in "".join(
        ch if ch.isalnum() else " " for ch in title or "").split()]
    strong = [w for w in words if w.lower() not in skip] or words
    if not strong:
        return "XX"
    if len(strong) == 1:
        return strong[0][:2].upper()
    return (strong[0][0] + strong[1][0]).upper()


def rating_text(average: float | None, count: int) -> str:
    if average is None or count <= 0:
        return ""
    return f"★ {average:.1f}"


def percent(fraction: float) -> int:
    return int(round(min(max(fraction, 0.0), 1.0) * 100))


def books_count(count: int) -> str:
    return f"{count} book" if count == 1 else f"{count} books"


def sleep_label(remaining_ms: int, end_of_chapter: bool) -> str:
    """The Sleep cell: 'Sleep' when off, else the countdown."""
    if remaining_ms <= 0 and not end_of_chapter:
        return "Sleep"
    if remaining_ms <= 0:
        return "Chapter"
    return format_ms(remaining_ms)


def description_paragraphs(text: str) -> list[str]:
    """'\\n'-separated paragraphs, blank ones dropped (descriptionParagraphs)."""
    return [p.strip() for p in (text or "").replace("\r", "").split("\n")
            if p.strip()]
