"""The one rule for a new lock-screen PIN, shared by the first-boot wizard
and Settings › Change PIN: ASCII digits only (the lock keypad types
nothing else), at least MIN_PIN and at most MAX_PIN (the lock screen's
entry cap) long."""
from __future__ import annotations

MIN_PIN = 6
MAX_PIN = 64

_DIGITS = frozenset('0123456789')


def valid_pin(pin: object) -> bool:
    return (isinstance(pin, str) and MIN_PIN <= len(pin) <= MAX_PIN
            and set(pin) <= _DIGITS)
