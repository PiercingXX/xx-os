"""Settings: the app's own sections (account, playback engine), then
Playback, Downloads and storage, Diagnostics and About.

Every value lives in ``config.json`` through the window's store (on the
worker). The diagnostics block is redacted before it reaches the
clipboard; the log view shows the redacted tail of the log file.
"""

from __future__ import annotations

from gi.repository import Gtk

from xx_music import __version__ as core_version
from xx_music import diagnostics
from xx_music.cache import VALID_CAP_CHOICES_GB
from xx_music.engine import STREAM
from xx_music.ui import common, css, fmt
from xx_ui import widgets

LOG_LINES = 200


def switch_row(title: str, subtitle: str, active: bool, on_change,
               last: bool = False) -> Gtk.Widget:
    switch = widgets.make_switch(active, on_change)
    switch.update_property([Gtk.AccessibleProperty.LABEL], [title])
    row = widgets.setting_row(title, subtitle, switch, last=last)
    _wrap_titles(row)
    return row


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
            trailing: Gtk.Widget | None = None) -> Gtk.Button:
    row = widgets.list_row(title, subtitle,
                           trailing=trailing or widgets.chevron(),
                           on_activate=callback, last=last)
    middle = row.get_child().get_first_child()
    child = middle.get_first_child() if middle else None
    while child is not None:
        if isinstance(child, Gtk.Label) and child.has_css_class(
                "xx-secondary"):
            child.set_wrap(True)
            child.set_lines(2)
            child.set_max_width_chars(26)
        child = child.get_next_sibling()
    return row


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
        self.body.append(self.playback_box)
        self.body.append(self.downloads_box)
        self.body.append(self._diagnostics())
        self.body.append(self._about())
        for box in (self.body,):
            box.add_css_class("xx-page-body")
        ui.io(ui.store.prefs, self._got_prefs, lambda _e: None)
        ui.on_downloads(self._paint_downloads)

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

    def _set(self, key: str, value) -> None:
        self.prefs[key] = value
        self.ui.io(lambda: self.ui.store.set_pref(key, value),
                   lambda p: self.prefs.update(p))

    def _paint_playback(self) -> None:
        box = self.playback_box
        common.clear(box)
        if not self.ui.supports(STREAM):
            return
        box.append(section("Playback"))
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
        box.append(meter)
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
        box.append(nav_row("Copy diagnostics",
                           "Versions and recent errors, no sign-in data",
                           self.copy_diagnostics,
                           trailing=widgets.icon_image("edit-copy-symbolic")))
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
            common.toast(self.ui, "Diagnostics copied")

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
                           lambda: self.open_licenses(entries), last=True))
        return box

    def open_licenses(self, entries: list) -> None:
        nav = self.ui.shell.current_nav()
        nav.push(widgets.licenses_page(nav, self.ui.spec.name, entries))


class LogView:
    def __init__(self, ui) -> None:
        self.ui = ui
        self.text = ""
        body = common.vbox(0)
        body.add_css_class("xx-page-body")
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
        root.append(common.header(ui.shell.pop, "Log",
                                  "Sign-in data hidden",
                                  "edit-copy-symbolic", "Copy the log",
                                  self.copy))
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
        self.label.set_text(text or "The log is empty.")

    def copy(self) -> None:
        if self.text:
            self.widget.get_clipboard().set(self.text)
            common.toast(self.ui, "Log copied")
