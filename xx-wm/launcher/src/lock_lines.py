"""Pure lock-screen decisions (GTK-free, unit-testable headlessly):
notification-line formatting, the swipe-to-unlock rule and the lockout
resume/clock-jump rule.

Kept in its own module so the lock screen's logic can be tested
without PyGObject: lock_screen.py imports gi at module top for the GTK window,
which would otherwise force every pure-logic test to skip on a headless host.
"""

from __future__ import annotations

_FAIL_THRESHOLD = 5     # wrong attempts before first lockout

# Swipe-to-unlock (Lomiri EdgeDragEvaluator): a real upward drag, not a
# velocity spike. Travel must cover 10% of the surface height and be
# mostly vertical; the speed needed falls linearly from a flick at zero
# travel to nothing at half the height, so a long slow drag also counts.
SWIPE_MIN_TRAVEL_FRAC = 0.10
SWIPE_VERTICAL_RATIO = 1.5      # |dy| must exceed 1.5 x |dx|
SWIPE_FLICK_SPEED = 560.0       # px/s needed at zero travel (~70 gu/s)
SWIPE_FULL_TRAVEL_FRAC = 0.5    # travel at which no speed is needed
SWIPE_FALLBACK_HEIGHT = 640     # unallocated surface: the smallest target
SWIPE_VELOCITY_WINDOW_S = 0.1   # release speed: last 100 ms of samples


def lock_screen_lines(notifications: list[tuple[str, str]], mode: str,
                      dnd_active: bool) -> list[str]:
    """Lines shown on the lock surface (design.md "Lock screen"): app name +
    summary only, `count` collapses to one line, hidden entirely while DnD
    is active or the mode is off."""
    if dnd_active or mode == 'off' or not notifications:
        return []
    if mode == 'count':
        n = len(notifications)
        return [f'{n} notification{"s" if n != 1 else ""}']
    return [
        ' — '.join(part for part in (app.strip(), summary.strip()) if part)
        for app, summary in notifications
        if app.strip() or summary.strip()
    ]


def _lockout_secs(fail_count: int) -> int:
    """Seconds to lock the keypad after fail_count total wrong attempts."""
    if fail_count < _FAIL_THRESHOLD:
        return 0
    # 30s per attempt beyond the threshold, capped at 5 min
    return min((fail_count - _FAIL_THRESHOLD + 1) * 30, 300)


def swipe_unlocks(dx: float, dy: float, velocity_y: float,
                  height: float) -> bool:
    """Whether a finished drag (offset dx/dy from its start, release
    velocity_y in px/s, negative = upward) unlocks / reveals the keypad.

    Needs upward travel of at least 10% of height, mostly vertical motion
    (|dy| > 1.5 x |dx|), and an upward release speed of at least
    SWIPE_FLICK_SPEED x (1 - travel / (height / 2)) — so a 10 px pocket
    flick never unlocks however fast, while a slow drag past half the
    screen always does.
    """
    if height <= 0:
        height = SWIPE_FALLBACK_HEIGHT
    travel = -dy
    if travel < SWIPE_MIN_TRAVEL_FRAC * height:
        return False
    if travel <= SWIPE_VERTICAL_RATIO * abs(dx):
        return False
    full = SWIPE_FULL_TRAVEL_FRAC * height
    required = SWIPE_FLICK_SPEED * max(0.0, 1.0 - travel / full)
    return max(0.0, -velocity_y) >= required


def release_velocity(samples: list[tuple[float, float]],
                     window_s: float = SWIPE_VELOCITY_WINDOW_S) -> float:
    """Vertical speed (px/s) at release from (time_s, y) drag samples,
    measured over the last window_s seconds. Fewer than two samples, or
    no elapsed time, reads as 0.0 (a held finger, not a flick)."""
    if len(samples) < 2:
        return 0.0
    t_end, y_end = samples[-1]
    start = samples[-2]
    for sample in reversed(samples[:-1]):
        if t_end - sample[0] > window_s:
            break
        start = sample
    elapsed = t_end - start[0]
    if elapsed <= 0:
        return 0.0
    return (y_end - start[1]) / elapsed


def resume_lockout_secs(fails: int, until: float, start: float,
                        now: float) -> int | None:
    """Seconds of a persisted lockout to re-arm after a shell restart.

    The persisted wall-clock deadline is never trusted past what the fail
    count earns: the re-armed lockout is capped at _lockout_secs(fails),
    so an RTC reset or clock jump cannot lock the keypad for years. None
    means the wall clock went back before the lockout started (start is
    0.0 for state saved before start was recorded): the caller clears the
    running lockout but keeps the fail count. 0 means nothing to re-arm.
    """
    if start > 0 and now < start:
        return None
    if fails <= 0 or until <= now:
        return 0
    return max(0, min(int(until - now), _lockout_secs(fails)))
