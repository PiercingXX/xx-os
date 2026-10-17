"""About page: version, offline promise, open-source licenses."""

from __future__ import annotations


class AboutController:
    def __init__(self, window) -> None:  # type: ignore[no-untyped-def]
        self.window = window

    def on_show(self) -> None:
        pass


def build(window):  # type: ignore[no-untyped-def]
    import xx_ui.widgets as widgets
    from xx_auth import __app_id__, __version__

    Gtk = window._Gtk
    controller = AboutController(window)

    header = widgets.compact_header(lambda: window.show("list"), "About", "xx-auth")
    body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 12)
    body.append(widgets.field("App", "xx-auth"))
    body.append(widgets.field("Version", __version__))
    body.append(widgets.field("Vault", "Encrypted, offline"))
    entries = widgets.standard_license_entries("xx-auth")
    body.append(widgets.about_content("xx-auth", __version__, entries,
                                      lambda: widgets.show_licenses_dialog(window.win, "xx-auth", entries)))
    _ = __app_id__
    page = widgets.page(header, body)
    return page, controller
