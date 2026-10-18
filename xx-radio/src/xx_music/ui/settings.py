"""Settings: the app's own sections (account, playback engine), then
Playback, Downloads and storage, Diagnostics and About.

Every value lives in ``config.json`` through the window's store (on the
worker). The diagnostics block is redacted before it reaches the
clipboard; the log view shows the redacted tail of the log file.

Playback opens with Minimal Mode (off by default): flipping it saves
the setting and rebuilds every screen at once, no restart. In Minimal
Mode the switches are ``[ on ]`` / ``[ off ]`` and rows open with ``▸``.
"""

from __future__ import annotations

from gi.repository import Gtk

from xx_music import __version__ as core_version
from xx_music import diagnostics
from xx_music.cache import VALID_CAP_CHOICES_GB
from xx_music.engine import STREAM
from xx_music.ui import common, css, fmt, minimal
from xx_ui import widgets

MINIMAL_TITLE = "Minimal mode"
MINIMAL_SUB = "Text only: no art, icons or tiles"

LOG_LINES = 200
DIAG_SUB = "Versions and recent errors, no sign-in data"
LOG_SUB = "Sign-in data hidden"


def switch_row(title: str, subtitle: str, active: bool, on_change,
               last: bool = False) -> Gtk.Widget:
    if minimal.active():
        row = widgets.setting_row(title, subtitle, on_off_button(
            title, active, on_change), last=last)
        _wrap_titles(row)
        return row
    switch = widgets.make_switch(active, on_change)
    switch.update_property([Gtk.AccessibleProperty.LABEL], [title])
    row = widgets.setting_row(title, subtitle, switch, last=last)
    _wrap_titles(row)
    return row


def on_off_button(title: str, active: bool, on_change) -> Gtk.Button:
    """Minimal Mode's switch: ``[ on ]`` Strong or ``[ off ]`` Quiet."""
    state = {"on": bool(active)}
    button = minimal.bracket_button(
        minimal.on_off(state["on"]), None, minimal.toggled(state["on"]),
        spoken=f"{title}, {minimal.on_off(state['on'])}")

    def flip(_b) -> None:
        state["on"] = not state["on"]
        button.get_child().set_text(minimal.bracket(
            minimal.on_off(state["on"])))
        minimal.set_tone(button, minimal.toggled(state["on"]))
        common.name(button, f"{title}, {minimal.on_off(state['on'])}")
        on_change(state["on"])

    button.connect("clicked", flip)
    button.xx_state = state
    return button


def _wrap_titles(row: Gtk.Widget) -> None:
    """Let a setting row's subtitle wrap to two lines (320 px, 2x text)."""
    middle = row.get_first_child()
    child = middle.get_first_child() if middle else None
    while child is not None:
        if isinstance(child, Gtk.Label) and child.has_css_class(
                "xx-secondary"):
            child.set_wrap(True)
            child.set_lines(2)
            child.set_max_width_chars(28)
        child = child.get_next_sibling()


def nav_row(title: str, subtitle: str, callback, last: bool = False,
            trailing: Gtk.Widget | None = None,
            arrow: bool = True) -> Gtk.Button:
    """A settings row that opens something. ``arrow=False`` drops the
    trailing chevron (and its Minimal Mode mark): the About block's
    licenses row carries no decorative glyph."""
    if not arrow:
        row = widgets.list_row(title, subtitle, trailing=trailing,
                               on_activate=callback, last=last)
        _wrap_secondary(row)
        return row
    if minimal.active() and (trailing is None or isinstance(trailing, str)):
        trailing = minimal.text(trailing or minimal.OPENS,
                                minimal.Tone.QUIET, 15, chars=6)
    elif isinstance(trailing, str):
        trailing = None
    row = widgets.list_row(title, subtitle,
                           trailing=trailing or widgets.chevron(),
                           on_activate=callback, last=last)
    _wrap_secondary(row)
    return row


def _wrap_secondary(row: Gtk.Button) -> None:
    middle = row.get_child().get_first_child()
    child = middle.get_first_child() if middle else None
    while child is not None:
        if isinstance(child, Gtk.Label) and child.has_css_class(
                "xx-secondary"):
            child.set_wrap(True)
            child.set_lines(2)
            child.set_max_width_chars(26)
        child = child.get_next_sibling()


def section(text: str) -> Gtk.Widget:
    return widgets.section_label(text)


class SettingsPage:
    def __init__(self, ui) -> None:
        self.ui = ui
        self.prefs = {}
        self.body = common.vbox(0)
        self.body.set_margin_bottom(32)
        root = common.vbox(0)
        root.append(widgets.page_header("Settings"))
        root.append(common.scroller(self.body))
        self.widget = root
        self._app_sections()
        self.playback_box = common.vbox(0)
        self.downloads_box = common.vbox(0)
        self.blocked_box = common.vbox(0)
        self.diagnostics_box = common.vbox(0)
        self.about_box = common.vbox(0)
        for box in (self.playback_box, self.downloads_box, self.blocked_box,
                    self.diagnostics_box, self.about_box):
            self.body.append(box)
        self._paint_static()
        for box in (self.body,):
            box.add_css_class("xx-page-body")
        ui.io(ui.store.prefs, self._got_prefs, lambda _e: None)
        ui.on_downloads(self._paint_downloads)
        minimal.listen(self._minimal_changed)

    def _paint_static(self) -> None:
        common.clear(self.diagnostics_box)
        self.diagnostics_box.append(self._diagnostics())
        common.clear(self.about_box)
        self.about_box.append(self._about())

    def _minimal_changed(self) -> None:
        self._paint_static()
        if self.prefs:
            self._paint_playback()
            self._paint_downloads()
            self._paint_blocked()

    def shown(self) -> None:
        self._paint_blocked()

    # -- app sections ------------------------------------------------------------------
    def _app_sections(self) -> None:
        keys = set()
        for item in self.ui.hooks.settings:
            keys.add(item.key)
            self.body.append(item.build(self.ui))
        if "engine" not in keys:
            self.body.append(self._engine_rows())

    def _engine_rows(self) -> Gtk.Widget:
        box = common.vbox(0)
        box.append(section("Playback engine"))
        rows = common.vbox(0)
        rows.append(widgets.setting_row(self.ui.engine.name, "Checking…",
                                        last=True))
        box.append(rows)

        def show(status) -> None:
            common.clear(rows)
            items = [(c.name, fmt.joined(c.state_text, c.version,
                                         c.needed_for))
                     for c in status.components] or [
                (self.ui.engine.name,
                 "Ready" if status.can_play else "Not set up")]
            for index, (title, sub) in enumerate(items):
                rows.append(widgets.setting_row(
                    title, sub, last=index == len(items) - 1))

        def failed(error) -> None:
            common.clear(rows)
            rows.append(widgets.setting_row(
                self.ui.engine.name,
                getattr(error, "user_text", "Something went wrong."),
                last=True))

        self.ui.run("status", self.ui.engine.status, show, failed)
        return box

    # -- playback and downloads ------------------------------------------------------------
    def _got_prefs(self, prefs: dict) -> None:
        self.prefs = prefs
        self.ui.prefs = prefs
        self._paint_playback()
        self._paint_downloads()
        self._paint_blocked()

    def _paint_blocked(self) -> None:
        from xx_music.blocked import blocked_summary

        box = self.blocked_box
        common.clear(box)
        box.append(section("Killed artists"))
        try:
            entries = list(self.ui.blocked.list())
        except Exception:  # noqa: BLE001 - settings never fail
            entries = []
        # The heading names the list; the row says what is on it. Thumbs
        # down songs have one home: Library > Thumbs down.
        box.append(nav_row("Artists", blocked_summary(len(entries)),
                           self.open_blocked))
        box.append(switch_row(
            "Remove their downloads too",
            "Delete downloads by killed artists instead of hiding them",
            bool(self.prefs.get("remove_blocked_downloads")),
            lambda on: self._set("remove_blocked_downloads", on),
            last=True))

    def open_blocked(self) -> None:
        self.ui.shell.push(BlockedView(self.ui).widget, "Killed artists")

    def _set(self, key: str, value) -> None:
        self.prefs[key] = value
        self.ui.io(lambda: self.ui.store.set_pref(key, value),
                   lambda p: self.prefs.update(p))

    def set_minimal(self, on: bool) -> None:
        """Save Minimal Mode and apply it now (after this click returns:
        the row that was clicked is rebuilt too)."""
        from gi.repository import GLib

        self._set("minimal_mode", bool(on))
        GLib.idle_add(lambda: (self.ui.set_minimal(bool(on)), False)[1])

    def _paint_playback(self) -> None:
        box = self.playback_box
        common.clear(box)
        box.append(section("Playback"))
        streams = self.ui.supports(STREAM)
        box.append(switch_row(
            MINIMAL_TITLE, MINIMAL_SUB, bool(self.prefs.get("minimal_mode")),
            self.set_minimal, last=not streams))
        if not streams:
            return
        box.append(switch_row(
            "Data saver", "Lower quality on mobile data",
            bool(self.prefs.get("data_saver")),
            lambda on: self._set("data_saver", on)))
        box.append(switch_row(
            "Even loudness", "Level songs to the same volume",
            bool(self.prefs.get("loudness_leveling")),
            lambda on: self._set("loudness_leveling", on), last=True))

    def _paint_downloads(self) -> None:
        if not self.prefs:
            return
        box = self.downloads_box
        common.clear(box)
        if not self.ui.supports(STREAM):
            return
        box.append(section("Downloads and storage"))
        cap = int(self.prefs.get("storage_cap_gb") or 2)
        done = [r for r in self.ui.downloads if r.state == "done"]
        used = sum(r.size_bytes or 0 for r in done)
        if minimal.active():
            from xx_music.ui.library import storage_lines

            box.append(storage_lines(
                used, cap, f"{fmt.songs(len(done))} downloaded · 1 GB "
                "always left free"))
        else:
            box.append(self._meter(used, cap, done))
        box.append(nav_row("Storage limit", f"{cap} GB for downloads and "
                           "songs fetched ahead", self.open_cap))
        box.append(switch_row(
            "Download on Wi-Fi only", "Mobile data never fills the phone",
            bool(self.prefs.get("wifi_only")),
            lambda on: self._set("wifi_only", on)))
        box.append(switch_row(
            "Keep liked songs", "Download songs you like",
            bool(self.prefs.get("keep_liked_offline")),
            lambda on: self._set("keep_liked_offline", on), last=True))

    def _meter(self, used: int, cap: int, done: list) -> Gtk.Widget:
        meter = common.vbox(8)
        meter.add_css_class("xx-mu-block")
        meter.set_margin_bottom(8)
        top = Gtk.Box.new(Gtk.Orientation.VERTICAL if css.large_text()
                          else Gtk.Orientation.HORIZONTAL, 8)
        figure = common.label(fmt.size(used), "xx-mu-h2 xx-mu-num", chars=10)
        figure.set_hexpand(True)
        figure.set_ellipsize(0)
        top.append(figure)
        of = common.label(f"of {cap} GB", "xx-secondary xx-mu-num",
                          xalign=1.0, chars=10)
        of.set_ellipsize(0)
        top.append(of)
        meter.append(top)
        meter.append(common.meter(used / (cap * 2**30)))
        meter.append(common.label(
            f"{fmt.songs(len(done))} downloaded · 1 GB always left free",
            "xx-mu-meta", lines=2, chars=34))
        return meter

    def open_cap(self):
        dialog, box = common.sheet("Storage limit")
        box.append(common.para(
            "Downloads and songs fetched ahead share this space. The "
            "oldest songs fetched ahead make room first; downloads stay.",
            "xx-secondary"))
        current = int(self.prefs.get("storage_cap_gb") or 2)
        slot = common.vbox(0)
        slot.set_margin_top(10)
        box.append(slot)

        def paint(selected: int) -> None:
            common.clear(slot)
            slot.append(common.grid_chips([
                (f"{gb} GB", gb == selected, lambda gb=gb: pick(gb))
                for gb in VALID_CAP_CHOICES_GB], per_line=3))

        def pick(gb: int) -> None:
            self._set("storage_cap_gb", gb)
            paint(gb)
            self._paint_downloads()

        paint(current)
        return common.present(dialog, self.ui.shell.window)

    # -- diagnostics -----------------------------------------------------------------------
    def _diagnostics(self) -> Gtk.Widget:
        box = common.vbox(0)
        box.append(section("Diagnostics"))
        self._diag_row = nav_row(
            "Copy diagnostics", DIAG_SUB, self.copy_diagnostics,
            trailing="copy" if minimal.active() else
            widgets.icon_image("edit-copy-symbolic"))
        box.append(self._diag_row)
        box.append(nav_row("Log", "What the app did recently",
                           self.open_log, last=True))
        return box

    def diagnostics_text(self) -> str:
        """The redacted block (worker: may ask the engine)."""
        ui = self.ui
        if ui.hooks.engine_lines is not None:
            lines = list(ui.hooks.engine_lines(ui))
        else:
            try:
                status = ui.engine.status()
                lines = [fmt.joined(c.name, c.state_text, c.version)
                         for c in status.components]
            except Exception:  # noqa: BLE001 - diagnostics never fail
                lines = []
        recent = ui.health.recent_errors()
        last = {e.capability: e.kind for e in recent}
        capabilities = {cap: last.get(cap, "ok")
                        for cap in sorted(ui.engine.capabilities)}
        errors = [diagnostics.ErrorRecord(e.when, e.capability, e.kind)
                  for e in recent]
        return diagnostics.collect(
            ui.spec.name, ui.hooks.version or core_version,
            engine_lines=[f"core {core_version}", *lines],
            capabilities=capabilities, errors=errors)

    def copy_diagnostics(self) -> None:
        def done(text: str) -> None:
            self.widget.get_clipboard().set(text)
            row = getattr(self, "_diag_row", None)
            if row is not None:
                common.copied(row, DIAG_SUB)

        self.ui.io(self.diagnostics_text, done, lambda _e: None)

    def open_log(self) -> None:
        self.ui.shell.push(LogView(self.ui).widget, "Log")

    # -- about ---------------------------------------------------------------------------------
    def _about(self) -> Gtk.Widget:
        ui = self.ui
        box = common.vbox(0)
        box.append(section("About"))
        version = ui.hooks.version or core_version
        box.append(widgets.setting_row(ui.spec.name, f"Version {version}"))
        if ui.hooks.disclaimer:
            note = common.para(ui.hooks.disclaimer, "xx-mu-meta", 34)
            note.set_margin_top(12)
            note.set_margin_bottom(12)
            box.append(note)
        entries = ui.hooks.licenses() if ui.hooks.licenses else \
            widgets.standard_license_entries(ui.spec.name)
        box.append(nav_row("Open-source licenses",
                           f"{len(entries)} components, GPL-3.0 app",
                           lambda: self.open_licenses(entries), last=True,
                           arrow=False))
        return box

    def open_licenses(self, entries: list) -> None:
        nav = self.ui.shell.current_nav()
        nav.push(widgets.licenses_page(nav, self.ui.spec.name, entries))


class BlockedView:
    def __init__(self, ui) -> None:
        self.ui = ui
        self.body = common.vbox(0)
        self.body.add_css_class("xx-page-body")
        self.body.set_margin_bottom(24)
        root = common.vbox(0)
        root.add_css_class("xx-mu")
        root.append(common.header(ui.shell.pop, "Killed artists",
                                  "Blocked everywhere"))
        root.append(common.scroller(self.body))
        self.widget = root
        self.paint()

    def paint(self) -> None:
        common.clear(self.body)
        try:
            entries = list(self.ui.blocked.list())
        except Exception:  # noqa: BLE001 - settings never fail
            entries = []
        if not entries:
            return
        for index, entry in enumerate(entries):
            # The name only: an engine id ("ar-north") means nothing here.
            title = entry.name or entry.id or "Artist"
            row = common.hbox(12)
            texts = common.vbox(2)
            texts.set_hexpand(True)
            texts.set_valign(Gtk.Align.CENTER)
            texts.append(common.label(title, "xx-title", chars=20))
            row.append(texts)
            unblock = minimal.outline(
                "Unblock", lambda e=entry: self.unblock(e))
            common.fit(unblock, 10)
            unblock.set_valign(Gtk.Align.CENTER)
            common.name(unblock, f"Unblock {title}")
            row.append(unblock)
            wrap = common.vbox(0)
            wrap.add_css_class("xx-mu-row")
            if index == len(entries) - 1:
                wrap.add_css_class("last")
            wrap.append(row)
            self.body.append(wrap)

    def unblock(self, entry) -> None:
        self.ui.unblock_artist(entry.id, entry.name,
                               lambda _e: self.paint())


class LogView:
    def __init__(self, ui) -> None:
        self.ui = ui
        self.text = ""
        body = common.vbox(0)
        body.add_css_class("xx-page-body")
        body.set_margin_top(12)
        body.set_margin_bottom(24)
        self.label = Gtk.Label(label="Reading the log…")
        self.label.add_css_class("xx-mu-log")
        self.label.set_xalign(0.0)
        self.label.set_yalign(0.0)
        self.label.set_wrap(True)
        self.label.set_wrap_mode(2)  # Pango WORD_CHAR
        self.label.set_max_width_chars(40)
        self.label.set_selectable(True)
        body.append(self.label)
        root = common.vbox(0)
        root.add_css_class("xx-mu")
        self.head = common.header(ui.shell.pop, "Log", LOG_SUB,
                                  "edit-copy-symbolic", "Copy the log",
                                  self.copy)
        root.append(self.head)
        root.append(common.scroller(body))
        self.widget = root
        ui.io(self.read, self.show, lambda _e: self.show(""))

    def read(self) -> str:
        path = self.ui.paths.log_file()
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()[-LOG_LINES:]
        except OSError:
            return ""
        return diagnostics.redact_text("".join(lines)).rstrip()

    def show(self, text: str) -> None:
        self.text = text
        self.label.set_text(text)  # an empty log shows nothing

    def copy(self) -> None:
        if self.text:
            self.widget.get_clipboard().set(self.text)
            common.copied(self.head, LOG_SUB)
