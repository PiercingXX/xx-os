"""Blocking page: blocked numbers, content-filter terms, dialer import.

Ports ``ui/BlockingActivity`` (edits the four persisted rule sets and the
unknown-sender hold, applying immediately). Every mutation writes the
SQLite rule tables and rebuilds the process-wide live filter, so the
next inbound message is filtered by the new rules.

Contact blocks additionally tag the xx-contacts entry ``"blocked"``
through the atomic ``UpdateTags`` call (never read-modify-write);
numbers with no contact entry stay local-only. The dialer import pulls
the dialer's local block table when it is reachable and stays silent
otherwise.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from xx_ui import widgets  # noqa: E402
from txxt import blocking as blocking_mod  # noqa: E402
from txxt.conversations import tap_to_focus  # noqa: E402
from txxt.list_presenter import count_label  # noqa: E402

DIALER_BLOCK_ENV = 'TXXT_DIALER_BLOCKS'
DIALER_BLOCK_NAMES = ('blocked.json', 'blocklist.json')


def dialer_search_roots() -> list[Path]:
    """Where the dialer's local block table may live, in priority order."""
    roots: list[Path] = []
    override = os.environ.get(DIALER_BLOCK_ENV)
    if override:
        roots.append(Path(override).parent)
    try:
        lane_root = Path(__file__).resolve().parents[2]
    except Exception:
        lane_root = None
    if lane_root is not None:
        roots.append(lane_root / 'ref' / 'xx-dialer')
        roots.append(lane_root.parent / 'xx-dialer')
    data_home = os.environ.get('XDG_DATA_HOME') or str(Path.home() / '.local' / 'share')
    roots.append(Path(data_home) / 'xx-dialer')
    return roots


def find_dialer_block_file() -> Path | None:
    """First reachable dialer block-table file, or None when absent."""
    for root in dialer_search_roots():
        for name in DIALER_BLOCK_NAMES:
            try:
                candidate = root / name
            except Exception:
                continue
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
    override = os.environ.get(DIALER_BLOCK_ENV)
    if override:
        try:
            direct = Path(override)
            if direct.is_file():
                return direct
        except OSError:
            pass
    return None


def parse_dialer_blocks(raw: str) -> list[str]:
    """Numbers from a dialer block-table payload (list or mapped list)."""
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if isinstance(data, dict):
        for key in ('blocked', 'blocklist', 'numbers'):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            return []
    if not isinstance(data, list):
        return []
    numbers = []
    for entry in data:
        text = entry if isinstance(entry, str) else str(entry.get('number', ''))
        if text.strip() and text.strip() not in numbers:
            numbers.append(text.strip())
    return numbers


def import_dialer_blocks(store) -> tuple[int, int]:
    """Merge the reachable dialer block table into ``sender`` rules.

    Returns ``(imported, total)``; ``(0, 0)`` when no table is
    reachable. Idempotent: ``blocklist_add`` replaces per key.
    """
    found = find_dialer_block_file()
    if found is None:
        return 0, 0
    try:
        numbers = parse_dialer_blocks(found.read_text(encoding='utf-8'))
    except OSError:
        return 0, 0
    for number in numbers:
        try:
            store.blocklist_add('sender', number)
        except Exception:
            pass
    try:
        blocking_mod.live_apply_from_store(store)
    except Exception:
        pass
    return len(numbers), len(numbers)


class BlockingPage(Adw.NavigationPage):
    """Rule editor over the store's ``blocklist``/``starred`` tables."""

    def __init__(
        self, navigation: Adw.NavigationView, parent_window: Gtk.Window,
        store, resolver=None, on_changed=None,
    ) -> None:
        super().__init__()
        self._navigation = navigation
        self._window = parent_window
        self._store = store
        self._resolver = resolver
        self._on_changed = on_changed
        self.set_title('Blocking')

        header = widgets.compact_header(
            self._on_back, 'Blocking', 'Numbers, words, senders',
        )
        body = Gtk.Box.new(Gtk.Orientation.VERTICAL, 16)

        hold = widgets.setting_row(
            'Hold unknown senders',
            'Off delivers all but blocked',
            widgets.make_switch(False),
        )
        self._hold_switch = hold.get_last_child()
        self._hold_switch.connect('notify::active', self._on_quarantine_changed)
        hold_group = Gtk.Box.new(Gtk.Orientation.VERTICAL, 0)
        hold_group.append(widgets.section_label('Unknown senders'))
        hold_group.append(hold)
        body.append(hold_group)

        self._lists: dict[str, Gtk.ListBox] = {}
        self._inputs: dict[str, Gtk.Entry] = {}
        for kind, title, hint in (
            ('sender', 'Blocked numbers', 'Number to block'),
            ('keyword', 'Blocked keywords', 'Whole-word term'),
            ('phrase', 'Blocked phrases', 'Substring phrase'),
        ):
            group = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
            group.append(widgets.section_label(title))
            entry = widgets.text_entry(hint)
            tap_to_focus(entry)
            self._inputs[kind] = entry
            group.append(entry)
            add_button = widgets.outline_button('Add')
            add_button.connect('clicked', self._on_add_clicked, kind)
            group.append(add_button)
            rows = Gtk.ListBox()
            rows.set_selection_mode(Gtk.SelectionMode.NONE)
            self._lists[kind] = rows
            group.append(rows)
            body.append(group)

        dialer_group = Gtk.Box.new(Gtk.Orientation.VERTICAL, 8)
        dialer_group.append(widgets.section_label('Dialer'))
        self._import_button = widgets.outline_button('Import dialer block list')
        self._import_button.connect('clicked', self._on_import_clicked)
        dialer_group.append(self._import_button)
        self._import_label = widgets.label('', 'xx-secondary')
        dialer_group.append(self._import_label)
        body.append(dialer_group)

        self.set_child(widgets.page(header, body, None))
        self._ready = False
        self.refresh()

    # ------------------------------------------------------------ loading

    def set_resolver(self, resolver) -> None:
        self._resolver = resolver

    def _entry_label(self, kind: str, value: str) -> str:
        """Blocked-sender rows read through the display rule (saved name
        when known, else the formatted number); keywords and phrases are
        match text and stay verbatim."""
        if kind != 'sender':
            return value
        if self._resolver is not None:
            try:
                name = self._resolver.display_name(value)
                if (name or '').strip():
                    return name
            except Exception:
                pass
        try:
            from txxt import phone_numbers as _phones

            return _phones.format_display_configured(value) or value
        except Exception:
            return value

    def _on_back(self) -> None:
        try:
            self._navigation.pop()
        except Exception:
            pass

    def refresh(self) -> None:
        worker = threading.Thread(target=self._load_in_worker, daemon=True)
        worker.start()

    def _load_in_worker(self) -> None:
        try:
            senders = self._store.blocklist_list('sender')
            keywords = self._store.blocklist_list('keyword')
            phrases = self._store.blocklist_list('phrase')
            quarantine = bool(self._store.get_setting(
                blocking_mod.QUARANTINE_SETTING_KEY, False,
            ))
        except Exception:
            return
        GLib.idle_add(
            self._apply_loaded,
            {'sender': senders, 'keyword': keywords, 'phrase': phrases},
            quarantine,
        )

    def _apply_loaded(self, entries: dict, quarantine: bool) -> bool:
        self._ready = False
        try:
            self._hold_switch.set_active(quarantine)
        except Exception:
            pass
        for kind, pairs in entries.items():
            rows = self._lists[kind]
            while True:
                row = rows.get_row_at_index(0)
                if row is None:
                    break
                rows.remove(row)
            for _kind, value in sorted(pairs, key=lambda pair: pair[1].lower()):
                label = widgets.label(self._entry_label(kind, value),
                                      'xx-title')
                label.set_hexpand(True)
                # Large text: a number or phrase wraps, never "(614) 5…".
                label.set_wrap(True)
                label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                label.set_lines(2)
                label.set_ellipsize(Pango.EllipsizeMode.END)
                remove = widgets.text_button('Remove')
                remove.connect('clicked', self._on_remove_clicked, kind, value)
                box = Gtk.Box.new(Gtk.Orientation.HORIZONTAL, 8)
                box.add_css_class('xx-row')
                box.set_hexpand(True)
                box.set_valign(Gtk.Align.CENTER)
                box.append(label)
                box.append(remove)
                row = Gtk.ListBoxRow()
                row.set_child(box)
                row.set_activatable(False)
                rows.append(row)
        self._ready = True
        return False

    # ------------------------------------------------------------ mutations

    def _run_store(self, fn) -> None:
        worker = threading.Thread(target=self._store_in_worker, args=(fn,), daemon=True)
        worker.start()

    def _store_in_worker(self, fn) -> None:
        try:
            fn()
        except Exception:
            pass
        try:
            blocking_mod.live_apply_from_store(self._store)
        except Exception:
            pass
        GLib.idle_add(self._refresh_from_idle)

    def _fire_changed(self) -> bool:
        if self._on_changed is not None:
            try:
                self._on_changed()
            except Exception:
                pass
        return False

    def _refresh_from_idle(self) -> bool:
        self.refresh()
        self._fire_changed()
        return False

    def _on_add_clicked(self, _button, kind: str) -> None:
        value = self._inputs[kind].get_text().strip()
        if not value:
            return
        self._inputs[kind].set_text('')
        self._run_store(lambda: self._add_rule(kind, value))

    def _add_rule(self, kind: str, value: str) -> None:
        self._store.blocklist_add(kind, value)
        if kind == 'sender' and self._resolver is not None:
            try:
                self._resolver.set_blocked(value, True)
            except Exception:
                pass

    def _on_remove_clicked(self, _button, kind: str, value: str) -> None:
        self._run_store(lambda: self._remove_rule(kind, value))

    def _remove_rule(self, kind: str, value: str) -> None:
        try:
            self._store.blocklist_remove(kind, value)
        except Exception:
            pass
        if kind == 'sender' and self._resolver is not None:
            try:
                self._resolver.set_blocked(value, False)
            except Exception:
                pass

    def _on_quarantine_changed(self, row, _spec) -> None:
        if not getattr(self, '_ready', False):
            return
        active = bool(row.get_active())
        self._run_store(
            lambda: self._store.set_setting(
                blocking_mod.QUARANTINE_SETTING_KEY, active,
            ),
        )

    def _on_import_clicked(self, _button) -> None:
        self._run_store(self._import_in_worker)

    def _import_in_worker(self) -> None:
        imported, _total = import_dialer_blocks(self._store)
        GLib.idle_add(self._announce_import, imported)

    def _announce_import(self, imported: int) -> bool:
        self._import_label.set_text(
            f"Imported {count_label(imported, 'number')}." if imported
            else 'No dialer block list found.',
        )
        return False
