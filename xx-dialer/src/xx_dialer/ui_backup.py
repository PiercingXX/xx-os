"""Backup page: export/import ring_policy.json + calllog.json.

Same shapes the Android app exports, so a file restores on either
side; stars never appear here (contacts owns them). Imported policy
always lands in observe mode (importers.apply_policy). Files move
through the portal chooser (Gtk.FileDialog), defaulting to
~/Documents; nothing is read or written without the user picking it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import GLib, Gtk  # noqa: E402

from xx_ui import widgets  # noqa: E402
from xx_dialer import (  # noqa: E402
    applog,
    codecs,
    config as app_config,
    importers,
)
from xx_dialer.store import Store  # noqa: E402


def _outline(text: str, callback=None):
    """An outline button whose label wraps between words (large text,
    320 px) instead of ellipsizing; it never widens the window."""
    return widgets.wrap_to_fit(widgets.outline_button(text, callback))


POLICY_FILENAME = "ring_policy.json"
CALLLOG_FILENAME = "calllog.json"


@dataclass
class BackupDeps:
    db_file: Path
    home: Path | None = None
    notice: object = None
    navigate: object = None


class BackupController:
    """Store-backed backup state; the view renders status strings."""

    def __init__(self, deps: BackupDeps) -> None:
        self._deps = deps

    def _notice(self, text: str) -> None:
        if self._deps.notice is not None:
            try:
                self._deps.notice(text)
            except Exception:
                pass

    def counts(self) -> tuple[int, int, int]:
        """(patterns, tiers, calls) for the status line."""
        with Store(self._deps.db_file) as store:
            return (len(store.all_patterns()), len(store.all_tiers()),
                    store.call_count())

    def export_policy_text(self) -> str:
        config = app_config.load(self._deps.home)
        with Store(self._deps.db_file) as store:
            snapshot = importers.export_policy(config, store)
        return codecs.encode_policy(snapshot)

    def export_calllog_text(self) -> str:
        with Store(self._deps.db_file) as store:
            return importers.export_calllog(store)

    def import_policy_text(self, text: str) -> str:
        config = app_config.load(self._deps.home)
        try:
            with Store(self._deps.db_file) as store:
                report = importers.import_policy_text(text, config, store)
        except ValueError as err:
            applog.write("backup", "policy import rejected: %s" % err,
                         self._deps.home)
            return "That file is not a valid policy backup (%s)." % err
        if report is None:
            applog.write("backup", "policy import undecodable",
                         self._deps.home)
            return "Could not read that file as a policy backup."
        app_config.save(config, self._deps.home)
        status = ("Imported %d patterns, %d tiers; "
                  "screening stays in observe mode." % (
                      report.patterns_added, report.tiers_added))
        applog.write("backup", "policy import: %s" % status,
                     self._deps.home)
        self._notice(status)
        return status

    def import_calllog_text(self, text: str) -> str:
        try:
            entries = codecs.decode_calllog(text)
        except Exception:
            entries = []
        if not entries:
            try:
                env = json.loads((text or "").strip())
                if not isinstance(env, dict) or not isinstance(
                        env.get("entries"), list):
                    return "Could not read that file as a call log backup."
            except ValueError:
                return "Could not read that file as a call log backup."
        try:
            with Store(self._deps.db_file) as store:
                report = importers.import_calllog(entries, store)
        except Exception:
            return "Could not read that file as a call log backup."
        status = "Imported %d calls (%d already here)." % (
            report.inserted, report.duplicates)
        applog.write("backup", "calllog import: %s" % status,
                     self._deps.home)
        self._notice(status)
        return status


class BackupView:
    """Export/import buttons over Gtk.FileDialog plus status labels."""

    def __init__(self, controller: BackupController) -> None:
        self.controller = controller
        navigate = getattr(controller._deps, "navigate", None)
        self._navigate = navigate if callable(navigate) else None
        self._root: Gtk.Widget | None = None
        self._build()
        self.refresh()

    @property
    def widget(self) -> Gtk.Widget:
        assert self._root is not None
        return self._root

    def _go_recents(self) -> None:
        if self._navigate is not None:
            self._navigate("recents")

    def _status(self, text: str = "") -> Gtk.Label:
        return widgets.wrap_label(text)

    def _build(self) -> None:
        if self._navigate is not None:
            header = widgets.compact_header(
                self._go_recents, "Import & export")
        else:
            header = widgets.page_header("Import & export")
        self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             spacing=8)
        self._body.add_css_class("xx-page-body")

        self._body.append(widgets.section_label("Export"))
        self._export_status = self._status()
        self._body.append(self._export_status)
        self._export_policy = _outline("Export ring policy")
        self._export_policy.connect("clicked", self._on_export_policy)
        self._body.append(self._export_policy)
        self._export_calllog = _outline("Export call log")
        self._export_calllog.connect("clicked", self._on_export_calllog)
        self._body.append(self._export_calllog)

        self._body.append(widgets.section_label("Import"))
        note = self._status(
            "Imported policy always starts in observe mode.")
        self._body.append(note)
        self._import_status = self._status()
        self._body.append(self._import_status)
        self._import_policy = _outline("Import ring policy")
        self._import_policy.connect("clicked", self._on_import_policy)
        self._body.append(self._import_policy)
        self._import_calllog = _outline("Import call log")
        self._import_calllog.connect("clicked", self._on_import_calllog)
        self._body.append(self._import_calllog)

        self._root = widgets.page(header, self._body)

    def refresh(self) -> None:
        try:
            patterns, tiers, calls = self.controller.counts()
        except Exception:
            return
        def count(n: int, word: str) -> str:
            return f"{n} {word}{'' if n == 1 else 's'}"

        self._export_status.set_text(
            f"{count(patterns, 'pattern')}, {count(tiers, 'tier')}, "
            f"{count(calls, 'call')} on this phone.")

    # ---- portal ---------------------------------------------------------

    def _save_text(self, filename: str, text: str) -> None:
        picker = Gtk.FileDialog()
        picker.set_title("Save %s" % filename)
        picker.set_initial_name(filename)

        def _done(source, result) -> None:
            try:
                picked = source.save_finish(result)
            except GLib.Error:
                return
            if picked is None or picked.get_path() is None:
                return
            try:
                Path(picked.get_path()).write_text(text, encoding="utf-8")
            except OSError:
                self.controller._notice("Could not write that file.")
                return
            applog.write("backup", "exported %s" % filename,
                         self.controller._deps.home)
            self.controller._notice("Saved %s." % filename)

        parent = self._root.get_root() if self._root is not None else None
        picker.save(parent if isinstance(parent, Gtk.Window) else None,
                    None, _done)

    def _open_text(self, filename: str, importer) -> None:
        picker = Gtk.FileDialog()
        picker.set_title("Open %s" % filename)

        def _done(source, result) -> None:
            try:
                picked = source.open_finish(result)
            except GLib.Error:
                return
            if picked is None or picked.get_path() is None:
                return
            try:
                text = Path(picked.get_path()).read_text(encoding="utf-8")
            except OSError:
                self.controller._notice("Could not read that file.")
                return
            self._import_status.set_text(importer(text))
            self.refresh()

        parent = self._root.get_root() if self._root is not None else None
        picker.open(parent if isinstance(parent, Gtk.Window) else None,
                    None, _done)

    def _on_export_policy(self, _button: Gtk.Button) -> None:
        try:
            self._save_text(POLICY_FILENAME,
                            self.controller.export_policy_text())
        except Exception:
            self.controller._notice("Could not build the export.")

    def _on_export_calllog(self, _button: Gtk.Button) -> None:
        try:
            self._save_text(CALLLOG_FILENAME,
                            self.controller.export_calllog_text())
        except Exception:
            self.controller._notice("Could not build the export.")

    def _on_import_policy(self, _button: Gtk.Button) -> None:
        self._open_text(POLICY_FILENAME,
                        self.controller.import_policy_text)

    def _on_import_calllog(self, _button: Gtk.Button) -> None:
        self._open_text(CALLLOG_FILENAME,
                        self.controller.import_calllog_text)


def build_backup(deps: BackupDeps) -> BackupView:
    return BackupView(BackupController(deps))
