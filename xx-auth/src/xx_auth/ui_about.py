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

    Gtk = window._Gtk
    controller = AboutController(window)

    # The offline promise is the header's subtitle; the rows follow the
    # suite's Setup "About" block (no second About label, no chevron).
    header = widgets.compact_header(lambda: window.show("list"), "About",
                                    "No network, no account.")
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
    entries = widgets.standard_license_entries("xx-auth")
    spdx = entries[0].spdx if entries else ""
    version = f"Version {__version__}" + (f" · {spdx}" if spdx else "")
    body.append(widgets.setting_row("xx-auth", version))
    body.append(widgets.list_row(
        "Open-source licenses", f"{len(entries)} components",
        on_activate=lambda: widgets.show_licenses_dialog(window.win, "xx-auth", entries),
        last=True))
    page = widgets.page(header, body)
    return page, controller
