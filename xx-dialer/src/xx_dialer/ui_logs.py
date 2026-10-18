"""Logs page: the app's rotated diagnostics with numbers redacted.

Reads ``~/.local/share/xx-dialer/logs/`` through ``applog`` — every
preview passes through ``applog.redact`` so phone numbers (digit runs
of 7+) never reach the screen while short ids and counts stay
readable. The preview never takes focus (no on-screen keyboard).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Pango  # noqa: E402

from xx_ui import widgets  # noqa: E402
from xx_dialer import applog  # noqa: E402


def _outline(text: str, callback=None):
    """An outline button whose label wraps between words (large text,
    320 px) instead of ellipsizing; it never widens the window."""
    return widgets.wrap_to_fit(widgets.outline_button(text, callback))


@dataclass
class LogsDeps:
    db_file: Path
    home: Path | None = None
    notice: object = None
    navigate: object = None


class LogsController:
    """Log listing + redacted previews; the view renders strings."""

    def __init__(self, deps: LogsDeps) -> None:
        self._deps = deps

    def _notice(self, text: str) -> None:
        if self._deps.notice is not None:
            try:
                self._deps.notice(text)
            except Exception:
                pass

    def files(self) -> list[tuple[str, int, float]]:
        return applog.list_logs(self._deps.home)

    def preview(self, name: str) -> str:
        text = applog.read_log(name, self._deps.home)
        if not text:
            return "No log lines yet."
        return text

    def clear(self) -> str:
        applog.clear_logs(self._deps.home)
        status = "Cleared the logs."
        self._notice(status)
        return status


class LogsView:
    """File buttons plus a redacted, unfocusable preview."""

    def __init__(self, controller: LogsController) -> None:
        self.controller = controller
        navigate = getattr(controller._deps, "navigate", None)
        self._navigate = navigate if callable(navigate) else None
        self._root: Gtk.Widget | None = None
        self._selected: str | None = None
        self._build()
        self.refresh()

    @property
    def widget(self) -> Gtk.Widget:
        assert self._root is not None
        return self._root

    def _go_recents(self) -> None:
        if self._navigate is not None:
            self._navigate("recents")

    def _build(self) -> None:
        if self._navigate is not None:
            header = widgets.compact_header(self._go_recents, "Logs")
        else:
            header = widgets.page_header("Logs")
        self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             spacing=8)
        self._body.add_css_class("xx-page-body")

        self._body.append(widgets.section_label("Log files"))
        note = widgets.wrap_label("Phone numbers are masked.")
        self._body.append(note)
        self._file_list = Gtk.ListBox()
        self._file_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._body.append(self._file_list)
        brow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._refresh_button = widgets.wrap_to_fit(
            _outline("Refresh"))
        self._refresh_button.set_hexpand(True)
        self._refresh_button.connect("clicked", self._on_refresh)
        brow.append(self._refresh_button)
        self._clear_button = widgets.wrap_to_fit(
            _outline("Clear logs"))
        self._clear_button.set_hexpand(True)
        self._clear_button.connect("clicked", self._on_clear)
        brow.append(self._clear_button)
        self._body.append(brow)

        self._body.append(widgets.section_label("Preview"))
        self._preview_scroll = Gtk.ScrolledWindow()
        self._preview_scroll.set_min_content_height(240)
        self._preview = Gtk.TextView()
        self._preview.set_editable(False)
        self._preview.set_can_focus(False)
        self._preview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._preview.add_css_class("xx-entry")
        self._preview_scroll.set_child(self._preview)
        self._body.append(self._preview_scroll)

        self._root = widgets.page(header, self._body)

    def refresh(self) -> None:
        while True:
            row = self._file_list.get_row_at_index(0)
            if row is None:
                break
            self._file_list.remove(row)
        try:
            files = self.controller.files()
        except Exception:
            files = []
        for name, size, _mtime in files:
            button = Gtk.Button()
            button.add_css_class("xx-menu-row")
            text = Gtk.Label(label="%s (%d bytes)" % (name, size),
                             xalign=0.0)
            text.add_css_class("xx-title")
            text.set_ellipsize(Pango.EllipsizeMode.END)
            button.set_child(text)
            button.connect("clicked", self._on_pick, name)
            self._file_list.append(button)
            if self._selected is None:
                self._selected = name
        if self._selected is not None and files:
            names = [name for name, _size, _mtime in files]
            if self._selected not in names:
                self._selected = names[0]
            self._show(self._selected)

    def _show(self, name: str) -> None:
        try:
            text = self.controller.preview(name)
        except Exception:
            text = "Could not read that log."
        text = "\n".join(text.splitlines()[-120:])
        self._preview.get_buffer().set_text(text)
        adj = self._preview_scroll.get_vadjustment()
        if adj is not None:
            adj.set_value(adj.get_upper())

    def _on_pick(self, _button: Gtk.Button, name: str) -> None:
        self._selected = name
        self._show(name)

    def _on_refresh(self, _button: Gtk.Button) -> None:
        self.refresh()

    def _on_clear(self, _button: Gtk.Button) -> None:
        self.controller.clear()
        self._selected = None
        self.refresh()
        self._preview.get_buffer().set_text("")


def build_logs(deps: LogsDeps) -> LogsView:
    return LogsView(LogsController(deps))
