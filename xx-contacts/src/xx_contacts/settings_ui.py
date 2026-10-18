"""App settings page pushed on the list's ``Adw.NavigationView``.

Phone-first 360x720: the list toggles from the Android settings screen
(hide contacts without a number, show photos) plus the entries that
left the list header (the Me card, import/export). Every row follows
the design system; switches restyle live with the theme.

Import-safe without a display: constructing the page needs a display,
but importing this module must not. The page holds no text entries and
never grabs focus, so opening it never pops the on-screen keyboard.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

gi.require_version("Pango", "1.0")

from gi.repository import Adw, Gtk, Pango  # noqa: E402

from xx_ui import widgets  # noqa: E402
import xx_contacts  # noqa: E402


def setting_row(title: str, subtitle: str = "", trailing=None,
                last: bool = False) -> Gtk.Box:
    """``widgets.setting_row`` whose title and subtitle wrap to two lines
    each (large text, 320 px) instead of ellipsizing at one; past two
    lines they ellipsize, and they never widen the window."""
    row = widgets.setting_row(title, subtitle, trailing, last=last)
    middle = row.get_first_child()
    line = middle.get_first_child() if middle is not None else None
    while isinstance(line, Gtk.Label):
        line.set_wrap(True)
        line.set_wrap_mode(Pango.WrapMode.WORD)
        line.set_lines(2)
        line.set_ellipsize(Pango.EllipsizeMode.END)
        line = line.get_next_sibling()
    return row


class SettingsPage(Adw.NavigationPage):
    """Toggles and destinations that left the list header."""

    def __init__(self, navigation: Adw.NavigationView, window,
                 config, config_path: str, on_changed=None) -> None:
        super().__init__()
        self._navigation = navigation
        self._window = window
        self._config = config
        self._config_path = config_path
        self._on_changed = on_changed
        self._alive = True

        header = widgets.compact_header(self._on_back, "Settings")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.add_css_class("xx-page-body")
        # Short bodies must pack at the top: a stretched viewport leaves
        # phantom gaps between the first children (seen on the Me card).
        body.set_valign(Gtk.Align.START)

        body.append(widgets.section_label("List"))
        self._phone_switch = widgets.make_switch(
            bool(config.has_phone_only), self._on_phone_toggled)
        body.append(setting_row(
            "Phone only", "Hide contacts without a number",
            self._phone_switch))
        self._photos_switch = widgets.make_switch(
            bool(config.show_photos), self._on_photos_toggled)
        body.append(setting_row(
            "Photos", "Show contact photos in the list",
            self._photos_switch, last=True))

        body.append(widgets.section_label("More"))
        body.append(self._nav_row("My card", "Your own contact card",
                                  self._on_me_clicked))
        body.append(self._nav_row("Import/export",
                                  "vCard files and GNOME Contacts",
                                  self._on_import_export_clicked))
        body.append(self._nav_row("About",
                                  "Version and open-source licenses",
                                  self._on_about_clicked,
                                  last=True))

        self.set_child(widgets.page(header, body))
        self.set_title("Settings")

    def _nav_row(self, title: str, subtitle: str, callback,
                 last: bool = False) -> Gtk.Widget:
        # No trailing chevron: the row reads as tappable on its own.
        row = setting_row(title, subtitle, last=last)
        click = Gtk.GestureClick.new()
        click.connect("pressed",
                      lambda _g, _n, _x, _y: callback())
        row.add_controller(click)
        return row

    def _save_and_notify(self) -> None:
        try:
            self._config.save(self._config_path)
        except OSError:
            pass
        if self._on_changed is not None:
            try:
                self._on_changed()
            except Exception:
                pass

    def _on_phone_toggled(self, active: bool) -> None:
        self._config.has_phone_only = bool(active)
        self._save_and_notify()

    def _on_photos_toggled(self, active: bool) -> None:
        self._config.show_photos = bool(active)
        self._save_and_notify()

    def _on_me_clicked(self) -> None:
        if self._alive:
            self._window.open_me_card()

    def _on_import_export_clicked(self) -> None:
        if self._alive:
            self._window.open_import_export()

    def _on_about_clicked(self) -> None:
        if self._alive:
            self._navigation.push(AboutPage(self._navigation))

    def _on_back(self) -> None:
        self._alive = False
        self._navigation.pop()


class AboutPage(Adw.NavigationPage):
    """Version, license and the open-source licenses page."""

    APP_NAME = "xx-contacts"

    def __init__(self, navigation: Adw.NavigationView) -> None:
        super().__init__()
        self._navigation = navigation
        entries = widgets.standard_license_entries(self.APP_NAME)
        version = getattr(xx_contacts, "__version__", "?")
        # widgets.page() gives the body its gutter; the header already
        # says About, so the block carries no second "About" label.
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        body.set_valign(Gtk.Align.START)
        body.append(widgets.about_content(
            self.APP_NAME, version, entries, self._on_licenses,
            section=False))
        header = widgets.compact_header(self._on_back, "About")
        self.set_child(widgets.page(header, body))
        self.set_title("About")

    def _on_licenses(self) -> None:
        self._navigation.push(widgets.licenses_page(
            self._navigation, self.APP_NAME,
            widgets.standard_license_entries(self.APP_NAME)))

    def _on_back(self) -> None:
        self._navigation.pop()
