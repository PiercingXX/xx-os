"""Undo history for the editor (port of ``ui/editor/EditHistory.kt``).

Bounded stack of text snapshots with coalescing: rapid keystrokes merge
into one undo step so holding a key does not need fifty taps to undo.
"""

from __future__ import annotations


class EditHistory:
    def __init__(self, capacity: int = 100) -> None:
        self._capacity = capacity
        self._undo: list[str] = []
        self._redo: list[str] = []

    def push(self, text: str) -> None:
        if self._undo and self._undo[-1] == text:
            return
        self._undo.append(text)
        del self._undo[: max(0, len(self._undo) - self._capacity)]
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self, current: str) -> str | None:
        if not self._undo:
            return None
        while self._undo and self._undo[-1] == current:
            self._undo.pop()
        if not self._undo:
            return None
        self._redo.append(current)
        return self._undo.pop()

    def redo(self, current: str) -> str | None:
        if not self._redo:
            return None
        self._undo.append(current)
        return self._redo.pop()

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
