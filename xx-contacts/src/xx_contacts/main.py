import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib  # noqa: E402

import xx_ui  # noqa: E402
from xx_contacts import __appid__  # noqa: E402
from xx_contacts.app_config import AppConfig, default_config_path  # noqa: E402
from xx_contacts.list_ui import ContactsWindow  # noqa: E402
from xx_contacts.store import ContactStore, default_db_path  # noqa: E402

import os  # noqa: E402
import sys  # noqa: E402

#: vCard suffixes Files hands the app: each opens the import confirm page.
VCF_SUFFIXES = (".vcf", ".vcard")


def is_vcf_path(path: str) -> bool:
    """True for local ``.vcf``/``.vcard`` files the import page can read."""
    return (path or "").strip().lower().endswith(VCF_SUFFIXES)


class ContactsApp(Adw.Application):
    """Contacts app: list window plus consumer entry points.

    ``--new`` opens a blank editor over the list; ``--tel NUMBER``
    prefills its first phone row (and implies ``--new``). Bare ``.vcf``
    arguments open the import confirm page for each file. ``--pick KIND
    --reply-to TOKEN`` (launched by the D-Bus service, never by hand)
    opens a pick-mode window whose choice is reported back over
    ``PickReply`` so the service can settle the holding ``Pick`` call.
    All four arrive through ``do_command_line``, so a second launch
    forwards them to the already-running instance instead of dropping
    them.
    """

    def __init__(self, pending_new=(), pending_files=(),
                 pending_picks=()) -> None:
        super().__init__(
            application_id=__appid__,
            flags=(Gio.ApplicationFlags.HANDLES_OPEN
                   | Gio.ApplicationFlags.HANDLES_COMMAND_LINE),
        )
        self.add_main_option(
            "new", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Open a new-contact editor", None)
        self.add_main_option(
            "tel", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
            "Prefill the new-contact editor with this number", "NUMBER")
        self.add_main_option(
            "pick", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
            "Open a contact picker for KIND (service use)", "KIND")
        self.add_main_option(
            "reply-to", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
            "PickReply token for --pick (service use)", "TOKEN")
        self.add_main_option(
            "db", 0, GLib.OptionFlags.NONE, GLib.OptionArg.STRING,
            "Contact database for --pick (service use)", "PATH")
        self._pending_new = [str(tel or "") for tel in pending_new]
        self._pending_files = [str(path) for path in pending_files]
        self._pending_picks = [(str(kind), str(token), str(db or ""))
                               for kind, token, db in pending_picks]

    @staticmethod
    def _option_string(options, name: str) -> str:
        value = options.lookup_value(name, None)
        return value.get_string() if value is not None else ""

    def do_command_line(self,
                        command_line: Gio.ApplicationCommandLine) -> int:
        options = command_line.get_options_dict()
        tel = self._option_string(options, "tel")
        if options.contains("new") or tel:
            self._pending_new.append(tel)
        kind = self._option_string(options, "pick")
        token = self._option_string(options, "reply-to")
        db = self._option_string(options, "db")
        if kind and token:
            self._pending_picks.append((kind, token, db))
        args = command_line.get_arguments() or ()
        for arg in args[1:]:
            try:
                target = command_line.create_file_for_arg(arg)
            except Exception:
                continue
            path = target.get_path()
            if path and is_vcf_path(path):
                if path not in self._pending_files:
                    self._pending_files.append(path)
        self.activate()
        return 0

    def do_activate(self) -> None:
        xx_ui.apply(self)
        if self._pending_picks:
            picks, self._pending_picks = self._pending_picks, []
            for kind, token, db in picks:
                self.open_pick_window(kind, token, db)
            return
        window = self.get_active_window()
        if window is None:
            home = os.path.expanduser("~")
            config_path = default_config_path(home)
            config = AppConfig.load(config_path)
            store = ContactStore(default_db_path(home))
            window = ContactsWindow(self, store, config, config_path,
                                    store.path)
        window.present()
        self._drain_pending(window)

    def open_pick_window(self, kind: str, token: str,
                         db_path: str = "") -> object | None:
        """Pick-mode window for a service ``Pick``; replies over D-Bus.

        Returns the window, or None when the kind is unknown (the
        service already validated it, so this only guards hand runs).
        Closing the window without picking reports a cancel.
        """
        from xx_contacts import picker as picker_module
        from xx_contacts import service_client
        from xx_contacts.picker_ui import ContactPickerPage

        try:
            normalized = picker_module.normalize_kind(kind)
        except ValueError:
            return None
        home = os.path.expanduser("~")
        config_path = default_config_path(home)
        config = AppConfig.load(config_path)
        store = ContactStore(db_path or default_db_path(home))
        try:
            import xx_phone

            rows = picker_module.build_pick_rows(
                normalized, store.all_records(),
                xx_phone.display_country(config.default_region))
        except Exception:
            rows = []
        window = Adw.ApplicationWindow(application=self)
        window.set_default_size(360, 720)
        navigation = Adw.NavigationView()
        settled: list[bool] = []

        def _close() -> None:
            try:
                store.close()
            except Exception:
                pass
            try:
                window.close()
            except Exception:
                pass

        def _settle(uid: str, value: str) -> None:
            if settled:
                return
            settled.append(True)
            try:
                service_client.reply_pick_async(
                    token, uid, value, lambda: _close(),
                    lambda _exc: _close())
            except Exception:
                _close()

        page = ContactPickerPage(navigation, normalized, rows, _settle,
                                 lambda: _settle("", ""))
        navigation.push(page)
        window.set_content(navigation)
        window.connect("close-request", lambda _w: _settle("", ""))
        window.present()
        return window

    def _drain_pending(self, window) -> None:
        for tel in self._pending_new:
            window.open_new_editor(prefill_phone=tel)
        del self._pending_new[:]
        for path in self._pending_files:
            window.open_vcf_file(path)
        del self._pending_files[:]


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    app = ContactsApp()
    app.run([sys.argv[0]] + args)


if __name__ == "__main__":
    main()
