"""About page: version, offline promise, open-source licenses."""

from __future__ import annotations


class AboutController:
    def __init__(self, window) -> None:  # type: ignore[no-untyped-def]
        self.window = window

    def on_show(self) -> None:
        pass


def build(window):  # type: ignore[no-untyped-def]
    import xx_ui.widgets as widgets
    from xx_auth import __version__

    controller = AboutController(window)

    # The offline promise is the header's subtitle; the rows are the
    # suite's shared About block (no second About label, no chevron).
    header = widgets.compact_header(lambda: window.show("list"), "About",
                                    "Fully offline")
    entries = widgets.standard_license_entries("xx-auth")
    body = widgets.about_content(
        "xx-auth", __version__, entries,
        lambda: widgets.show_licenses_dialog(window.win, "xx-auth", entries),
        section=False)
    page = widgets.page(header, body)
    return page, controller
