"""Settings and transcript history (`xx-captions --settings`).

One undecorated window on the xx_ui design system: engine and language
pickers, the model download card, the call-transcription opt-in, and
the on-device transcript history with a detail page per call. Every
value shown here is read from (and written back through) the real
config and transcript store; without GTK the module still imports and
run_settings() returns 2.
"""
from __future__ import annotations

import logging
import threading
import time

from xx_captions import config, models
from xx_captions.overlay_state import progress_fraction
from xx_captions.store import TranscriptStore

_log = logging.getLogger("captions-settings")

APPLICATION_ID = "io.piercingxx.CaptionsSettings"

ENGINE_OPTIONS = (("sherpa-onnx", "Sherpa"), ("vosk", "Vosk"))
ENGINE_DEFAULT_MODEL = {
    "sherpa-onnx": config.SHERPA_20M_ID,
    "vosk": config.VOSK_ID,
}
LANGUAGE_OPTIONS = ("en", "de", "fr", "es")


def initials_for(peer: object) -> str:
    """Avatar initials for a peer name or number."""
    words = str(peer or "").split()
    if not words:
        return "?"
    if words[0][0] in "+0123456789":
        return words[0][:2]
    return "".join(word[0] for word in words[:2]).upper()


def format_day(started: object) -> str:
    """Short day stamp for a row subtitle, e.g. 'Oct 16'."""
    try:
        return time.strftime("%b %d", time.localtime(int(started)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def format_time(started: object) -> str:
    """Detail subtitle stamp, e.g. 'Oct 16, 07:05 PM'."""
    try:
        return time.strftime("%b %d, %I:%M %p",
                             time.localtime(int(started)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def format_duration(seconds: object) -> str:
    """M:SS call length, e.g. '4:12'."""
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError):
        return "0:00"
    return f"{total // 60}:{total % 60:02d}"


def format_offset(offset_ms: object) -> str:
    """M:SS line offset from milliseconds, e.g. '0:09'."""
    try:
        total = max(0, int(offset_ms) // 1000)
    except (TypeError, ValueError):
        return "0:00"
    return f"{total // 60}:{total % 60:02d}"


def short_model_name(model_id: object) -> str:
    """Human model name for titles; unknown ids pass through."""
    model_id = str(model_id or "")
    if model_id == config.VOSK_ID:
        return "Vosk small English"
    if model_id in config.MODEL_CATALOG:
        return "Sherpa streaming Zipformer"
    return model_id or "Unknown model"


def model_state(home, model_id: str) -> tuple[bool, int]:
    """(installed, size_mb) for the model card."""
    entry = config.MODEL_CATALOG.get(model_id, {})
    try:
        size_mb = int(entry.get("size_mb") or 0)
    except (TypeError, ValueError):
        size_mb = 0
    return models.is_installed(model_id, home), size_mb


def model_status(installed: bool, size_mb: int,
                 downloading: bool = False) -> str:
    """Model card subtitle, e.g. '42 MB · Installed'."""
    if downloading:
        state = "Downloading"
    else:
        state = "Installed" if installed else "Not installed"
    return f"{size_mb} MB · {state}" if size_mb > 0 else state


def read_settings(home) -> dict:
    """Engine/language/opt-in snapshot for this HOME."""
    data = config.load(home)
    engine = data.get("engine", "sherpa-onnx")
    if engine not in ENGINE_DEFAULT_MODEL:
        engine = "sherpa-onnx"
    language = data.get("language", "en")
    if language not in LANGUAGE_OPTIONS:
        language = "en"
    return {
        "engine": engine,
        "model": data.get("model", config.DEFAULT_CONFIG["model"]),
        "language": language,
        "call_transcription_enabled": bool(
            data.get("call_transcription_enabled", False)),
    }


def write_setting(home, key: str, value) -> None:
    """Persist one settings value through the real config."""
    data = config.load(home)
    data[key] = value
    config.save(data, home)


_HAS_GTK = True
try:
    import gi
except ImportError:
    _HAS_GTK = False

if _HAS_GTK:
    gi.require_version("Gtk", "4.0")
    try:
        gi.require_version("Pango", "1.0")
    except (ValueError, ImportError):
        pass
    from gi.repository import Gdk, GLib, Gtk, Pango

    from xx_captions.overlay import DownloadCancelled, ensure_ui
    from xx_ui import widgets


if _HAS_GTK:
    def _wrap_two(line: Gtk.Label) -> Gtk.Label:
        """Wrap a one-line label to two lines (large text, 320 px) and
        ellipsize only past that; it never widens the window."""
        line.set_wrap(True)
        line.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        line.set_lines(2)
        line.set_ellipsize(Pango.EllipsizeMode.END)
        return line

    class SettingsWindow(Gtk.Window):
        """Captions settings with transcript history and detail pages."""

        def __init__(self, home=None) -> None:
            super().__init__()
            self._home = home
            self._settings = read_settings(home)
            self._store: TranscriptStore | None = None
            self._downloading = False
            self._cancel_download = threading.Event()
            self._model_progress: Gtk.ProgressBar | None = None

            ensure_ui(self)
            self.set_title("xx-captions")
            self.set_decorated(False)
            self.set_default_size(360, 720)

            self._stack = Gtk.Stack()
            self._stack.add_named(self._build_main(), "main")
            self._detail_body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
            self._detail_page = self._build_detail()
            self._stack.add_named(self._detail_page, "detail")
            self.set_child(self._stack)

            self.connect("close-request", self._on_close_request)
            keys = Gtk.EventControllerKey.new()
            keys.connect("key-pressed", self._on_key)
            self.add_controller(keys)

        def _on_key(self, _ctl, keyval: int, _code: int, _state) -> bool:
            """Escape (and the phone's back) steps back, then closes."""
            if keyval not in (Gdk.KEY_Escape, Gdk.KEY_Back):
                return False
            if self._stack.get_visible_child_name() == "detail":
                self.show_main()
            else:
                self.close()
            return True

        def _store_open(self) -> TranscriptStore:
            if self._store is None:
                self._store = TranscriptStore(self._home)
            return self._store

        def _on_close_request(self, _window) -> bool:
            store, self._store = self._store, None
            if store is not None:
                try:
                    store.close()
                except Exception:
                    pass
            return False

        def _build_main(self) -> Gtk.Widget:
            header = widgets.page_header(
                "Captions", "On-device live captions")
            body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
            body.append(widgets.section_label("Engine"))
            body.append(widgets.segmented([
                (label, self._settings["engine"] == key,
                 lambda key=key: self._on_engine(key))
                for key, label in ENGINE_OPTIONS
            ]))
            body.append(widgets.section_label("Model"))
            self._model_card = widgets.card()
            body.append(self._model_card)
            self._refresh_model_card()
            body.append(widgets.section_label("Language"))
            body.append(widgets.segmented([
                (code.upper(), self._settings["language"] == code,
                 lambda code=code: self._on_language(code))
                for code in LANGUAGE_OPTIONS
            ]))
            body.append(widgets.section_label("Options"))
            body.append(widgets.setting_row(
                "Call transcription",
                "Never leaves this phone.",
                widgets.make_switch(
                    self._settings["call_transcription_enabled"],
                    self._on_call_opt_in),
                last=True))
            self._history_label = widgets.section_label("History")
            body.append(self._history_label)
            self._history_box = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
            body.append(self._history_box)
            self.refresh_history()
            return widgets.page(header, body)

        def _build_detail(self) -> Gtk.Widget:
            header = widgets.compact_header(
                self.show_main, "", "")
            middle = header.get_first_child().get_next_sibling()
            self._detail_title = middle.get_first_child()
            self._detail_sub = self._detail_title.get_next_sibling()
            if self._detail_sub is None:
                self._detail_sub = widgets.label("", "xx-secondary")
                middle.append(self._detail_sub)
            _wrap_two(self._detail_sub)
            return widgets.page(header, self._detail_body)

        @property
        def history_ids(self) -> list[str]:
            try:
                return [row[0] for row in self._store_open()
                        .list_transcripts()]
            except Exception:
                return []

        def show_main(self) -> None:
            self._stack.set_visible_child_name("main")

        def show_detail(self, call_id: str) -> None:
            try:
                peer, started, ended, lines = self._store_open() \
                    .get_transcript(call_id)
            except Exception:
                return
            self._detail_title.set_text(
                str(peer or "Unknown call").upper())
            self._detail_sub.set_text(
                f"{format_time(started)} · "
                f"{format_duration(ended - started)}" if ended else
                format_time(started))
            self._detail_sub.set_visible(
                bool(self._detail_sub.get_text()))
            child = self._detail_body.get_first_child()
            while child is not None:
                following = child.get_next_sibling()
                self._detail_body.remove(child)
                child = following
            for text, offset_ms in lines:
                self._detail_body.append(
                    widgets.caption(format_offset(offset_ms)))
                self._detail_body.append(
                    widgets.wrap_label(str(text), "xx-body"))
            delete = widgets.danger_button(
                "Delete",
                lambda call_id=call_id: self._on_delete(call_id))
            delete.set_hexpand(True)
            self._detail_body.append(delete)
            self._stack.set_visible_child_name("detail")

        def _on_delete(self, call_id: str) -> None:
            try:
                self._store_open().delete_transcript(call_id)
            except Exception:
                pass
            self.refresh_history()
            self.show_main()

        def refresh_history(self) -> None:
            box = self._history_box
            child = box.get_first_child()
            while child is not None:
                following = child.get_next_sibling()
                box.remove(child)
                child = following
            try:
                rows = self._store_open().list_transcripts()
            except Exception:
                rows = []
            # An empty history shows nothing, not even its heading.
            self._history_label.set_visible(bool(rows))
            for pos, (call_id, peer, started, ended) in enumerate(rows):
                peer = str(peer or "Unknown call")
                row = widgets.list_row(
                    peer, format_day(started),
                    meta=format_duration(ended - started) if ended else "",
                    leading=widgets.avatar(initials_for(peer), 40),
                    on_activate=lambda call_id=call_id:
                        self.show_detail(call_id),
                    last=pos == len(rows) - 1)
                box.append(row)

        def _on_engine(self, key: str) -> None:
            write_setting(self._home, "engine", key)
            write_setting(self._home, "model", ENGINE_DEFAULT_MODEL[key])
            self._settings = read_settings(self._home)
            self._refresh_model_card()

        def _on_language(self, code: str) -> None:
            write_setting(self._home, "language", code)
            self._settings = read_settings(self._home)

        def _on_call_opt_in(self, active: bool) -> None:
            write_setting(self._home, "call_transcription_enabled",
                          bool(active))
            self._settings = read_settings(self._home)

        def _refresh_model_card(self) -> None:
            card = self._model_card
            child = card.get_first_child()
            while child is not None:
                following = child.get_next_sibling()
                card.remove(child)
                child = following
            model_id = self._settings["model"]
            installed, size_mb = model_state(self._home, model_id)
            card.append(_wrap_two(widgets.label(short_model_name(model_id),
                                                "xx-title")))
            card.append(_wrap_two(widgets.label(
                model_status(installed, size_mb, self._downloading),
                "xx-secondary")))
            self._model_progress = Gtk.ProgressBar()
            self._model_progress.set_show_text(False)
            self._model_progress.set_visible(self._downloading)
            card.append(self._model_progress)
            buttons = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
            if self._downloading:
                cancel = widgets.outline_button("Cancel",
                                                self._on_model_cancel)
                cancel.set_hexpand(True)
                buttons.append(cancel)
            elif installed:
                delete = widgets.outline_button("Delete",
                                                self._on_model_delete)
                delete.set_hexpand(True)
                buttons.append(delete)
            else:
                download = widgets.primary_button(
                    "Download", self._on_model_download)
                download.set_hexpand(True)
                buttons.append(download)
            card.append(buttons)

        def _on_model_download(self) -> None:
            if self._downloading:
                return
            self._downloading = True
            self._cancel_download.clear()
            self._refresh_model_card()
            thread = threading.Thread(target=self._download_worker,
                                      daemon=True)
            thread.start()

        def _on_model_cancel(self) -> None:
            self._cancel_download.set()

        def _on_model_delete(self) -> None:
            try:
                models.delete(self._settings["model"], self._home)
            except Exception as exc:
                _log.warning("model delete failed: %s", exc)
            self._refresh_model_card()

        def _download_worker(self) -> None:
            model_id = self._settings["model"]

            def progress(done: int, total: int | None) -> None:
                if self._cancel_download.is_set():
                    raise DownloadCancelled("cancelled")
                GLib.idle_add(self._on_download_progress,
                              progress_fraction(done, total))

            try:
                models.download(model_id, self._home, progress=progress)
            except DownloadCancelled:
                pass
            except Exception as exc:
                _log.warning("model download failed: %s", exc)
            GLib.idle_add(self._on_download_finished)

        def _on_download_progress(self, fraction: float) -> bool:
            if self._model_progress is not None:
                self._model_progress.set_fraction(fraction)
            return False

        def _on_download_finished(self) -> bool:
            self._downloading = False
            self._refresh_model_card()
            return False

    class SettingsApp(Gtk.Application):
        """Owns one settings window."""

        def __init__(self, home=None) -> None:
            super().__init__(application_id=APPLICATION_ID)
            self._home = home
            self._window: SettingsWindow | None = None

        def do_activate(self) -> None:
            if self._window is None:
                window = SettingsWindow(home=self._home)
                window.set_application(self)
                window.present()
                self._window = window
            else:
                self._window.present()


def run_settings(home=None) -> int:
    """Show the settings window; 2 when GTK is unavailable."""
    if not _HAS_GTK:
        _log.error("settings need GTK4; not available")
        return 2
    config.ensure_dirs(home)
    app = SettingsApp(home)
    return int(app.run([]))


__all__ = [
    "APPLICATION_ID",
    "ENGINE_DEFAULT_MODEL",
    "ENGINE_OPTIONS",
    "LANGUAGE_OPTIONS",
    "SettingsApp",
    "SettingsWindow",
    "format_day",
    "format_duration",
    "format_offset",
    "format_time",
    "initials_for",
    "model_state",
    "model_status",
    "read_settings",
    "run_settings",
    "short_model_name",
    "write_setting",
]
