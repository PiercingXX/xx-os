"""xx-auth main window: offline TOTP/HOTP authenticator.

Headless-safe import: Gtk/Adw load lazily so pure tests never need a
display. Keyring and file work runs on worker threads; results post back
with GLib.idle_add, so nothing blocks the GTK main loop."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

from xx_auth import keystore as keystore_mod
from xx_auth import prefs as prefs_mod
from xx_auth.keystore import MemoryKeyring, SecretCipher
from xx_auth.prefs import Prefs
from xx_auth.store import VaultStore

# App-local styles on top of xx_ui: the live code in the brand display face
# (Space Mono, Signal = full foreground), the countdown ring and the quiet
# destructive outline. Rendered per theme and text scale, re-rendered live.
_APP_CSS = """
.xx-auth-code {{ font-size: {code}px; color: {fg}; letter-spacing: 0.04em; }}
.xx-auth-code.hero {{ font-size: {hero}px; }}
.xx-auth-code.unreadable {{ color: {muted}; }}
.xx-auth-secret {{ font-size: {title}px; color: {fg}; letter-spacing: 0.04em; }}
.xx-auth-row {{ padding-top: 14px; padding-bottom: 14px; }}
.xx-auth-ring {{ color: alpha({fg}, 0.80); font-size: {time}px; }}
.xx-auth-ring.warn {{ color: {danger}; }}
.xx-auth-danger {{ color: {danger}; border-color: alpha({danger}, 0.6); }}
.xx-auth-counter {{ font-size: {time}px; color: {muted}; }}
"""


# The largest live code that still fits a 320 px screen in full: an 8-digit
# code is 9 Space Mono cells (0.612 em + 0.04 em tracking) inside 280 px of
# content. A code is never ellipsized, so large text stops growing it here.
CODE_MAX_PX = 44


def app_css(theme) -> str:  # type: ignore[no-untyped-def]
    """The app stylesheet for one xx_ui theme (colors + text scale)."""
    from xx_ui import theme as theme_mod

    scale = getattr(theme, "scale", 1.0) or 1.0

    def px(base: int) -> int:
        return round(base * scale)

    return _APP_CSS.format(
        code=min(px(32), CODE_MAX_PX), hero=min(px(40), CODE_MAX_PX),
        title=px(16), time=px(13),
        fg=theme.foreground, muted=theme.muted,
        danger=getattr(theme, "danger", "") or theme_mod.DANGER_RED)


def _require_gtk():
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

    return Adw, Gdk, Gio, GLib, Gtk, Pango


@dataclass
class Backend:
    store: VaultStore
    cipher: SecretCipher
    prefs: Prefs
    password_mode: bool
    unlocked: bool


def group_code(code: str) -> str:
    if len(code) == 8:
        return code[:4] + " " + code[4:]
    if len(code) == 7:
        return code[:3] + " " + code[3:]
    if len(code) == 6:
        return code[:3] + " " + code[3:]
    return code


class AuthWindow:
    def __init__(self, app, prefs: Prefs | None = None) -> None:
        Adw, Gdk, Gio, GLib, Gtk, Pango = _require_gtk()
        self._Adw, self._Gtk, self._GLib = Adw, Gtk, GLib
        self._Pango = Pango
        self.app = app
        self.prefs = prefs or prefs_mod.load()
        self.backend: Backend | None = None
        self._pages: dict[str, object] = {}
        self._last_active = time.monotonic()
        self._clipboard = None
        self._setup_done = False

        import xx_ui

        self._xx_ui = xx_ui
        self.win = Adw.ApplicationWindow(application=app)
        self.win.set_title("xx-auth")
        self.win.set_default_size(360, 720)
        # Adw windows floor at 360 px wide by default: a 320 px
        # screen would cut the app off.
        self.win.set_size_request(320, 200)
        try:
            xx_ui.apply(self.win)
        except Exception:
            pass
        self._load_app_css()

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.set_vexpand(True)
        self.win.set_content(self.stack)
        self._build_loading()
        self._track_activity()
        self._start_tick()
        self._resolve_backend()

    def _load_app_css(self) -> None:
        try:
            from gi.repository import Gdk as GdkMod
            from gi.repository import Gtk as GtkMod

            import xx_ui

            display = GdkMod.Display.get_default()
            if display is None:
                return
            provider = GtkMod.CssProvider()
            GtkMod.StyleContext.add_provider_for_display(
                display, provider, GtkMod.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)

            def install(theme) -> None:  # type: ignore[no-untyped-def]
                provider.load_from_data(app_css(theme).encode("utf-8"))

            install(xx_ui.load())
            xx_ui.on_change(install)
        except Exception:
            pass

    def _track_activity(self) -> None:
        from gi.repository import Gtk as GtkMod

        def touch(*_args):
            self._last_active = time.monotonic()
            return False

        motion = GtkMod.EventControllerMotion.new()
        motion.connect("motion", touch)
        self.win.add_controller(motion)
        keys = GtkMod.EventControllerKey.new()
        keys.connect("key-pressed", touch)
        self.win.add_controller(keys)
        click = GtkMod.GestureClick.new()
        click.set_propagation_phase(GtkMod.PropagationPhase.CAPTURE)
        click.connect("pressed", touch)
        self.win.add_controller(click)

    # -- backend ------------------------------------------------------
    def _resolve_backend(self) -> None:
        thread = threading.Thread(target=self._resolve_work, daemon=True)
        thread.start()

    def _resolve_work(self) -> None:
        try:
            from xx_auth import accounts as accounts_mod

            store = VaultStore()
            prefs = self.prefs
            keyring = MemoryKeyring() if os.environ.get("XX_AUTH_NO_KEYRING") else keystore_mod.SecretServiceKeyring()
            try:
                resolved = accounts_mod.open_at_startup(store, prefs, keyring)
            except accounts_mod.VaultUnreadable as exc:
                self._GLib.idle_add(self._on_backend_error, str(exc))
                return
            if resolved.cipher is not None:
                backend = Backend(store, resolved.cipher, prefs, False,
                                  not prefs.lock_enabled or not prefs.lock_hash)
            else:
                backend = Backend(store, None, prefs, True, False)  # type: ignore[arg-type]
            self._GLib.idle_add(self._on_backend, backend)
        except Exception as exc:  # noqa: BLE001
            self._GLib.idle_add(self._on_backend_error, str(exc))

    def _on_backend(self, backend: Backend) -> bool:
        self.backend = backend
        if backend.cipher is None:
            if backend.password_mode and not self._setup_done and not backend.prefs.lock_hash:
                self._show_setup()
            else:
                self._show_lock()
            self.show("lock")
        elif not backend.unlocked:
            self._show_lock()
            self.show("lock")
        else:
            self.show("list")
        if os.environ.get("XX_AUTH_SCENARIO"):
            self.seed_demo(os.environ["XX_AUTH_SCENARIO"])
        page = os.environ.get("XX_AUTH_PAGE", "")
        if page and (backend.cipher is not None and backend.unlocked):
            self.show(page)
        return False

    def _on_backend_error(self, message: str) -> bool:
        self._build_info_page("loading", "xx-auth", "Couldn't start: " + message)
        self.show("loading")
        return False

    # -- lock ---------------------------------------------------------
    def unlock(self, password: str) -> bool:
        backend = self.backend
        if backend is None:
            return False
        from xx_auth import accounts as accounts_mod

        keyring = MemoryKeyring() if os.environ.get("XX_AUTH_NO_KEYRING") else keystore_mod.SecretServiceKeyring()
        try:
            backend.cipher = accounts_mod.unlock_with_password(
                backend.store, backend.prefs, keyring, password)
        except accounts_mod.VaultUnreadable:
            return False
        backend.unlocked = True
        backend.password_mode = backend.store.header is None or (
            backend.store.header.key_source == "password")
        self._touch()
        self._show_list()
        self.show("list")
        return True

    def set_password(self, password: str) -> None:
        backend = self.backend
        assert backend is not None
        from xx_auth import accounts as accounts_mod

        backend.cipher = accounts_mod.setup_password(backend.store, backend.prefs, password)
        backend.unlocked = True
        backend.password_mode = True
        self._setup_done = True
        self._touch()
        self._show_list()
        self.show("list")

    def lock(self) -> None:
        backend = self.backend
        if backend is None:
            return
        if backend.password_mode or (backend.prefs.lock_enabled and backend.prefs.lock_hash):
            backend.unlocked = False
            backend.cipher = None if backend.password_mode else backend.cipher
            self._show_lock()
            self.show("lock")

    def _touch(self) -> None:
        self._last_active = time.monotonic()

    # -- navigation ---------------------------------------------------
    def show(self, name: str) -> None:
        if name == "list":
            self._show_list()
        elif name == "add":
            self._show_add()
        elif name == "io":
            self._show_io()
        elif name == "about":
            self._show_about()
        elif name == "locksettings":
            self._show_locksettings()
        elif name == "lock":
            self._lock_controller()
        controller = self._pages.get(name)
        if controller is not None and hasattr(controller, "on_show"):
            controller.on_show()
        self.stack.set_visible_child_name(name)

    def _page(self, name: str, builder):  # type: ignore[no-untyped-def]
        if name not in self._pages:
            widget, controller = builder(self)
            self.stack.add_named(widget, name)
            self._pages[name] = controller
        return self._pages[name]

    def _build_loading(self) -> None:
        Gtk = self._Gtk
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        # Blank while the keyring answers: it takes a moment, not a message.
        self.stack.add_named(box, "loading")
        self.stack.set_visible_child_name("loading")

    def _build_info_page(self, name: str, title: str, text: str) -> None:
        import xx_ui.widgets as widgets

        Gtk = self._Gtk
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        info = Gtk.Label(label=text)
        info.set_wrap(True)
        info.set_wrap_mode(self._Pango.WrapMode.WORD_CHAR)
        info.set_max_width_chars(28)
        body.append(info)
        page = widgets.page(widgets.page_header(title), body)
        if self.stack.get_child_by_name(name) is None:
            self.stack.add_named(page, name)
        else:
            old = self.stack.get_child_by_name(name)
            self.stack.remove(old)
            self.stack.add_named(page, name)

    def _show_list(self) -> None:
        from xx_auth import ui_list

        self._page("list", ui_list.build)

    def _show_add(self) -> None:
        from xx_auth import ui_add

        self._page("add", ui_add.build)

    def _show_io(self) -> None:
        from xx_auth import ui_io

        self._page("io", ui_io.build)

    def _show_about(self) -> None:
        from xx_auth import ui_about

        self._page("about", ui_about.build)

    def _lock_controller(self):  # type: ignore[no-untyped-def]
        from xx_auth import ui_lock

        return self._page("lock", ui_lock.build_lock)

    def _show_lock(self) -> None:
        self._lock_controller().set_mode("unlock")

    def _show_setup(self) -> None:
        self._lock_controller().set_mode("setup")

    def _show_locksettings(self) -> None:
        from xx_auth import ui_lock

        self._page("locksettings", ui_lock.build_settings)

    def show_detail(self, account_id: str) -> None:
        from xx_auth import ui_detail

        controller = self._page("detail", ui_detail.build)
        controller.open(account_id)
        self.stack.set_visible_child_name("detail")

    # -- shared actions -----------------------------------------------
    def copy_code(self, code: str) -> None:
        if self._clipboard is None:
            from xx_auth.clipboard import CodeClipboard

            self._clipboard = CodeClipboard()
        self._clipboard.copy(code)

    def seed_demo(self, scenario: str) -> None:
        from xx_auth import accounts as accounts_mod
        from xx_auth import base32 as base32_mod

        backend = self.backend
        if backend is None or backend.cipher is None or backend.store.count():
            return
        seeds = [
            ("GitHub", "alice", False, "SHA1", 6, 30, 0, ""),
            ("Proton", "bob@pm.me", False, "SHA256", 8, 60, 0, "Work"),
            ("Bank", "savings", True, "SHA1", 6, 30, 7, ""),
        ]
        secret = base32_mod.decode("JBSWY3DPEHPK3PXP")
        for issuer, label, hotp, algo, digits, period, counter, group in seeds:
            accounts_mod.add_manual(
                backend.store, issuer, label, base32_mod.encode(secret),
                hotp, algo, digits, period, counter, backend.cipher,
            )
            if group:
                row = backend.store.get_all()[-1]
                from dataclasses import replace

                backend.store.update(replace(row, group=group))
        backend.store.save()
        if scenario == "empty":
            backend.store.delete_all()
            backend.store.save()

    def refresh(self) -> None:
        controller = self._pages.get("list")
        if controller is not None and hasattr(controller, "refresh"):
            controller.refresh()
        detail = self._pages.get("detail")
        if detail is not None and hasattr(detail, "refresh"):
            detail.refresh()

    # -- tick ---------------------------------------------------------
    def _start_tick(self) -> None:
        self._GLib.timeout_add(1000, self._on_tick)

    def _on_tick(self) -> bool:
        backend = self.backend
        if backend is not None and backend.cipher is not None and backend.unlocked:
            idle_limit = max(15, int(backend.prefs.idle_seconds or 120))
            needs_lock = backend.password_mode or (backend.prefs.lock_enabled and backend.prefs.lock_hash)
            if needs_lock and time.monotonic() - self._last_active > idle_limit:
                self.lock()
                return True
            # Only the visible page ticks, in place: rebuilding hidden pages
            # every second relayouts the whole window for nothing.
            visible = self.stack.get_visible_child_name()
            controller = self._pages.get(visible) if visible in ("list", "detail") else None
            if controller is not None and hasattr(controller, "tick"):
                controller.tick()
        return True


class AuthApp:
    def __init__(self) -> None:
        Adw, _Gdk, Gio, _GLib, _Gtk, _Pango = _require_gtk()
        from xx_auth import __app_id__

        self._app = Adw.Application(application_id=__app_id__,
                                    flags=Gio.ApplicationFlags.FLAGS_NONE)
        self._app.connect("activate", self._on_activate)
        self.window: AuthWindow | None = None

    def _on_activate(self, app) -> None:
        if self.window is None:
            self.window = AuthWindow(app)
            app._auth_window = self.window
        self.window.win.present()

    def run(self, argv=None) -> int:  # type: ignore[no-untyped-def]
        return self._app.run(argv if argv is not None else [])


def main(argv=None) -> int:  # type: ignore[no-untyped-def]
    import sys

    args = list(argv) if argv is not None else sys.argv[1:]
    app = AuthApp()
    return app.run(args)
