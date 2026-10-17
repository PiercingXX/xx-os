"""xx-note main entry: Adw.Application with the notes shell.

Headless-safe import: Gtk/Adw load lazily so pure tests never need a
display. Every blocking call (file scan, WebDAV) rides a worker thread;
results post back with GLib.idle_add. `--sync-once` runs one headless
WebDAV pass for the systemd user timer (no display needed).
"""

from __future__ import annotations

import os
import sys
import threading



def _require_gtk():  # type: ignore[no-untyped-def]
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

    return Adw, Gdk, Gio, GLib, Gtk, Pango


class NoteApplication:
    def __init__(self) -> None:
        Adw, Gdk, Gio, GLib, Gtk, Pango = _require_gtk()
        self._Adw, self._Gdk, self._Gio, self._GLib, self._Gtk, self._Pango = (
            Adw, Gdk, Gio, GLib, Gtk, Pango,
        )
        from xx_note import __app_id__

        self._app = self._Adw.Application(
            application_id=__app_id__,
            flags=self._Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self._app.connect("activate", self._on_activate)
        self.window = None
        self._theme_unsub = None

    def _on_activate(self, app: object) -> None:
        if self.window is not None:
            self.window.win.present()
            return
        from xx_note import paths as paths_mod
        from xx_note import prefs as prefs_mod
        from xx_note import secrets as secrets_mod
        from xx_note import store as store_mod
        from xx_note.ui import shell as shell_mod

        paths_mod.ensure_app_dirs()
        settings = prefs_mod.load()
        vault = store_mod.Vault(settings.resolved_notes_dir())
        vault.ensure()
        self.window = shell_mod.NoteWindow(
            self._app,
            vault,
            settings,
            secrets_mod.LibsecretBackend(),
            self._Adw, self._Gtk, self._Pango, self._GLib,
        )
        self.window.win.set_title("xx-note")
        self._apply_theme()
        try:
            import xx_ui

            self._theme_unsub = xx_ui.on_change(self._apply_theme)
        except Exception:
            self._theme_unsub = None
        scenario = os.environ.get("XX_NOTE_SCENARIO", "")
        page = os.environ.get("XX_NOTE_PAGE", "")
        if scenario:
            self.window.seed_demo(scenario)
        self.window.refresh()
        if page:
            if page == "editor":
                self.window.open_first_for_snap()
            self.window.show_page(page)
        self._app._note_window = self.window
        self.window.win.present()

    def _apply_theme(self, theme=None) -> None:  # type: ignore[no-untyped-def]
        """Shared xx_ui stylesheet, then the app layer rendered from the
        same theme (colors and text scale), on every live change."""
        try:
            import xx_ui

            if theme is None:
                theme = xx_ui.apply(self.window.win if self.window else None)
        except Exception:
            return
        try:
            from xx_note.ui import style as style_mod

            style_mod.install(theme)
        except Exception:
            pass

    def run(self, argv: list[str]) -> int:
        return self._app.run(argv)


def sync_once() -> int:
    """One headless WebDAV push/pull pass (systemd timer entry)."""
    from xx_note import prefs as prefs_mod
    from xx_note import secrets as secrets_mod
    from xx_note import store as store_mod
    from xx_note import sync_engine as engine_mod
    from xx_note import webdav as webdav_mod
    from xx_note import paths as paths_mod

    paths_mod.ensure_app_dirs()
    settings = prefs_mod.load()
    if not settings.webdav_enabled or not settings.webdav_url.strip():
        print("sync not configured: open Settings, Sync first")
        return 2
    finished: dict = {}

    def work() -> None:
        try:
            secrets = secrets_mod.LibsecretBackend()
            password = secrets.get_password(settings.webdav_url, settings.webdav_user)
            if not password:
                finished["error"] = "sign-in required: save the password in Settings"
                return
            client = webdav_mod.WebDavClient(
                settings.webdav_url, settings.webdav_user, password
            )
            vault = store_mod.Vault(settings.resolved_notes_dir())
            vault.ensure()
            result = engine_mod.reconcile(
                vault, client, settings.webdav_path.strip() or "Notes/"
            )
            finished["ok"] = result
        except Exception as exc:  # surface plainly, never a traceback
            finished["error"] = str(exc)

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join()
    if "ok" in finished:
        result = finished["ok"]
        print(
            f"Sync done: {result.pushed} up, {result.pulled} down, "
            f"{result.forked} forks"
        )
        for problem in result.errors:
            print(f"note: {problem}")
        return 0
    print(f"sync failed: {finished.get('error')}")
    return 1


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] == "--sync-once":
        return sync_once()
    return NoteApplication().run([])
