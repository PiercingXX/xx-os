"""Caption overlay: its own small GTK4 process (`xx-captions --overlay`).

Layer OVERLAY, keyboard NONE, anchored bottom with the persisted
margin above the gesture area, exclusive zone 0. Taps pass through
everywhere except the drag handle and the overflow button (empty input
region plus those rects). Partials replace the last line in
place; the matching final commits it (CaptionBuffer), so labels only
ever change text, never rebuild. Colors come from the xx_ui design
system with live reload; the on state (and an error) reads in the danger
red, the one fixed color allowed. Engine and download work stays on worker threads
and posts back with GLib.idle_add, so the main loop never blocks.

Without layer-shell (Broadway, test VMs) the same window falls back
to a normal undecorated window; without GTK the module still imports
and run_overlay() returns 2.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from string import Template

from xx_captions import config, models
from xx_captions.client import CaptionsClient
from xx_captions.overlay_state import (
    CaptionBuffer,
    clamp_margin,
    download_label,
    load_margin,
    model_missing,
    phone_default_model,
    progress_fraction,
    save_margin,
)

_log = logging.getLogger("captions-overlay")

APPLICATION_ID = "io.piercingxx.Captions"

RECORD_RED = "#ff6b6b"

_HANDLE_HEIGHT = 48


def _app_css_path() -> Path:
    return Path(__file__).with_name("captions.css")


def render_app_css(theme) -> str:
    """Overlay chrome for an xx_ui theme. Tokens only, never literals."""
    mapping = {
        "surface": theme.surface,
        "border": theme.border,
        "muted": theme.muted,
        "danger": RECORD_RED,
    }
    return Template(_app_css_path().read_text(encoding="utf-8")).substitute(
        mapping)


def _shell_config_path(home) -> Path | None:
    base = home if home is not None else Path.home()
    try:
        return Path(base) / ".config" / "xx-wm" / "config.json"
    except Exception:
        return None


def load_ui_theme(home):
    """Active xx_ui theme for this HOME (falls back, never raises)."""
    import xx_ui

    path = _shell_config_path(home)
    if path is not None:
        try:
            return xx_ui.theme.load(path)
        except Exception:
            pass
    return xx_ui.theme.load()


_HAS_GTK = True
try:
    import gi
except ImportError:
    _HAS_GTK = False

if _HAS_GTK:
    try:
        gi.require_version("Gtk4LayerShell", "1.0")
        _HAS_LAYER = True
    except (ValueError, ImportError):
        _HAS_LAYER = False

    gi.require_version("Gtk", "4.0")
    try:
        gi.require_version("Pango", "1.0")
    except (ValueError, ImportError):
        pass
    from gi.repository import GLib, Gtk, Pango

    if _HAS_LAYER:
        from gi.repository import Gtk4LayerShell as LayerShell

    try:
        import xx_ui
        from xx_ui import widgets
        _HAS_XX_UI = True
    except ImportError:
        _HAS_XX_UI = False

_UI_APPLIED = False


def ensure_ui(target=None):
    """Install the xx_ui design system once per process."""
    global _UI_APPLIED
    if not _HAS_GTK or not _HAS_XX_UI or _UI_APPLIED:
        return
    try:
        xx_ui.apply(target)
    except Exception:
        return
    _UI_APPLIED = True


def layer_available() -> bool:
    """True when the layer-shell seam can own this window."""
    if not _HAS_GTK or not _HAS_LAYER:
        return False
    try:
        return bool(LayerShell.is_supported())
    except Exception:
        return False


class DownloadCancelled(Exception):
    pass


if _HAS_GTK:
    class CaptionWindow(Gtk.Window):
        """Bottom overlay: caption lines, or the first-run model screen."""

        def __init__(self, home=None, client: CaptionsClient | None = None,
                     model_id: str | None = None) -> None:
            super().__init__()
            self._home = home
            self._model_id = model_id or phone_default_model()
            self._buffer = CaptionBuffer()
            self._margin = load_margin(home)
            self._client = client
            self._downloading = False
            self._cancel_download = threading.Event()
            self._drag_start_margin = self._margin
            self._unsubscribe_ui = None
            self._settings_window = None

            self.set_decorated(False)
            self.set_resizable(False)

            if layer_available():
                LayerShell.init_for_window(self)
                LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
                LayerShell.set_anchor(self, LayerShell.Edge.TOP, False)
                LayerShell.set_anchor(self, LayerShell.Edge.BOTTOM, True)
                LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
                LayerShell.set_anchor(self, LayerShell.Edge.RIGHT, True)
                LayerShell.set_exclusive_zone(self, 0)
                LayerShell.set_keyboard_mode(
                    self, LayerShell.KeyboardMode.NONE)
                LayerShell.set_margin(
                    self, LayerShell.Edge.BOTTOM, self._margin)

            ensure_ui(self)
            self._theme_provider = Gtk.CssProvider()
            self.apply_theme()
            try:
                display = self.get_display()
                if display is not None:
                    Gtk.StyleContext.add_provider_for_display(
                        display, self._theme_provider,
                        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            except Exception:
                pass
            if _HAS_XX_UI:
                try:
                    self._unsubscribe_ui = xx_ui.on_change(
                        self._on_ui_theme)
                except Exception:
                    self._unsubscribe_ui = None
            self.connect("close-request", self._on_close_request)

            handle = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                             spacing=4)
            handle.set_size_request(-1, _HANDLE_HEIGHT)
            handle.set_valign(Gtk.Align.CENTER)
            self._status = widgets.caption("Captions off")
            # FILL, not START: a wrapping label at START gets only its
            # (narrow) natural width and breaks "CAPTIONS OFF" in two.
            self._status.set_halign(Gtk.Align.FILL)
            self._status.set_xalign(0.0)
            self._status.set_hexpand(True)
            self._status.set_valign(Gtk.Align.CENTER)
            self.set_status("Captions off")
            handle.append(self._status)
            self._toggle = widgets.caps_button("Start")
            self._toggle.set_valign(Gtk.Align.CENTER)
            self._toggle.connect("clicked", self._on_toggle_clicked)
            handle.append(self._toggle)
            self._more = widgets.icon_button("view-more-symbolic")
            self._more.set_valign(Gtk.Align.CENTER)
            self._more.connect("clicked", self._on_more_clicked)
            handle.append(self._more)
            self._handle = handle
            self._menu = self._build_menu(self._more)

            drag = Gtk.GestureDrag.new()
            drag.connect("drag-begin", self._on_drag_begin)
            drag.connect("drag-update", self._on_drag_update)
            drag.connect("drag-end", self._on_drag_end)
            self._status.add_controller(drag)

            self._lines: list = []
            caption_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                  spacing=2)
            for _ in range(3):
                line = widgets.wrap_label("", "xx-body")
                line.set_halign(Gtk.Align.FILL)
                line.set_visible(False)
                caption_box.append(line)
                self._lines.append(line)
            self._caption_box = caption_box

            self._model_title = widgets.wrap_label("Captions need a model",
                                                  "xx-dialog-title")
            self._model_size = widgets.wrap_label("", "xx-secondary")
            self._progress = Gtk.ProgressBar()
            self._progress.set_show_text(False)
            self._progress.set_visible(False)
            self._download = widgets.primary_button("Download")
            self._download.connect("clicked", self._on_download_clicked)
            self._cancel = widgets.outline_button("Cancel")
            self._cancel.connect("clicked", self._on_cancel_clicked)
            self._cancel.set_visible(False)
            self._model_error = widgets.wrap_label("", "xx-secondary")
            self._model_error.add_css_class("xx-danger-text")
            self._model_error.set_xalign(0.0)
            self._model_error.set_visible(False)
            model_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=8)
            model_box.append(self._model_title)
            model_box.append(self._model_size)
            model_box.append(self._progress)
            model_box.append(self._download)
            model_box.append(self._cancel)
            model_box.append(self._model_error)
            self._model_box = model_box

            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            root.add_css_class("caption-root")
            # Full width less a 12 px inset each side: the overlay follows
            # the screen (320 px to 450 px) instead of a fixed card.
            root.set_halign(Gtk.Align.FILL)
            root.set_hexpand(True)
            root.set_margin_start(12)
            root.set_margin_end(12)
            root.set_vexpand(True)
            root.set_valign(Gtk.Align.END)
            root.append(handle)
            root.append(caption_box)
            root.append(model_box)
            self.set_child(root)

            self.connect("realize", self._on_realize)
            if model_missing(home):
                self.show_model_view()
            else:
                self.show_caption_view()

        def _build_menu(self, parent) -> Gtk.Popover:
            menu = Gtk.Popover()
            menu.set_parent(parent)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            box.set_size_request(240, -1)
            box.append(widgets.menu_row("Settings", self._open_settings))
            box.append(widgets.menu_row("Close", self._close_menu))
            menu.set_child(box)
            return menu

        def _close_menu(self) -> None:
            try:
                self._menu.popdown()
            except Exception:
                pass
            self._on_close_clicked()

        def _open_settings(self) -> None:
            from xx_captions.settings import SettingsWindow

            try:
                self._menu.popdown()
            except Exception:
                pass
            # One settings window: a second tap raises it, never a twin.
            window = self._settings_window
            if window is None or not window.get_visible():
                window = SettingsWindow(home=self._home)
                self._settings_window = window
            window.present()

        def _on_ui_theme(self, theme) -> None:
            try:
                self.apply_theme(theme)
            except Exception:
                pass

        def _on_close_request(self, _window) -> bool:
            if self._unsubscribe_ui is not None:
                try:
                    self._unsubscribe_ui()
                except Exception:
                    pass
                self._unsubscribe_ui = None
            return False

        def apply_theme(self, theme=None) -> None:
            """Reload the overlay sheet from the current shell preset."""
            if not _HAS_XX_UI:
                return
            try:
                active = theme if theme is not None else load_ui_theme(
                    self._home)
                self._theme_provider.load_from_data(
                    render_app_css(active).encode("utf-8"))
            except Exception:
                pass

        def show_caption_view(self) -> None:
            # Start only means something once a model is installed.
            self._toggle.set_visible(True)
            self._model_box.set_visible(False)
            self._caption_box.set_visible(True)
            self.render()

        def show_model_view(self) -> None:
            self._toggle.set_visible(False)
            self._caption_box.set_visible(False)
            self._model_box.set_visible(True)
            size = download_label(self._model_id)
            self._model_size.set_text(size)
            self._model_size.set_visible(bool(size))
            self._progress.set_visible(False)
            self._cancel.set_visible(False)
            self._download.set_visible(True)
            self._model_error.set_visible(False)

        def on_text(self, text: str, final: bool) -> None:
            if final:
                self._buffer.push_final(text)
            else:
                self._buffer.push_partial(text)
            self.render()

        def render(self) -> None:
            texts = list(self._buffer.lines()[-3:])
            partial = self._buffer.live_partial()
            slot = [""] * (3 - len(texts)) + texts
            for pos, (label, text) in enumerate(zip(self._lines, slot)):
                if label.get_text() != text:
                    label.set_text(text)
                label.set_visible(bool(text))
                is_partial = bool(partial) and pos == 2 and text == partial
                if is_partial:
                    label.add_css_class("caption-line-partial")
                else:
                    label.remove_css_class("caption-line-partial")
            GLib.idle_add(self._refresh_input_region, priority=GLib.PRIORITY_LOW)

        def set_status(self, text: str, recording: bool = False) -> None:
            """Caps state line. It wraps (two lines at most) rather than
            widening the overlay, so a long error or large text still
            fits 320 px; short labels never ellipsize."""
            self._status.set_text(text.upper())
            self._status.set_wrap(True)
            self._status.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self._status.set_lines(2)
            self._status.set_ellipsize(Pango.EllipsizeMode.END)
            self._status.set_max_width_chars(18)
            if recording:
                self._status.add_css_class("caption-rec-on")
            else:
                self._status.remove_css_class("caption-rec-on")

        def set_active(self, active: bool, _source: str = "") -> None:
            self._toggle.set_sensitive(True)
            if active:
                self.set_status("Captions on", recording=True)
                self._toggle.set_label("STOP")
            else:
                self.set_status("Captions off")
                self._toggle.set_label("START")

        def set_starting(self) -> None:
            self.set_status("Starting\u2026")
            # No second Start while the engine warms up.
            self._toggle.set_sensitive(False)

        def set_error(self, message: str) -> None:
            # Red like the on state: something needs the user's eye.
            self.set_status(message, recording=True)
            self._toggle.set_sensitive(True)

        def _on_toggle_clicked(self, _button) -> None:
            client = self._client
            if client is None or not client.connected:
                self.set_error("service unavailable")
                return
            if client.active:
                client.stop(done=self._on_toggle_done)
            else:
                self.set_starting()
                client.start("playback", done=self._on_toggle_done)

        def _on_toggle_done(self, error: str | None) -> None:
            if error:
                GLib.idle_add(self.set_error, error)
            else:
                GLib.idle_add(self._refresh_props_idle)

        def _refresh_props_idle(self) -> bool:
            try:
                if self._client is not None:
                    self._client.refresh_props()
            except Exception:
                pass
            return False

        def _on_more_clicked(self, _button) -> None:
            try:
                self._menu.popup()
            except Exception:
                pass

        def _on_close_clicked(self, _button=None) -> None:
            try:
                if self._client is not None and self._client.active:
                    self._client.stop()
            except Exception:
                pass
            self.close()

        def _on_download_clicked(self, _button) -> None:
            if self._downloading:
                return
            self._downloading = True
            self._cancel_download.clear()
            self._model_error.set_visible(False)
            self._progress.set_visible(True)
            self._progress.set_fraction(0.0)
            self._cancel.set_visible(True)
            self._download.set_visible(False)
            thread = threading.Thread(target=self._download_worker,
                                      daemon=True)
            thread.start()

        def _on_cancel_clicked(self, _button) -> None:
            self._cancel_download.set()

        def _download_progress(self, done: int, total: int | None) -> None:
            if self._cancel_download.is_set():
                raise DownloadCancelled("cancelled")
            fraction = progress_fraction(done, total)
            GLib.idle_add(self._progress.set_fraction, fraction)

        def _download_worker(self) -> None:
            error: str | None = None
            try:
                models.download(self._model_id, self._home,
                                progress=self._download_progress)
            except DownloadCancelled:
                error = ""
            except Exception as exc:
                _log.warning("model download failed: %s", exc)
                error = "Download failed"
            GLib.idle_add(self._on_download_finished, error)

        def _on_download_finished(self, error: str | None) -> bool:
            self._downloading = False
            self._cancel.set_visible(False)
            if error is None:
                self.show_caption_view()
            else:
                self._download.set_visible(True)
                self._progress.set_visible(False)
                # A cancel the user just tapped needs no message.
                self._model_error.set_text(error)
                self._model_error.set_visible(bool(error))
            return False

        def apply_margin(self, margin: int) -> int:
            clamped = clamp_margin(margin)
            self._margin = clamped
            if layer_available():
                try:
                    LayerShell.set_margin(
                        self, LayerShell.Edge.BOTTOM, clamped)
                except Exception:
                    pass
            return clamped

        def _on_drag_begin(self, _gesture, _x: float, _y: float) -> None:
            self._drag_start_margin = self._margin

        def _on_drag_update(self, gesture, _x: float, _y: float) -> None:
            try:
                _ok, _offset_x, offset_y = gesture.get_offset()
            except Exception:
                return
            self.apply_margin(int(self._drag_start_margin - offset_y))

        def _on_drag_end(self, _gesture, _x: float, _y: float) -> None:
            try:
                save_margin(self._margin, self._home)
            except Exception:
                pass

        def _on_realize(self, _widget) -> None:
            self._refresh_input_region()

        def _refresh_input_region(self) -> bool:
            """Pass taps through except on the handle and overflow button."""
            try:
                import cairo
            except ImportError:
                return False
            try:
                surface = self.get_surface()
                if surface is None:
                    return False
                region = cairo.Region()
                for widget in (self._handle, self._more):
                    alloc = widget.get_allocation()
                    rect = cairo.RectangleInt(alloc.x, alloc.y,
                                             alloc.width, alloc.height)
                    region = region.union_rectangle(rect)
                surface.set_input_region(region)
            except Exception:
                pass
            return False

    class CaptionApp(Gtk.Application):
        """Owns the window and the service client for one overlay."""

        def __init__(self, home=None) -> None:
            super().__init__(application_id=APPLICATION_ID)
            self._home = home
            self._window: CaptionWindow | None = None

        def do_activate(self) -> None:
            if self._window is None:
                client = CaptionsClient(on_text=self._on_text,
                                        on_props=self._on_props)
                window = CaptionWindow(home=self._home, client=client)
                client._on_text = window.on_text
                client._on_props = window.set_active
                client.connect()
                client.refresh_props()
                window.set_application(self)
                window.present()
                self._window = window
            else:
                self._window.present()

        def _on_text(self, _text: str, _final: bool) -> None:
            pass

        def _on_props(self, _active: bool, _source: str) -> None:
            pass


def run_overlay(home=None) -> int:
    """Show the overlay; 2 when GTK is unavailable (never crashes)."""
    if not _HAS_GTK:
        _log.error("overlay needs GTK4; not available")
        return 2
    config.ensure_dirs(home)
    app = CaptionApp(home)
    return int(app.run([]))


__all__ = [
    "APPLICATION_ID",
    "RECORD_RED",
    "CaptionApp",
    "CaptionWindow",
    "DownloadCancelled",
    "ensure_ui",
    "layer_available",
    "load_ui_theme",
    "render_app_css",
    "run_overlay",
]
