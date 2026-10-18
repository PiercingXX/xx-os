"""Rules page: the screening control room.

Renders the pure shapes from ``rules.py`` over the warm SQLite store
and ``config.json``. Every config write goes through ``config.save``
(which the policy service picks up on its next ``CheckNumber`` — it
reloads config per call, so no restart or watch is needed); pattern
and blocklist writes go straight to the store tables the service
reads per call. Test-a-number runs the REAL engine through
``PolicyService.check_number``, the same method the D-Bus handler
serves the shell with.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from xx_ui import widgets  # noqa: E402
import xx_phone  # noqa: E402
import xx_dialer  # noqa: E402
from xx_dialer import config as app_config  # noqa: E402
from xx_dialer import importers, masks, policy, rules  # noqa: E402
from xx_dialer.service import PolicyService  # noqa: E402
from xx_dialer.store import BLOCKED_GROUP, PatternRow, Store  # noqa: E402


def _outline(text: str, callback=None):
    """An outline button whose label wraps between words (large text,
    320 px) instead of ellipsizing; it never widens the window."""
    return widgets.wrap_to_fit(widgets.outline_button(text, callback))


HIDDEN_OPTIONS = ("Unknown", "Silence", "Block")
HIDDEN_VALUES = ("unknown", "silence", "block")

NOTIF_OPTIONS = ("At once", "Daily", "Never")
NOTIF_VALUES = ("immediate", "daily", "never")

BYPASS_OPTIONS = (("30 min", 30), ("2 h", 120), ("8 h", 480))

TONE_KEYS = (("unknown_tone", "Unknown callers"),
             ("default_tone", "Default"))


@dataclass
class RulesDeps:
    db_file: Path
    home: Path | None = None
    notice: object = None
    service_factory: object = None
    contacts: object = None


@dataclass
class RulesSnapshot:
    enforcing: bool = False
    offer: bool = False
    bypass_until_ms: int | None = None
    bypass_minutes: int = 120
    unknown: policy.Window = None  # type: ignore[assignment]
    business: policy.Window = None  # type: ignore[assignment]
    hidden_policy: str = "unknown"
    repeat: bool = True
    notif_policy: str = "immediate"
    unknown_tone: str = ""
    default_tone: str = ""
    patterns: list = field(default_factory=list)
    blocked: list = field(default_factory=list)
    tallies: dict = field(default_factory=dict)
    log: list = field(default_factory=list)


class RulesController:
    """Store-backed Rules state; the view renders snapshots."""

    def __init__(self, deps: RulesDeps) -> None:
        self._deps = deps
        self._service = None

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _config(self) -> dict:
        return app_config.load(self._deps.home)

    def _save(self, config: dict) -> RulesSnapshot:
        app_config.save(config, self._deps.home)
        return self.load()

    def _notice(self, text: str) -> None:
        if self._deps.notice is not None:
            try:
                self._deps.notice(text)
            except Exception:
                pass

    def _service_or_make(self) -> PolicyService:
        if self._service is None:
            factory = self._deps.service_factory
            if factory is not None:
                self._service = factory(self._deps.db_file, self._deps.home)
            else:
                self._service = PolicyService(db_file=self._deps.db_file,
                                              home=self._deps.home)
        return self._service

    def load(self) -> RulesSnapshot:
        from xx_dialer.store import load_rules

        config = self._config()
        with Store(self._deps.db_file) as store:
            engine_rules = load_rules(store, config)
            patterns = store.all_patterns()
            blocked = store.all_blocked()
            tallies = rules.screen_tallies(store, rules.week_start_ms(
                self._now_ms()))
            log = store.last_screened(rules.LOG_LIMIT)
        return RulesSnapshot(
            enforcing=config.get("enforcement_mode") == "enforcing",
            offer=rules.offer_enforcement(config, self._now_ms()),
            bypass_until_ms=rules.bypass_until_ms(config),
            bypass_minutes=int(config.get("bypass_duration_minutes") or 120),
            unknown=engine_rules.unknown_window,
            business=engine_rules.business_window,
            hidden_policy=engine_rules.hidden_caller_policy.value,
            repeat=engine_rules.repeat_caller_enabled,
            notif_policy=config.get("silenced_notif_policy") or "immediate",
            unknown_tone=config.get("unknown_tone") or "",
            default_tone=config.get("default_tone") or "",
            patterns=patterns, blocked=blocked, tallies=tallies, log=log)

    def set_enforcing(self, enforcing: bool) -> RulesSnapshot:
        config = self._config()
        rules.set_enforcement(config, enforcing)
        return self._save(config)

    def arm_bypass(self, minutes: int | None = None) -> RulesSnapshot:
        config = self._config()
        if minutes is None:
            minutes = int(config.get("bypass_duration_minutes") or 120)
        rules.set_bypass(config, int(minutes), self._now_ms())
        return self._save(config)

    def stop_bypass(self) -> RulesSnapshot:
        config = self._config()
        rules.clear_bypass(config)
        return self._save(config)

    def set_bypass_minutes(self, minutes: int) -> RulesSnapshot:
        config = self._config()
        config["bypass_duration_minutes"] = int(minutes)
        return self._save(config)

    def save_window(self, key: str, start: int, end: int,
                    days: int) -> RulesSnapshot:
        config = self._config()
        try:
            rules.set_window(config, key, int(start), int(end), int(days))
        except (TypeError, ValueError) as err:
            raise ValueError("invalid window: %s" % (err,)) from err
        return self._save(config)

    def set_hidden_policy(self, value: str) -> RulesSnapshot:
        if value not in HIDDEN_VALUES:
            raise ValueError("unknown hidden-caller policy: %r" % (value,))
        config = self._config()
        config["hidden_caller_policy"] = value
        return self._save(config)

    def set_repeat(self, enabled: bool) -> RulesSnapshot:
        config = self._config()
        config["repeat_caller_enabled"] = bool(enabled)
        return self._save(config)

    def set_notif_policy(self, value: str) -> RulesSnapshot:
        if value not in NOTIF_VALUES:
            raise ValueError("unknown silenced policy: %r" % (value,))
        config = self._config()
        config["silenced_notif_policy"] = value
        return self._save(config)

    def set_tone(self, key: str, path: str) -> RulesSnapshot:
        config = self._config()
        config[key] = path
        return self._save(config)

    def add_pattern(self, prefix: str, wildcards: int,
                    action: str) -> RulesSnapshot:
        clean = "".join(ch for ch in prefix if ch.isdigit())
        try:
            policy.PatternRule(clean, int(wildcards))
        except ValueError as err:
            raise ValueError("invalid pattern: %s" % (err,)) from err
        if action not in ("block", "silence"):
            raise ValueError("action must be block or silence")
        with Store(self._deps.db_file) as store:
            store.add_pattern(PatternRow(clean, int(wildcards), action,
                                         created_at=self._now_ms()))
        return self.load()

    def delete_pattern(self, row_id: int) -> RulesSnapshot:
        with Store(self._deps.db_file) as store:
            store.delete_pattern(int(row_id))
        return self.load()

    def set_neighbor_preset(self, line_number: str | None) -> RulesSnapshot:
        """Arm the neighbor-spoof silence rule, or disarm when armed."""
        with Store(self._deps.db_file) as store:
            armed = [row for row in store.all_patterns()
                     if row.preset == rules.NEIGHBOR_PRESET]
            if armed:
                for row in armed:
                    store.delete_pattern(row.row_id)
                return self.load()
            region = policy.region_of(self._config())
            digits = masks.digits_of(
                policy.canonical_number(line_number or "", region) or "")
            draft = masks.neighbor_draft(digits) if digits else None
            if draft is None:
                raise ValueError("need your own number to derive the prefix")
            store.add_pattern(PatternRow(
                draft.prefix, draft.wildcards, "silence",
                preset=rules.NEIGHBOR_PRESET, created_at=self._now_ms()))
            return self.load()

    def import_blocklist_text(self, text: str) -> RulesSnapshot:
        with Store(self._deps.db_file) as store:
            parsed = importers.import_blocklist_text(text, store)
        self._notice("Blocklist import: %d added, %d skipped."
                     % (len(parsed.numbers), parsed.skipped))
        return self.load()

    def unblock(self, number: str) -> RulesSnapshot:
        """Unblock a number locally and on the contact behind it.

        Removing only the ``blocked_number`` row leaves the Blocked
        group membership behind, so the engine still rejects the caller
        and the next contacts sync restores the row: clear the tier
        too, and push the removal upstream as an UpdateTags delta.
        """
        region = policy.region_of(self._config())
        canonical = policy.canonical_number(number, region) \
            or (number or "").strip()
        uid = None
        if canonical:
            with Store(self._deps.db_file) as store:
                row = store.find_mirror(canonical)
                uid = row.lookup_key if row is not None else None
        client = self._deps.contacts
        if uid is not None and client is not None:
            try:
                client.set_blocked(uid, False)
                return self.load()
            except Exception:
                pass
        with Store(self._deps.db_file) as store:
            if uid is None:
                if canonical:
                    store.remove_blocked(canonical)
            else:
                store.remove_tier(uid, BLOCKED_GROUP)
                for known in store.mirror_numbers_for_key(uid):
                    if not self._blocked_elsewhere(store, uid, known):
                        store.remove_blocked(known)
        return self.load()

    @staticmethod
    def _blocked_elsewhere(store: Store, uid: str, number: str) -> bool:
        for key, tier, _ in store.all_tiers():
            if tier == BLOCKED_GROUP and key != uid \
                    and number in store.mirror_numbers_for_key(key):
                return True
        return False

    def clear_log(self) -> RulesSnapshot:
        with Store(self._deps.db_file) as store:
            store.clear_screen_log()
        return self.load()

    def test_number(self, raw: str, now=None) -> tuple[str, str]:
        if not (raw or "").strip():
            raise ValueError("type a number to test")
        return rules.test_number(self._service_or_make(), raw, now)


class RulesView:
    """The Rules page widget."""

    def __init__(self, controller: RulesController) -> None:
        self.controller = controller
        self._suppress = False
        self._snapshot = controller.load()
        self._root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             spacing=0)
        self._build()
        self.refresh()
        GLib.timeout_add(15_000, self._on_tick)

    @property
    def widget(self) -> Gtk.Widget:
        return self._page

    def _on_tick(self) -> bool:
        try:
            self._render_bypass()
        except Exception:
            return False
        return True

    # ---- construction ---------------------------------------------------

    def _section(self, title: str) -> Gtk.Box:
        # No gutter of its own: the page body already carries it.
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(widgets.section_label(title))
        self._body.append(box)
        return box

    def _build(self) -> None:
        self._body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             spacing=4)
        # The mode switch sits beside the title and drops under it when
        # the line is too narrow (320 px, large text) rather than
        # squeezing "Rules" to an ellipsis.
        wrap_box = getattr(Adw, "WrapBox", None)
        if wrap_box is not None:
            head = wrap_box(child_spacing=8, line_spacing=4)
            head.set_justify(Adw.JustifyMode.SPREAD)
            head.set_justify_last_line(True)
        else:
            head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=8)
        head.set_valign(Gtk.Align.CENTER)
        title = widgets.label("Rules", "xx-display")
        title.set_ellipsize(Pango.EllipsizeMode.NONE)
        title.set_hexpand(True)
        title.set_valign(Gtk.Align.CENTER)
        head.append(title)
        self._mode_seg = widgets.segmented([
            ("Enforce", False, self._on_enforce_pick),
            ("Observe", True, self._on_observe_pick),
        ])
        self._mode_seg.set_valign(Gtk.Align.CENTER)
        head.append(self._mode_seg)
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                         spacing=0)
        header.add_css_class("xx-page-body")
        header.append(head)
        self._mode_caption = widgets.caption("")
        self._mode_caption.set_ellipsize(Pango.EllipsizeMode.NONE)
        self._mode_caption.set_wrap(True)
        self._mode_caption.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        header.append(self._mode_caption)
        self._offer_button = _outline(
            "Start screening now", self._on_offer)
        header.append(self._offer_button)

        expect_card = widgets.card()
        expect_card.append(widgets.wrap_label(
            "Expecting a call", "xx-title"))
        expect_card.append(widgets.wrap_label(
            "Everyone rings for a while, unknown too"))
        self._dur_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._dur_row.set_halign(Gtk.Align.CENTER)
        self._dur_buttons: list[Gtk.Button] = []
        for label, minutes in BYPASS_OPTIONS:
            button = _outline((label))
            button.connect("clicked", self._on_expect, minutes)
            button.set_hexpand(True)
            self._dur_row.append(button)
            self._dur_buttons.append(button)
        expect_card.append(self._dur_row)
        active_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._expect_countdown = widgets.label("", "xx-secondary")
        self._expect_countdown.set_hexpand(True)
        active_row.append(self._expect_countdown)
        self._stop_button = _outline(
            "Stop expecting", self._on_stop_expect)
        active_row.append(self._stop_button)
        self._active_row = active_row
        expect_card.append(active_row)
        self._body.append(expect_card)

        who = self._section("Who gets through")
        self._preced_list = Gtk.ListBox()
        self._preced_list.set_selection_mode(Gtk.SelectionMode.NONE)
        who.append(self._preced_list)

        windows = self._section("Ring windows")
        self._win_unknown, self._win_unknown_value = self._window_button(
            "Unknown")
        self._win_unknown.connect("clicked", self._on_edit_window,
                                  "unknown_window")
        self._win_business, self._win_business_value = \
            self._window_button("Business")
        self._win_business.connect("clicked", self._on_edit_window,
                                   "business_window")
        windows.append(self._win_unknown)
        windows.append(self._win_business)

        pol = self._section("How to handle")
        pol.append(widgets.setting_row(
            "Repeat caller rings",
            "Second call within 15 min gets through",
            trailing=self._repeat_switch_new()))
        hidden = self._section("Hidden callers")
        self._hidden_seg = widgets.segmented([
            (label, False, self._on_hidden_seg(value))
            for label, value in zip(HIDDEN_OPTIONS, HIDDEN_VALUES)
        ])
        self._hidden_seg.add_css_class("xx-dialer-seg")
        hidden.append(self._hidden_seg)
        silenced = self._section("Silenced calls")
        self._notif_seg = widgets.segmented([
            (label, False, self._on_notif_seg(value))
            for label, value in zip(NOTIF_OPTIONS, NOTIF_VALUES)
        ])
        silenced.append(self._notif_seg)

        tone = self._section("Ringtones")
        self._tone_rows: dict[str, Gtk.Button] = {}
        for key, title in TONE_KEYS:
            brow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=8)
            brow.set_valign(Gtk.Align.CENTER)
            lab = Gtk.Label(label=title, xalign=0.0, hexpand=True)
            lab.add_css_class("xx-title")
            lab.set_ellipsize(Pango.EllipsizeMode.END)
            lab.set_valign(Gtk.Align.CENTER)
            pick = _outline("Choose")
            pick.connect("clicked", self._on_pick_tone, key)
            clear = _outline("Clear")
            clear.connect("clicked", self._on_clear_tone, key)
            self._tone_rows[key] = pick
            brow.append(lab)
            brow.append(pick)
            brow.append(clear)
            tone.append(brow)

        pat = self._section("Number patterns")
        self._pattern_list = Gtk.ListBox()
        self._pattern_list.set_selection_mode(Gtk.SelectionMode.NONE)
        pat.append(self._pattern_list)
        # Stacked full-width, like Import blocklist file below: side by
        # side they outgrow 320 px at large text.
        prow = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._add_pattern = widgets.wrap_to_fit(
            _outline("Add pattern"))
        self._add_pattern.set_hexpand(True)
        self._add_pattern.connect("clicked", self._on_add_pattern)
        self._neighbor_button = widgets.wrap_to_fit(
            _outline(""))
        self._neighbor_button.set_hexpand(True)
        self._neighbor_button.connect("clicked", self._on_neighbor)
        prow.append(self._add_pattern)
        prow.append(self._neighbor_button)
        pat.append(prow)
        self._import_button = _outline(
            "Import blocklist file")
        self._import_button.connect("clicked", self._on_import_blocklist)
        pat.append(self._import_button)

        blocked = self._section("Blocked numbers")
        self._blocked_list = Gtk.ListBox()
        self._blocked_list.set_selection_mode(Gtk.SelectionMode.NONE)
        blocked.append(self._blocked_list)

        test = self._section("Test a number")
        trow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._test_entry = widgets.text_entry("Number to test")
        self._test_entry.set_hexpand(True)
        self._test_entry.set_can_focus(False)
        click = Gtk.GestureClick.new()
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_test_entry_click)
        self._test_entry.add_controller(click)
        self._test_entry.connect("activate", self._on_test_go)
        self._test_go = _outline("Check")
        self._test_go.connect("clicked", self._on_test_go)
        trow.append(self._test_entry)
        trow.append(self._test_go)
        test.append(trow)
        self._test_verdict = widgets.wrap_label("", "xx-title")
        self._test_reason = widgets.wrap_label("")
        test.append(self._test_verdict)
        test.append(self._test_reason)

        week = self._section("This week")
        self._tally_label = widgets.wrap_label("")
        week.append(self._tally_label)

        log = self._section("Screening log")
        self._log_list = Gtk.ListBox()
        self._log_list.set_selection_mode(Gtk.SelectionMode.NONE)
        log.append(self._log_list)
        self._clear_log = _outline("Clear screening log")
        self._clear_log.connect("clicked", self._on_clear_log)
        log.append(self._clear_log)

        self._about_entries = widgets.standard_license_entries("Dialer")
        self._body.append(widgets.about_content(
            "Dialer", getattr(xx_dialer, "__version__", "?"),
            self._about_entries, self._on_licenses))

        self._page = widgets.page(header, self._body)

    def _on_licenses(self) -> None:
        parent = self._root.get_root()
        if isinstance(parent, Gtk.Window):
            widgets.show_licenses_dialog(parent, "Dialer",
                                         self._about_entries)

    @staticmethod
    def _window_button(title: str) -> tuple[Gtk.Button, Gtk.Label]:
        button = Gtk.Button()
        button.add_css_class("xx-btn")
        button.add_css_class("xx-btn-outline")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_hexpand(True)
        head = Gtk.Label(label=title, xalign=0.0)
        head.add_css_class("xx-title")
        head.set_ellipsize(Pango.EllipsizeMode.END)
        value = Gtk.Label(label="", xalign=0.0)
        value.add_css_class("xx-secondary")
        value.set_wrap(True)
        value.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        box.append(head)
        box.append(value)
        button.set_child(box)
        return button, value

    def _repeat_switch_new(self) -> Gtk.Switch:
        self._repeat_switch = widgets.make_switch()
        self._repeat_switch.connect("notify::active",
                                    self._on_repeat_flip)
        return self._repeat_switch

    def _on_hidden_seg(self, value: str):
        def _picked() -> None:
            if self._suppress:
                return
            try:
                self.controller.set_hidden_policy(value)
            except ValueError:
                pass
            self.refresh()

        return _picked

    def _on_notif_seg(self, value: str):
        def _picked() -> None:
            if self._suppress:
                return
            try:
                self.controller.set_notif_policy(value)
            except ValueError:
                pass
            self.refresh()

        return _picked

    # ---- refresh ----------------------------------------------------------

    @staticmethod
    def _select_seg(seg: Gtk.Box, selected: int) -> None:
        child = seg.get_first_child()
        index = 0
        while child is not None:
            if index == selected:
                child.add_css_class("seg-selected")
            else:
                child.remove_css_class("seg-selected")
            child = child.get_next_sibling()
            index += 1

    def refresh(self) -> None:
        self._snapshot = self.controller.load()
        snap = self._snapshot
        self._suppress = True
        try:
            self._select_seg(self._mode_seg, 0 if snap.enforcing else 1)
            self._select_seg(self._hidden_seg,
                             HIDDEN_VALUES.index(snap.hidden_policy))
            self._select_seg(self._notif_seg,
                             NOTIF_VALUES.index(snap.notif_policy))
            self._repeat_switch.set_active(snap.repeat)
        except ValueError:
            pass
        finally:
            self._suppress = False
        screened = snap.tallies.get("total", 0)
        self._mode_caption.set_text(
            "%s · %d SCREENED THIS WEEK"
            % ("ENFORCING" if snap.enforcing else "OBSERVING", screened))
        self._offer_button.set_visible(snap.offer and not snap.enforcing)
        self._render_bypass()
        self._render_precedence(snap)
        self._win_unknown_value.set_text(
            rules.window_line(snap.unknown))
        self._win_business_value.set_text(
            rules.window_line(snap.business))
        for key, _title in TONE_KEYS:
            current = snap.unknown_tone if key == "unknown_tone" \
                else snap.default_tone
            widgets.set_button_text(
                self._tone_rows[key],
                "Set" if not current else current.split("/")[-1])
        self._render_patterns(snap)
        self._render_blocked(snap)
        self._render_tallies(snap)
        self._render_log(snap)

    def _render_bypass(self) -> None:
        snap = self._snapshot
        now_ms = int(time.time() * 1000)
        active = rules.bypass_active_ms(snap.bypass_until_ms, now_ms)
        self._dur_row.set_visible(not active)
        self._active_row.set_visible(active)
        if active:
            self._expect_countdown.set_text(
                rules.countdown_label(snap.bypass_until_ms, now_ms))

    def _render_precedence(self, snap: RulesSnapshot) -> None:
        while True:
            row = self._preced_list.get_row_at_index(0)
            if row is None:
                break
            self._preced_list.remove(row)
        for entry in rules.precedence_rows(
                snap.unknown, snap.business,
                policy.StirAction.BLOCK,
                policy.HiddenCallerPolicy(snap.hidden_policy), snap.repeat):
            line = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                           spacing=0)
            line.add_css_class("xx-row")
            head = Gtk.Label(
                label="%d. %s" % (entry.n, entry.condition), xalign=0.0)
            head.add_css_class("xx-title")
            head.set_wrap(True)
            head.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            tail = Gtk.Label(
                label="%s%s" % (entry.verdict,
                                " (%s)" % entry.tone if entry.tone else ""),
                xalign=0.0)
            tail.add_css_class("xx-secondary")
            tail.set_wrap(True)
            tail.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            if entry.verdict == "Block":
                tail.add_css_class("xx-danger-text")
            line.append(head)
            line.append(tail)
            self._preced_list.append(line)

    def _render_patterns(self, snap: RulesSnapshot) -> None:
        while True:
            row = self._pattern_list.get_row_at_index(0)
            if row is None:
                break
            self._pattern_list.remove(row)
        armed = any(row.preset == rules.NEIGHBOR_PRESET
                    for row in snap.patterns)
        widgets.set_button_text(
            self._neighbor_button,
            "Neighbor-spoof: on" if armed else "Neighbor-spoof: off")
        self._pattern_list.set_visible(bool(snap.patterns))
        if not snap.patterns:
            return
        for prow in snap.patterns:
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=8)
            line.add_css_class("xx-row")
            line.set_valign(Gtk.Align.CENTER)
            draft = masks.Draft(prow.e164_prefix, prow.wildcards)
            text = Gtk.Label(label="%s — %s" % (
                masks.preview(draft),
                "blocks" if prow.action == "block" else "silences"),
                xalign=0.0, hexpand=True)
            text.add_css_class("xx-title")
            text.set_ellipsize(Pango.EllipsizeMode.END)
            text.set_valign(Gtk.Align.CENTER)
            delete = _outline("Delete")
            delete.connect("clicked", self._on_delete_pattern,
                           prow.row_id)
            line.append(text)
            line.append(delete)
            self._pattern_list.append(line)

    def _region(self) -> str:
        try:
            return policy.region_of(self.controller._config())
        except Exception:
            return "US"

    def _render_blocked(self, snap: RulesSnapshot) -> None:
        while True:
            row = self._blocked_list.get_row_at_index(0)
            if row is None:
                break
            self._blocked_list.remove(row)
        self._blocked_list.set_visible(bool(snap.blocked))
        if not snap.blocked:
            return
        region = self._region()
        for number in sorted(snap.blocked):
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=8)
            line.add_css_class("xx-row")
            line.set_valign(Gtk.Align.CENTER)
            text = Gtk.Label(
                label=xx_phone.display(
                    number, xx_phone.display_country(region)),
                xalign=0.0, hexpand=True)
            text.add_css_class("xx-title")
            text.set_ellipsize(Pango.EllipsizeMode.END)
            text.set_valign(Gtk.Align.CENTER)
            remove = _outline("Unblock")
            remove.connect("clicked", self._on_unblock, number)
            line.append(text)
            line.append(remove)
            self._blocked_list.append(line)

    def _render_tallies(self, snap: RulesSnapshot) -> None:
        by_verdict = snap.tallies.get("by_verdict", {})
        by_reason = snap.tallies.get("by_reason", {})
        head = "%d screened: %s rang, %s silenced, %s blocked." % (
            snap.tallies.get("total", 0),
            by_verdict.get("Ring", 0),
            by_verdict.get("Silence", 0),
            by_verdict.get("Block", 0))
        top = sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0]))[:4]
        if top:
            head += "\n%s." % ", ".join("%s (%d)" % (reason, count)
                                        for reason, count in top)
        self._tally_label.set_text(head)

    def _render_log(self, snap: RulesSnapshot) -> None:
        while True:
            row = self._log_list.get_row_at_index(0)
            if row is None:
                break
            self._log_list.remove(row)
        self._log_list.set_visible(bool(snap.log))
        if not snap.log:
            return
        region = self._region()
        for entry in snap.log:
            glyph = {"Block": "Blocked", "Silence": "Silenced",
                     "Ring": "Rang"}.get(entry.verdict, entry.verdict)
            line = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                           spacing=0)
            line.add_css_class("xx-row")
            who = xx_phone.display(
                entry.e164, xx_phone.display_country(region)) \
                if entry.e164 else "hidden"
            head = Gtk.Label(
                label="%s · %s" % (glyph, who),
                xalign=0.0)
            head.add_css_class("xx-title")
            head.set_ellipsize(Pango.EllipsizeMode.END)
            if entry.verdict == "Block":
                head.add_css_class("xx-danger-text")
            tail = Gtk.Label(
                label=entry.reason or entry.verdict, xalign=0.0)
            tail.add_css_class("xx-secondary")
            tail.set_wrap(True)
            tail.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            line.append(head)
            line.append(tail)
            self._log_list.append(line)

    # ---- mode + bypass ------------------------------------------------------

    def _on_enforce_pick(self) -> None:
        if self._suppress or self._snapshot.enforcing:
            return
        self._confirm_enforce()

    def _on_observe_pick(self) -> None:
        if self._suppress or not self._snapshot.enforcing:
            return
        self.controller.set_enforcing(False)
        self.refresh()

    def _confirm_enforce(self) -> None:
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
            if response_id == "enforce":
                self.controller.set_enforcing(True)
            self.refresh()

        dialog.connect("response", _done)
        parent = self._root.get_root()
        if isinstance(parent, Gtk.Window):
            dialog.present(parent)
        else:
            _done(dialog, "cancel")

    def _on_offer(self, _button: Gtk.Button | None = None) -> None:
        self._confirm_enforce()

    def _on_expect(self, _button: Gtk.Button, minutes: int) -> None:
        self.controller.arm_bypass(int(minutes))
        self.refresh()

    def _on_stop_expect(self, _button: Gtk.Button | None = None) -> None:
        self.controller.stop_bypass()
        self.refresh()

    # ---- windows ------------------------------------------------------------

    def _on_edit_window(self, _button: Gtk.Button, key: str) -> None:
        snap = self._snapshot
        current = snap.unknown if key == "unknown_window" else snap.business
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        time_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=8)
        start_h = Gtk.SpinButton.new_with_range(0, 23, 1)
        start_m = Gtk.SpinButton.new_with_range(0, 59, 1)
        end_h = Gtk.SpinButton.new_with_range(0, 23, 1)
        end_m = Gtk.SpinButton.new_with_range(0, 59, 1)
        start_h.set_value(current.start_minute // 60)
        start_m.set_value(current.start_minute % 60)
        end_h.set_value(current.end_minute // 60)
        end_m.set_value(current.end_minute % 60)
        for widget, text in ((start_h, "From h"), (start_m, "m"),
                             (end_h, "To h"), (end_m, "m")):
            widget.set_tooltip_text(text)
            time_row.append(widget)
        box.append(time_row)
        day_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=4)
        toggles: list[Gtk.ToggleButton] = []

        def _sync_chip(toggle: Gtk.ToggleButton) -> None:
            if toggle.get_active():
                toggle.add_css_class("selected")
            else:
                toggle.remove_css_class("selected")

        for i, name in enumerate(rules.DAY_LABELS):
            toggle = Gtk.ToggleButton(label=name)
            toggle.add_css_class("xx-chip")
            toggle.set_active(bool(current.days_mask & rules.day_bit(i)))
            _sync_chip(toggle)
            toggle.connect("toggled", _sync_chip)
            day_row.append(toggle)
            toggles.append(toggle)
        box.append(day_row)
        note = Gtk.Label(label="Start is inclusive, end is exclusive; "
                              "an end before the start wraps past midnight.",
                         xalign=0.0)
        note.add_css_class("xx-muted")
        note.set_wrap(True)
        box.append(note)

        dialog = Adw.AlertDialog.new("Edit window", "")
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance(
            "save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")

        def _done(source, response_id: str) -> None:
            if response_id != "save":
                return
            start = int(start_h.get_value()) * 60 + int(start_m.get_value())
            end = int(end_h.get_value()) * 60 + int(end_m.get_value())
            days = rules.mask_from_chips(
                [toggle.get_active() for toggle in toggles])
            if not days:
                self.controller._notice("Pick at least one day.")
                return
            try:
                self.controller.save_window(key, start, end, days)
            except ValueError:
                self.controller._notice("That window is not valid.")
                return
            self.refresh()

        dialog.connect("response", _done)
        parent = self._root.get_root()
        if isinstance(parent, Gtk.Window):
            dialog.present(parent)

    # ---- policies -------------------------------------------------------------

    def _on_repeat_flip(self, switch: Gtk.Switch, _spec) -> None:
        if self._suppress:
            return
        self.controller.set_repeat(switch.get_active())
        self.refresh()

    # ---- tones ------------------------------------------------------------------

    def _on_pick_tone(self, _button: Gtk.Button, key: str) -> None:
        picker = Gtk.FileDialog()
        picker.set_title("Choose a ringtone")

        def _done(source, result) -> None:
            try:
                picked = source.open_finish(result)
            except GLib.Error:
                return
            path = picked.get_path() if picked is not None else None
            if path:
                self.controller.set_tone(key, path)
                self.refresh()

        parent = self._root.get_root()
        picker.open(parent if isinstance(parent, Gtk.Window) else None,
                    None, _done)

    def _on_clear_tone(self, _button: Gtk.Button, key: str) -> None:
        self.controller.set_tone(key, "")
        self.refresh()

    # ---- patterns ---------------------------------------------------------------

    def _on_add_pattern(self, _button: Gtk.Button) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        entry = widgets.text_entry("Sample number, e.g. +1 425-555-0100")
        entry.set_can_focus(False)
        scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0, 6, 1)
        scale.set_value(4)
        picked = ["block"]

        def _pick_block() -> None:
            picked[0] = "block"
            self._select_seg(action, 0)

        def _pick_silence() -> None:
            picked[0] = "silence"
            self._select_seg(action, 1)

        action = widgets.segmented([
            ("Block", True, _pick_block),
            ("Silence", False, _pick_silence),
        ])
        preview = Gtk.Label(label="", xalign=0.0)
        preview.add_css_class("xx-secondary")
        preview.set_ellipsize(Pango.EllipsizeMode.END)

        def _paint(*_args) -> None:
            digits = masks.digits_of(entry.get_text())
            masked = max(1, int(scale.get_value()))
            draft = masks.build(digits, masked) if digits else None
            preview.set_text(masks.preview(draft) if draft else "")

        entry.connect("changed", _paint)
        scale.connect("value-changed", _paint)
        box.append(entry)
        box.append(scale)
        box.append(action)
        box.append(preview)

        dialog = Adw.AlertDialog.new("Add pattern", "")
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance(
            "save", Adw.ResponseAppearance.SUGGESTED)

        def _done(source, response_id: str) -> None:
            if response_id != "save":
                return
            digits = masks.digits_of(entry.get_text())
            masked = max(1, int(scale.get_value()))
            draft = masks.build(digits, masked) if digits else None
            if draft is None:
                self.controller._notice("Type a number first.")
                return
            try:
                self.controller.add_pattern(draft.prefix,
                                            draft.wildcards, picked[0])
            except ValueError:
                self.controller._notice("That pattern is not valid.")
                return
            self.refresh()

        dialog.connect("response", _done)
        parent = self._root.get_root()
        if isinstance(parent, Gtk.Window):
            dialog.present(parent)

    def _on_delete_pattern(self, _button: Gtk.Button, row_id: int) -> None:
        self.controller.delete_pattern(row_id)
        self.refresh()

    def _on_neighbor(self, _button: Gtk.Button) -> None:
        with Store(self.controller._deps.db_file) as store:
            armed = any(row.preset == rules.NEIGHBOR_PRESET
                        for row in store.all_patterns())
        if armed:
            self.controller.set_neighbor_preset(None)
            self.refresh()
            return
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        entry = widgets.text_entry("Your own number")
        entry.set_can_focus(False)
        box.append(entry)
        dialog = Adw.AlertDialog.new(
            "Neighbor-spoof preset",
            "Silences calls shaped like your own number prefix.")
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("arm", "Silence")
        dialog.set_response_appearance(
            "arm", Adw.ResponseAppearance.SUGGESTED)

        def _done(source, response_id: str) -> None:
            if response_id != "arm":
                return
            try:
                self.controller.set_neighbor_preset(entry.get_text())
            except ValueError:
                self.controller._notice(
                    "That number cannot derive a prefix.")
                return
            self.refresh()

        dialog.connect("response", _done)
        parent = self._root.get_root()
        if isinstance(parent, Gtk.Window):
            dialog.present(parent)

    def _on_import_blocklist(self, _button: Gtk.Button) -> None:
        picker = Gtk.FileDialog()
        picker.set_title("Import blocklist file")

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
            self.controller.import_blocklist_text(text)
            self.refresh()

        parent = self._root.get_root()
        picker.open(parent if isinstance(parent, Gtk.Window) else None,
                    None, _done)

    def _on_unblock(self, _button: Gtk.Button, number: str) -> None:
        self.controller.unblock(number)
        self.refresh()

    # ---- test + log ---------------------------------------------------------------

    def _on_test_entry_click(self, _gesture, _n: int, _x: float,
                             _y: float) -> None:
        self._test_entry.set_can_focus(True)
        self._test_entry.grab_focus()

    def _on_test_go(self, _source) -> None:
        raw = self._test_entry.get_text()
        if not raw.strip():
            self._test_verdict.set_text("Type a number to test.")
            self._test_reason.set_text("")
            return
        self._test_go.set_sensitive(False)
        self._test_verdict.set_text("Checking...")
        self._test_reason.set_text("")

        def _work() -> None:
            try:
                verdict, reason = self.controller.test_number(raw)
            except Exception as err:
                GLib.idle_add(self._finish_test, None, str(err))
                return
            GLib.idle_add(self._finish_test, (verdict, reason), "")

        threading.Thread(target=_work, daemon=True).start()

    def _finish_test(self, outcome, error: str) -> bool:
        try:
            self._test_go.set_sensitive(True)
        except Exception:
            return False
        if outcome is None:
            self._test_verdict.set_text("Could not check: %s" % error)
            self._test_reason.set_text("")
            return False
        verdict, reason = outcome
        self._test_verdict.set_text(
            {"ring": "Would ring", "silence": "Would stay silent",
             "block": "Would be blocked"}.get(verdict, verdict))
        self._test_reason.set_text(reason)
        return False

    def _on_clear_log(self, _button: Gtk.Button) -> None:
        self.controller.clear_log()
        self.refresh()

def build_rules(deps: RulesDeps) -> RulesView:
    return RulesView(RulesController(deps))
