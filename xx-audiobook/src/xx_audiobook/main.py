"""xx-audiobook entry point: Adw.Application with the five-tab shell.

The window (ui/shell.py) builds the five tabs (Library, Playing,
Downloads, Zones, Setup) and the pushed views on the shared xx_ui design
system with live theme switching.
"""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from xx_audiobook import __app_id__  # noqa: E402
from xx_audiobook import applog as applog_module  # noqa: E402
from xx_audiobook import prefs as prefs_module  # noqa: E402
from xx_audiobook.paths import ensure_app_dirs  # noqa: E402
from xx_audiobook.ui import shell as shell_module  # noqa: E402

TABS: tuple = shell_module.TABS


def build_window(app: Adw.Application, ctx=None):
    """The real window; ``ctx`` defaults to the production wiring."""
    if ctx is None:
        from xx_audiobook.ui.context import default_context

        ctx = default_context()
    return shell_module.build(app, ctx)


class AudiobookApplication(Adw.Application):
    def __init__(self, context_factory=None) -> None:
        super().__init__(
            application_id=__app_id__,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._context_factory = context_factory

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is not None:
            window.present()
            return
        ensure_app_dirs()
        prefs_module.load()
        applog_module.get_logger().info("audiobook started")
        ctx = self._context_factory() if self._context_factory else None
        window = build_window(self, ctx)
        window.present()


def main(argv: list[str] | None = None, context_factory=None) -> int:
    app = AudiobookApplication(context_factory)
    return app.run(sys.argv if argv is None else [sys.argv[0], *argv])


if __name__ == "__main__":
    raise SystemExit(main())
