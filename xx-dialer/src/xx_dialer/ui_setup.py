"""Setup page: first-run state with actually probed values.

Every row shows live state, probed on a worker thread and posted back
with ``GLib.idle_add`` — never a ``call_sync`` on the GTK loop:
xx-contacts reachable/activatable, the policy service activatable, a
Voice-capable modem under ModemManager, the shell consulting us
(screen rows per ``docs/shell-consult.md``), and a notification
server with a test-ring button. Finishing seeds the observe week
(put-if-absent, never restarted); once the week ends the page offers
enforcement instead of flipping it.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from xx_ui import widgets  # noqa: E402
from xx_dialer import config as app_config  # noqa: E402
from xx_dialer import rules, setup as setup_probes  # noqa: E402
from xx_dialer.store import Store  # noqa: E402


def _outline(text: str, callback=None):
    """An outline button whose label wraps between words (large text,
    320 px) instead of ellipsizing; it never widens the window."""
    return widgets.wrap_to_fit(widgets.outline_button(text, callback))


def _primary(text: str, callback=None):
    return widgets.wrap_to_fit(widgets.primary_button(text, callback))


GLYPH_OK = "object-select-symbolic"
GLYPH_WARN = "dialog-warning-symbolic"


@dataclass
class SetupDeps:
    db_file: Path
    home: Path | None = None
    notice: object = None
    fetch_names: object = None
    fetch_modems: object = None
    test_ring: object = None
    navigate: object = None


@dataclass
class SetupSnapshot:
    contacts: setup_probes.Probe = field(
        default_factory=lambda: setup_probes.Probe(False, "Checking..."))
    policy: setup_probes.Probe = field(
        default_factory=lambda: setup_probes.Probe(False, "Checking..."))
    modem: setup_probes.Probe = field(
        default_factory=lambda: setup_probes.Probe(False, "Checking..."))
    consult: setup_probes.Probe = field(
        default_factory=lambda: setup_probes.Probe(False, "Checking..."))
    notify: setup_probes.Probe = field(
        default_factory=lambda: setup_probes.Probe(False, "Checking..."))
    enforcing: bool = False
    offer: bool = False
    watch_until: str = ""


class SetupController:
    """Setup state; probes run through injectable fetchers."""

    def __init__(self, deps: SetupDeps) -> None:
        self._deps = deps
        self.snapshot = SetupSnapshot()

    def _notice(self, text: str) -> None:
        if self._deps.notice is not None:
            try:
                self._deps.notice(text)
            except Exception:
                pass

    def probe(self) -> SetupSnapshot:
        """Fetch live state synchronously. Worker thread only."""
        fetch_names = self._deps.fetch_names \
            or setup_probes.real_session_names
        fetch_modems = self._deps.fetch_modems \
            or setup_probes.real_modem_objects
        try:
            names = fetch_names()
        except Exception:
            names = ([], [])
        try:
            objects = fetch_modems()
        except Exception:
            objects = None
        config = app_config.load(self._deps.home)
        with Store(self._deps.db_file) as store:
            consult = setup_probes.probe_consult(store)
        enforcing = config.get("enforcement_mode") == "enforcing"
        now_ms = int(time.time() * 1000)
        offer = rules.offer_enforcement(config, now_ms)
        watch_until = ""
        if not enforcing:
            raw = config.get("observe_week_end") or 0
            try:
                if float(raw) > 0:
                    watch_until = datetime.fromtimestamp(
                        float(raw) / 1000).strftime("%a, %b %d")
            except (TypeError, ValueError, OverflowError, OSError):
                watch_until = ""
        self.snapshot = SetupSnapshot(
            contacts=setup_probes.probe_contacts(names),
            policy=setup_probes.probe_policy(names),
            modem=setup_probes.probe_modem(objects),
            consult=consult,
            notify=setup_probes.probe_notifications(names),
            enforcing=enforcing, offer=offer, watch_until=watch_until)
        return self.snapshot

    def finish(self) -> SetupSnapshot:
        """Seed the observe week put-if-absent, then re-probe."""
        config = app_config.load(self._deps.home)
        rules.seed_observe_week(config, int(time.time() * 1000))
        app_config.save(config, self._deps.home)
        return self.probe()

    def start_screening(self) -> SetupSnapshot:
        config = app_config.load(self._deps.home)
        rules.set_enforcement(config, True)
        app_config.save(config, self._deps.home)
        return self.probe()

    def send_test_ring(self, on_done) -> None:
        sender = self._deps.test_ring or setup_probes.send_test_ring
        try:
            sender(on_done)
        except Exception as err:
            on_done(False, str(err))


class SetupView:
    """The Setup page widget."""

    ROWS = (("contacts", "Contacts"),
            ("policy", "Screening service"),
            ("modem", "Modem"),
            ("consult", "Shell consult"),
            ("notify", "Notifications"))

    def __init__(self, controller: SetupController) -> None:
        self.controller = controller
        navigate = getattr(controller._deps, "navigate", None)
        self._navigate = navigate if callable(navigate) else None
        self._root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             spacing=8)
        self._build()
        self.refresh()
        self.recheck()

    @property
    def widget(self) -> Gtk.Widget:
        return self._page

    def _go_recents(self) -> None:
        if self._navigate is not None:
            self._navigate("recents")

    def _build(self) -> None:
        if self._navigate is not None:
            header = widgets.compact_header(self._go_recents, "Setup")
        else:
            header = widgets.page_header("Setup")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                       spacing=8)
        body.add_css_class("xx-page-body")
        intro = widgets.wrap_label(
            "Finish starts a week of watching. Screening stays off "
            "until you turn it on.")
        body.append(intro)
        self._rows: dict[str, tuple[Gtk.Image, Gtk.Label]] = {}
        for _key, title in self.ROWS:
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=12)
            box.set_valign(Gtk.Align.CENTER)
            glyph = widgets.icon_image("dialog-warning-symbolic")
            glyph.set_valign(Gtk.Align.CENTER)
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                           spacing=2)
            text.set_hexpand(True)
            text.set_valign(Gtk.Align.CENTER)
            head = widgets.wrap_label(title, "xx-title")
            detail = widgets.wrap_label("Checking...")
            text.append(head)
            text.append(detail)
            box.append(glyph)
            box.append(text)
            body.append(box)
            self._rows[_key] = (glyph, detail)
        brow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._recheck_button = _outline("Recheck")
        self._recheck_button.set_hexpand(True)
        self._recheck_button.connect("clicked", self._on_recheck)
        self._ring_button = _outline("Test ring")
        self._ring_button.set_hexpand(True)
        self._ring_button.connect("clicked", self._on_test_ring)
        brow.append(self._recheck_button)
        brow.append(self._ring_button)
        body.append(brow)
        self._observe_label = widgets.wrap_label("")
        body.append(self._observe_label)
        frow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._finish_button = _outline(
            "Finish: watch for a week")
        self._finish_button.set_hexpand(True)
        self._finish_button.connect("clicked", self._on_finish)
        self._enforce_button = _primary(
            "Start screening now")
        self._enforce_button.set_hexpand(True)
        self._enforce_button.connect("clicked", self._on_enforce)
        frow.append(self._finish_button)
        frow.append(self._enforce_button)
        body.append(frow)
        self._page = widgets.page(header, body)

    # ---- refresh ----------------------------------------------------------

    def refresh(self) -> None:
        self._render(self.controller.snapshot)

    def _render(self, snapshot: SetupSnapshot) -> None:
        for key, probe in ((key, getattr(snapshot, key))
                           for key, _title in self.ROWS):
            glyph, detail = self._rows[key]
            glyph.set_from_icon_name(
                GLYPH_OK if probe.ok else GLYPH_WARN)
            detail.set_text(probe.detail)
        if snapshot.enforcing:
            self._observe_label.set_text("Screening is on.")
        elif snapshot.watch_until:
            self._observe_label.set_text("Watching until %s."
                                         % snapshot.watch_until)
        else:
            self._observe_label.set_text("The observe week is not "
                                         "started yet.")
        self._enforce_button.set_visible(snapshot.offer
                                         and not snapshot.enforcing)

    def recheck(self) -> None:
        def _work() -> None:
            try:
                snapshot = self.controller.probe()
            except Exception:
                return
            GLib.idle_add(self._render, snapshot)

        threading.Thread(target=_work, daemon=True).start()

    def _on_recheck(self, _button: Gtk.Button | None = None) -> None:
        self.recheck()

    def _on_finish(self, _button: Gtk.Button | None = None) -> None:
        def _work() -> None:
            try:
                snapshot = self.controller.finish()
            except Exception:
                return
            GLib.idle_add(self._render, snapshot)

        threading.Thread(target=_work, daemon=True).start()

    def _on_enforce(self, _button: Gtk.Button | None = None) -> None:
        dialog = Adw.AlertDialog.new(
            "Start screening?",
            "Unknown calls outside their window stay silent and "
            "blocked callers are hung up. Starred and saved contacts "
            "always ring.")
        dialog.add_response("cancel", "Keep watching")
        dialog.add_response("enforce", "Start screening")
        dialog.set_response_appearance(
            "enforce", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")

        def _done(source, response_id: str) -> None:
            if response_id != "enforce":
                return

            def _work() -> None:
                try:
                    snapshot = self.controller.start_screening()
                except Exception:
                    return
                GLib.idle_add(self._render, snapshot)

            threading.Thread(target=_work, daemon=True).start()

        dialog.connect("response", _done)
        parent = self._root.get_root()
        if isinstance(parent, Gtk.Window):
            dialog.present(parent)

    def _on_test_ring(self, _button: Gtk.Button | None = None) -> None:
        self._ring_button.set_sensitive(False)

        def _done(ok: bool, detail: str) -> None:
            def _show() -> bool:
                try:
                    self._ring_button.set_sensitive(True)
                except Exception:
                    return False
                glyph, text = self._rows["notify"]
                if ok:
                    text.set_text("Test ring sent.")
                    glyph.set_from_icon_name(GLYPH_OK)
                else:
                    text.set_text("Test ring failed: %s" % detail)
                    glyph.set_from_icon_name(GLYPH_WARN)
                return False

            GLib.idle_add(_show)

        try:
            self.controller.send_test_ring(_done)
        except Exception as err:
            _done(False, str(err))


def build_setup(deps: SetupDeps) -> SetupView:
    return SetupView(SetupController(deps))
