"""Sensitive clipboard copy with auto-clear. Port of Android
`ui/CodeClipboard.kt` with the lane's 30 s timeout: the clip is cleared
again only when it still holds our code (never something the operator
copied since); when the clipboard is unreadable it is cleared
unconditionally. All GTK calls stay on the main thread."""

from __future__ import annotations

CLEAR_AFTER_MS = 30_000


class GdkAdapter:
    """Real clipboard. Reads chain asynchronously so nothing ever blocks."""

    def copy_text(self, text: str) -> None:
        from gi.repository import Gdk

        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(text)

    def clear_if_matches(self, code: str) -> None:
        from gi.repository import Gdk

        display = Gdk.Display.get_default()
        if display is None:
            return
        clipboard = display.get_clipboard()

        def done(_clipboard, result):  # type: ignore[no-untyped-def]
            try:
                current = clipboard.read_text_finish(result)
            except Exception:
                current = None
            if current is None or current == code:
                clipboard.set("")

        clipboard.read_text_async(None, done)


class CodeClipboard:
    def __init__(self, adapter=None, schedule=None) -> None:  # type: ignore[no-untyped-def]
        self._adapter = adapter if adapter is not None else GdkAdapter()
        self._schedule = schedule
        self._code: str | None = None

    def copy(self, code: str) -> None:
        self._code = code
        self._adapter.copy_text(code)
        if self._schedule is not None:
            self._schedule(CLEAR_AFTER_MS, self._on_timeout)
            return
        from gi.repository import GLib

        GLib.timeout_add(CLEAR_AFTER_MS, self._fire)

    def _fire(self) -> bool:
        self._on_timeout()
        return False

    def _on_timeout(self) -> None:
        if self._code is not None:
            self._adapter.clear_if_matches(self._code)
