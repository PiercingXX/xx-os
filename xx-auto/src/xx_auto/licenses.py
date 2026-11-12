"""About screen data: the app license plus the suite local-only note."""

from __future__ import annotations

from . import __version__

APP_NAME = "auto"
VERSION_LINE = f"auto {__version__}"
LOCAL_ONLY = ("Free and ad-free. Collects no personal data. "
              "Nothing here leaves your device.")


def license_rows() -> list[tuple[str, str]]:
    """(title, detail) rows for the open-source licenses screen."""
    return [
        ("auto", "GPL-3.0 — this app"),
        ("GTK4 / libadwaita / PyGObject", "LGPL-2.1 — system libraries"),
        ("JetBrains Mono", "OFL-1.1 — bundled with the suite theme"),
    ]
